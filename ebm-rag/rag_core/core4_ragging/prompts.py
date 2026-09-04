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

_SYSTEM = """You are a clinical EBM (Evidence-Based Medicine) assistant.
Rules you MUST follow:
1. Base clinical evidence claims ONLY on the provided Evidence Chunks. Do not use external knowledge.
2. Local Calculator Results may be used only as deterministic local calculations, not as literature evidence.
3. Every clinical recommendation sentence MUST end with its evidence level and source, e.g. (Level_1 evidence, PMID:123).
4. Do NOT fabricate PMID, DOI, or journal names. Use only what is provided.
5. If direct evidence is lacking, write: "lacking direct evidence".
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
        {"chunk_id": "chunk-id", "pmid": null, "doi": null, "six_s_level": "string", "ocebm_level": "string", "score": 0.0}
      ]
    }
  ],
  "alternatives": [],
  "warnings": []
}"""


def build_ebm_prompt(dx_summary: str, case_context: dict, chunks: list) -> list[dict]:
    safe_context = dict(case_context or {})
    calculator_results = safe_context.get("calculator_results", [])

    chunks_text = ""
    for i, chunk in enumerate(chunks[:10]):
        chunks_text += (
            f"\n--- Chunk {i + 1} [{chunk.get('six_s_level', 'unknown')} | {chunk.get('ocebm_level', 'unknown')} | score:{chunk.get('score', 0):.3f}] ---\n"
            f"Chunk ID: {chunk.get('chunk_id', '')}\n"
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
        f"Evidence Chunks:\n{chunks_text}\n\n"
        "Generate the EBM assessment JSON now."
    )
    return [{"role": "user", "content": f"[SYSTEM]\n{_SYSTEM}\n\n{user_msg}"}]
