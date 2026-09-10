# 檔案路徑: rootmedicals-a/ebm-rag/rag_core/core4_ragging/prompts.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: RAG Core4 查詢/驗證層，負責 retrieval、EBM 生成、ICD gate 與安全燈號。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# File Path: ebm-rag/rag_core/core4_ragging/prompts.py
# Timestamp: 2026-06-09
# Version: v0.4
# Description: Core4 EBM Prompt 建構器。
#              強制只根據 chunks 作答；calculator_results 只作為本地計算值，不可當作文獻證據。
#              回傳必須為純 JSON，不可編造 PMID/DOI/期刊。
# Change Notes:
#              - v0.5 (2026-09-10): 規則 5 改成要求逐字輸出 marker，並新增 8b 輸出語言規則。
#                規則 5 原本只說「write: "lacking direct evidence"」，模型會改寫語序成
#                "Direct evidence is lacking regarding..."，而 llmebm/app/medpilot.py 的
#                信任閘是照字面比對，於是缺證據的回答被當成正常臨床答案顯示出來。
#                marker 一律維持英文，讓中文回答時該閘仍然對得上。
#              - v0.4 (2026-09-04): 補上 light_color 判準與 llmaaj_score 級距。
#                v0.3 以前只給 schema 而沒有任何判斷標準，模型缺乏依據，同一份
#                臨床輸入重複呼叫會在 green/yellow/orange 之間擺盪（實測完整
#                case_context 五次得到 orange×4、green×1，另有一次 yellow）。
#                判準沿用程式碼既有定義：OCEBM Level_1/Level_2 與 Six-S
#                System/Summaries/Syntheses（traffic_light._GREEN_*），分數錨點
#                沿用 pipeline 的 green90/yellow60/orange30。
#                另註明 schema 中的 "llmaaj_score": 0 只是欄位佔位，不是預設值
#                —— 舊版模型會被它錨定，實測回傳 0~5。
# ----------------------------------------------------------------------------------------------------

import json

# How many retrieved chunks are shown to the model. Also bounds the valid ref
# labels (C1..C{MAX_PROMPT_CHUNKS}); pipeline._resolve_source_refs must use the
# same window when mapping refs back to real chunk ids.
MAX_PROMPT_CHUNKS = 10

_SYSTEM = """You are a clinical EBM (Evidence-Based Medicine) assistant.
Rules you MUST follow:
1. Base clinical evidence claims ONLY on the provided Evidence Chunks. Do not use external knowledge.
2. Local Calculator Results may be used only as deterministic local calculations, not as literature evidence.
3. Every clinical recommendation sentence MUST end with its evidence level and source, e.g. (Level_1 evidence, PMID:123).
4. Do NOT fabricate PMID, DOI, or journal names. Use only what is provided.
4b. In "sources", "chunk_id" MUST be one of the Ref labels shown with the
   Evidence Chunks below (C1, C2, ...), copied exactly. Only the refs actually
   listed exist; never cite a label that was not shown to you, and never guess
   a neighbouring number. A source you cannot label with a shown ref must be
   left out entirely.
5. If direct evidence is lacking, the affected "comment" MUST contain the marker
   `lacking direct evidence` copied verbatim, in English, in that exact word
   order. Do NOT paraphrase it into "direct evidence is lacking" or any other
   wording: a downstream trust gate matches this marker and a paraphrase lets an
   answer through that says it has no evidence. Keep the marker in English even
   when the rest of your answer is written in another language.
6. Return ONLY valid JSON. No markdown, no code fences.
7. light_color decision rule. Judge ONLY the evidence-to-plan relationship.
   Apply exactly one, in this order:
   - orange: the Evidence Chunks contradict the stated plan, or the case
     context states a contraindication that applies to THIS patient
     (a negated mention such as "no active bleeding" is NOT a contraindication).
   - yellow: no citable supporting source exists, the supporting evidence is
     only indirect, or the plan cannot be assessed from the given evidence.
   - green: the Evidence Chunks directly support the stated plan AND at least
     one supporting chunk is OCEBM Level_1/Level_2 or Six-S
     System/Summaries/Syntheses.
   Do not downgrade merely because the literature discusses risks in general.
   Do not withhold green only because optional demographics are missing.
   Downstream deterministic gates already enforce ICD-anchor and
   contraindication safety, so do not double-apply them here.
8. llmaaj_score is an integer 0-100 expressing confidence in the light_color.
   Anchors: green ~90, yellow ~60, orange ~30. The 0 shown in the schema below
   is a placeholder for the field, NOT a default or a suggested value.
8b. Output language. Write the human-readable fields — "short_comment" and every
   rag_comments[].topic and rag_comments[].comment — in the SAME language as the
   Clinical Query dx_summary below. If dx_summary is written in Traditional
   Chinese, answer in Traditional Chinese; if it is English, answer in English.
   This applies to the prose only: the enum fields ("light_color",
   "evidence_level", "grade"), the ref labels (C1, C2, ...), source identifiers,
   and the rule 5 marker stay in English regardless of the answer language.
9. Output schema:
{
  "light_color": "green|yellow|orange",
  "llmaaj_score": 0,
  "short_comment": "string",
  "rag_comments": [
    {
      "topic": "string",
      "comment": "string",
      "evidence_level": "Level_1|Level_2|Level_3|Level_4|Level_5|unknown",
      "grade": "Grade_A|Grade_B|Grade_C|unknown",
      "sources": [
        {"chunk_id": "C1", "pmid": null, "doi": null, "six_s_level": "string", "ocebm_level": "string", "score": 0.0}
      ]
    }
  ],
  "alternatives": [],
  "warnings": []
}"""


def _available_refs_line(chunks: list) -> str:
    shown = len(chunks[:MAX_PROMPT_CHUNKS])
    if not shown:
        return "Available refs: none. No evidence chunks were retrieved."
    labels = ", ".join("C%d" % (i + 1) for i in range(shown))
    return (
        f"Available refs (the ONLY valid values for sources[].chunk_id): {labels}. "
        f"There are exactly {shown}; any other label is invalid."
    )


def build_ebm_prompt(dx_summary: str, case_context: dict, chunks: list) -> list[dict]:
    safe_context = dict(case_context or {})
    calculator_results = safe_context.get("calculator_results", [])

    # Real chunk ids end in a zero-padded running number, so showing them invites
    # the model to cite a neighbour it was never given (see _resolve_source_refs).
    # Local refs carry no such sequence and are mapped back before validation.
    chunks_text = ""
    for i, chunk in enumerate(chunks[:MAX_PROMPT_CHUNKS]):
        chunks_text += (
            f"\n--- Chunk {i + 1} [{chunk.get('six_s_level', 'unknown')} | {chunk.get('ocebm_level', 'unknown')} | score:{chunk.get('score', 0):.3f}] ---\n"
            f"Ref: C{i + 1}\n"
            f"PMID: {chunk.get('pmid', 'null')}  DOI: {chunk.get('doi', 'null')}\n"
            f"{chunk.get('text', '')[:1200]}\n"
        )

    calculator_text = json.dumps(calculator_results, ensure_ascii=False, indent=2)
    context_without_calculators = dict(safe_context)
    context_without_calculators.pop("calculator_results", None)

    user_msg = (
        f"Clinical Query:\ndx_summary: {dx_summary}\n"
        f"case_context: {json.dumps(context_without_calculators, ensure_ascii=False)}\n\n"
        f"Local Calculator Results (deterministic local calculations, NOT literature evidence):\n{calculator_text}\n\n"
        f"Evidence Chunks:\n{chunks_text}\n"
        f"{_available_refs_line(chunks)}\n\n"
        "Generate the EBM assessment JSON now."
    )
    return [{"role": "user", "content": f"[SYSTEM]\n{_SYSTEM}\n\n{user_msg}"}]
