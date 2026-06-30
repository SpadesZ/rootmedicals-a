#!/usr/bin/env python3
# 檔案路徑: rootmedicals-a/llmxx-client/apps/clinicalguard-standalone/src/clinical_guard_app/local_core.py
# 產生時間: 2026-06-18 10:30 +08:00
# 版本: v0.2
# 模組定位:
#   ClinicalGuard 的本機資料層與早期 rule-based alert engine。GUI 會用它保存 encounter、
#   建立同步佇列、匯出 PDF，並在 EBM closed-loop 之外提供基本臨床安全提示。
# 主要責任:
#   1. 建立與維護 SQLite table：local_encounter、local_alert_event、local_sync_queue。
#   2. 將 SOAP/vitals 儲存成本機 encounter，支援 Save、Save + Sync、Export PDF。
#   3. 提供簡單 deterministic alert 規則，作為 HIS 端的本機安全提醒，不取代 RAG/EBM 燈號。
# 維護提醒:
#   - 這裡可以保存 demo encounter，但不要把它誤認成 llmxx-server 的 final gate 狀態資料庫。
#   - local_sync_queue 是模擬後送 HIS/API 的佇列；Ctrl+Alt+G 截圖送審不依賴這個 queue。
#   - 如果要接正式 HIS，請優先把資料交換契約抽到 server/API 層，不要讓 GUI 直接承擔正式整合。
# 驗證方式:
#   - py_compile。
#   - gui_app.py --self-test 會覆蓋建立 encounter、queue 與 PDF 匯出。
# ----------------------------------------------------------------------------------------------------
"""
ClinicalGuard 本機資料與規則核心。

一般由 gui_app.py 呼叫；維護時可先跑 GUI self-test 確認 SQLite/PDF 路徑正常。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import textwrap
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _detect_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        # 打包前後路徑深度可能不同，因此用 app/data 特徵找 ClinicalGuard app root。
        if (parent / "app").exists() and (parent / "data").exists():
            return parent
    return p.parents[5]


ROOT = _detect_repo_root()
DEFAULT_DB = ROOT / "data" / "local_guard.db"
DEFAULT_EXPORT_DIR = ROOT / "data" / "local_exports"


@dataclass
class AlertRow:
    alert_code: str
    severity: str
    title: str
    message: str
    suggestion: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "alert_code": self.alert_code,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "suggestion": self.suggestion,
        }


class ClinicalGuardLocal:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        # SQLite schema 保持小而明確：encounter 是輸入快照，alert_event 是本機提示，sync_queue 是後送佇列。
        # EBM/RAG 的來源、分數、燈號不寫在這裡，避免兩套狀態互相覆蓋。
        ddl = """
        CREATE TABLE IF NOT EXISTS local_encounter (
            encounter_uuid TEXT PRIMARY KEY,
            pid TEXT NOT NULL,
            patient_uid TEXT NOT NULL,
            patient_label TEXT,
            visit_time TEXT NOT NULL,
            soap_s TEXT,
            soap_o TEXT,
            soap_a TEXT,
            soap_p TEXT,
            vs_bp TEXT,
            vs_hr TEXT,
            vs_temp TEXT,
            vs_rr TEXT,
            vs_spo2 TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_alert_event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            encounter_uuid TEXT NOT NULL,
            alert_code TEXT,
            severity TEXT,
            title TEXT,
            message TEXT,
            suggestion TEXT,
            physician_action TEXT,
            ignore_reason TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            encounter_uuid TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            sync_state TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TEXT NOT NULL
        );
        """
        with self._connect() as conn:
            conn.executescript(ddl)
            conn.commit()

    @staticmethod
    def _iso_now() -> str:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _to_number(value: Any) -> Optional[float]:
        text = str(value or "").strip().lower().replace("%", "")
        if not text:
            return None
        buff = []
        for ch in text:
            if ch.isdigit() or ch in (".", "-"):
                buff.append(ch)
            elif buff:
                break
        if not buff:
            return None
        try:
            return float("".join(buff))
        except Exception:
            # 生命徵象解析失敗時不丟例外，讓 UI 能繼續保存 encounter；缺資料會自然少觸發本機 rule。
            return None

    @staticmethod
    def _contains_any(text: str, words: List[str]) -> bool:
        t = text.lower()
        return any(w in t for w in words)

    def evaluate_alerts(self, soap: Dict[str, str], vital_signs: Dict[str, str]) -> List[AlertRow]:
        alerts: List[AlertRow] = []
        s = str(soap.get("S") or "")
        o = str(soap.get("O") or "")
        a = str(soap.get("A") or "")
        p = str(soap.get("P") or "")
        text_all = f"{s}\n{o}\n{a}\n{p}".lower()

        temp = self._to_number(vital_signs.get("temp"))
        rr = self._to_number(vital_signs.get("rr"))
        spo2 = self._to_number(vital_signs.get("spo2"))
        hr = self._to_number(vital_signs.get("hr"))

        if spo2 is not None and spo2 < 92:
            # 本機 rule 只抓明顯紅旗，目的是提醒醫師，不做 evidence-backed 結論。
            alerts.append(
                AlertRow(
                    alert_code="resp_hypoxemia",
                    severity="critical",
                    title="低血氧風險",
                    message=f"SpO2={spo2:.0f}% 低於 92%，需優先排除急性呼吸衰竭或肺部病灶。",
                    suggestion="建議立即評估氧療需求、ABG、影像與住院指標。",
                )
            )

        if rr is not None and rr >= 24:
            alerts.append(
                AlertRow(
                    alert_code="resp_tachypnea",
                    severity="high",
                    title="呼吸急促警示",
                    message=f"RR={rr:.0f}，已達呼吸急促區間。",
                    suggestion="建議補充呼吸音、氧合與感染來源評估。",
                )
            )

        if temp is not None and temp >= 39 and self._contains_any(text_all, ["cough", "咳", "痰", "pneumonia", "肺炎"]):
            alerts.append(
                AlertRow(
                    alert_code="inf_pneumonia_possible",
                    severity="high",
                    title="高燒合併呼吸道症狀",
                    message=f"Temp={temp:.1f} 且主訴含呼吸道症狀，需注意肺炎/敗血症可能。",
                    suggestion="建議補上感染源線索、qSOFA 相關要素、必要時安排影像與檢驗。",
                )
            )

        if self._contains_any(text_all, ["chest pain", "胸痛"]) and self._contains_any(
            text_all, ["dyspnea", "喘", "呼吸困難", "shortness of breath"]
        ):
            alerts.append(
                AlertRow(
                    alert_code="cv_chestpain_dyspnea",
                    severity="critical",
                    title="胸痛合併呼吸困難",
                    message="症狀組合可能涉及 ACS/PE 等高風險診斷。",
                    suggestion="建議優先完成危急鑑別流程與監測。",
                )
            )

        if self._contains_any(text_all, ["confusion", "意識改變", "嗜睡"]) and temp is not None and temp >= 38:
            alerts.append(
                AlertRow(
                    alert_code="neuro_infection_redflag",
                    severity="critical",
                    title="發燒合併神經學紅旗",
                    message="需排除中樞感染、敗血症相關腦病變等急重症。",
                    suggestion="建議立即進行神經學評估與必要影像/檢驗。",
                )
            )

        if self._contains_any(text_all, ["weakness", "無力", "偏癱", "麻"]) and self._contains_any(text_all, ["sudden", "突然"]):
            alerts.append(
                AlertRow(
                    alert_code="stroke_redflag",
                    severity="critical",
                    title="疑似中風紅旗",
                    message="出現急性神經缺損描述，請優先排除腦中風。",
                    suggestion="建議啟動急性中風評估流程。",
                )
            )

        if not str(soap.get("A") or "").strip():
            alerts.append(
                AlertRow(
                    alert_code="doc_missing_assessment",
                    severity="medium",
                    title="SOAP A 欄位不足",
                    message="Assessment 尚未填寫，可能影響臨床推理可追溯性。",
                    suggestion="建議至少補上主要問題與鑑別方向。",
                )
            )

        if hr is not None and hr >= 120 and temp is not None and temp >= 38.5:
            alerts.append(
                AlertRow(
                    alert_code="sepsis_screen",
                    severity="high",
                    title="感染性休克風險提示",
                    message=f"HR={hr:.0f} 且 Temp={temp:.1f}，需提高敗血症警覺。",
                    suggestion="建議快速評估感染來源、器官灌流與乳酸。",
                )
            )

        dedup: Dict[str, AlertRow] = {}
        for row in alerts:
            # 同一 alert_code 只保留一次，避免 S/O/A/P 多處提到同一風險時 UI 反覆跳同樣訊息。
            dedup[row.alert_code] = row
        return list(dedup.values())

    def create_encounter(
        self,
        pid: str,
        patient_uid: str,
        patient_label: str,
        visit_time: str,
        soap: Dict[str, str],
        vital_signs: Dict[str, str],
    ) -> Tuple[str, List[AlertRow]]:
        encounter_uuid = f"enc-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        now = self._iso_now()
        alerts = self.evaluate_alerts(soap, vital_signs)

        with self._connect() as conn:
            # 這裡保存的是醫師端輸入當下的本機快照，方便 demo 後查。正式病歷持久化仍應交給 HIS。
            conn.execute(
                """
                INSERT INTO local_encounter (
                    encounter_uuid, pid, patient_uid, patient_label, visit_time,
                    soap_s, soap_o, soap_a, soap_p,
                    vs_bp, vs_hr, vs_temp, vs_rr, vs_spo2,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'final', ?, ?)
                """,
                (
                    encounter_uuid,
                    pid,
                    patient_uid,
                    patient_label,
                    visit_time,
                    str(soap.get("S") or ""),
                    str(soap.get("O") or ""),
                    str(soap.get("A") or ""),
                    str(soap.get("P") or ""),
                    str(vital_signs.get("bp") or ""),
                    str(vital_signs.get("hr") or ""),
                    str(vital_signs.get("temp") or ""),
                    str(vital_signs.get("rr") or ""),
                    str(vital_signs.get("spo2") or ""),
                    now,
                    now,
                ),
            )
            for row in alerts:
                conn.execute(
                    """
                    INSERT INTO local_alert_event (
                        encounter_uuid, alert_code, severity, title, message, suggestion,
                        physician_action, ignore_reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ack', '', ?)
                    """,
                    (
                        encounter_uuid,
                        row.alert_code,
                        row.severity,
                        row.title,
                        row.message,
                        row.suggestion,
                        now,
                    ),
                )
            conn.commit()
        return encounter_uuid, alerts

    def get_encounter_detail(self, encounter_uuid: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            enc = conn.execute(
                """
                SELECT *
                FROM local_encounter
                WHERE encounter_uuid = ?
                """,
                (encounter_uuid,),
            ).fetchone()
            if enc is None:
                return None
            alerts = conn.execute(
                """
                SELECT *
                FROM local_alert_event
                WHERE encounter_uuid = ?
                ORDER BY id ASC
                """,
                (encounter_uuid,),
            ).fetchall()

        return {
            "encounter_uuid": enc["encounter_uuid"],
            "pid": enc["pid"],
            "patient_uid": enc["patient_uid"],
            "patient_label": enc["patient_label"] or "",
            "visit_time": enc["visit_time"] or "",
            "soap": {
                "S": enc["soap_s"] or "",
                "O": enc["soap_o"] or "",
                "A": enc["soap_a"] or "",
                "P": enc["soap_p"] or "",
            },
            "vital_signs": {
                "bp": enc["vs_bp"] or "",
                "hr": enc["vs_hr"] or "",
                "temp": enc["vs_temp"] or "",
                "rr": enc["vs_rr"] or "",
                "spo2": enc["vs_spo2"] or "",
            },
            "created_at": enc["created_at"] or "",
            "updated_at": enc["updated_at"] or "",
            "alerts": [dict(row) for row in alerts],
        }

    def latest_encounter_uuid(self) -> str:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT encounter_uuid
                FROM local_encounter
                ORDER BY created_at DESC, encounter_uuid DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return ""
            return str(row["encounter_uuid"] or "")

    def build_packet(self, encounter_uuid: str) -> Dict[str, Any]:
        detail = self.get_encounter_detail(encounter_uuid)
        if detail is None:
            raise ValueError("encounter not found")

        return {
            "pid": detail["pid"],
            "encounter_uuid": detail["encounter_uuid"],
            "patient_uid": detail["patient_uid"],
            "patient_label": detail["patient_label"],
            "visit_time": detail["visit_time"],
            "source": "phase1",
            "soap": detail["soap"],
            "vital_signs": detail["vital_signs"],
            "alerts": [
                {
                    "alert_code": str(row.get("alert_code") or ""),
                    "severity": str(row.get("severity") or ""),
                    "title": str(row.get("title") or ""),
                    "physician_action": str(row.get("physician_action") or "ack"),
                    "ignore_reason": str(row.get("ignore_reason") or ""),
                }
                for row in (detail.get("alerts") or [])
            ],
        }

    def enqueue_packet(self, encounter_uuid: str, payload: Dict[str, Any]) -> None:
        now = self._iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO local_sync_queue (encounter_uuid, payload_json, sync_state, retry_count, last_error, updated_at)
                VALUES (?, ?, 'pending', 0, '', ?)
                ON CONFLICT(encounter_uuid) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    sync_state = 'pending',
                    updated_at = excluded.updated_at
                """,
                (encounter_uuid, json.dumps(payload, ensure_ascii=False), now),
            )
            conn.commit()

    @staticmethod
    def _post_json(url: str, payload: Dict[str, Any], timeout: float = 10.0) -> Tuple[int, Dict[str, Any]]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url=url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                parsed = json.loads(body) if body else {}
                return int(getattr(resp, "status", 200)), parsed
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            parsed = {}
            try:
                parsed = json.loads(body) if body else {}
            except Exception:
                parsed = {"status": "error", "message": body or str(exc)}
            return int(exc.code), parsed
        except URLError as exc:
            return 0, {"status": "error", "message": str(exc)}

    def sync_pending(self, sync_url: str) -> Dict[str, Any]:
        sent = 0
        failed = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, encounter_uuid, payload_json, retry_count
                FROM local_sync_queue
                WHERE sync_state IN ('pending', 'failed')
                ORDER BY id ASC
                """
            ).fetchall()

            for row in rows:
                queue_id = int(row["id"])
                payload = json.loads(row["payload_json"] or "{}")
                code, result = self._post_json(sync_url, payload, timeout=12.0)
                ok = code == 200 and str(result.get("status") or "").lower() in {"success", "duplicate"}
                now = self._iso_now()
                if ok:
                    conn.execute(
                        """
                        UPDATE local_sync_queue
                        SET sync_state = 'sent', retry_count = ?, last_error = '', updated_at = ?
                        WHERE id = ?
                        """,
                        (int(row["retry_count"]) + 1, now, queue_id),
                    )
                    sent += 1
                else:
                    conn.execute(
                        """
                        UPDATE local_sync_queue
                        SET sync_state = 'failed', retry_count = ?, last_error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            int(row["retry_count"]) + 1,
                            str(result.get("message") or f"HTTP {code}"),
                            now,
                            queue_id,
                        ),
                    )
                    failed += 1
            conn.commit()
        return {"sent": sent, "failed": failed, "total": len(rows)}

    @staticmethod
    def _sanitize_for_filename(text: str) -> str:
        value = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in str(text or ""))
        while "--" in value:
            value = value.replace("--", "-")
        return value.strip("-_.")[:80] or "encounter"

    def export_encounter_pdf(self, encounter_uuid: str, output_path: Optional[Path] = None) -> Dict[str, Any]:
        detail = self.get_encounter_detail(encounter_uuid)
        if detail is None:
            return {"status": "error", "message": "Encounter not found"}

        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            from reportlab.pdfgen import canvas
        except Exception as exc:
            return {"status": "error", "message": f"PDF dependency missing: {exc}"}

        if output_path is None:
            DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            safe_uid = self._sanitize_for_filename(detail["patient_uid"])
            safe_enc = self._sanitize_for_filename(encounter_uuid)
            output_path = DEFAULT_EXPORT_DIR / f"visit_summary_{safe_uid}_{safe_enc}_{ts}.pdf"
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        font_name = "Helvetica"
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            font_name = "STSong-Light"
        except Exception:
            font_name = "Helvetica"

        c = canvas.Canvas(str(output_path), pagesize=A4)
        width, height = A4
        margin_x = 46
        top_y = height - 42
        line_h = 16
        state = {"y": top_y}

        def new_page() -> None:
            c.showPage()
            c.setFont(font_name, 11)
            state["y"] = top_y

        def draw_line(text: str, font_size: int = 11) -> None:
            if state["y"] <= 50:
                new_page()
            c.setFont(font_name, font_size)
            c.drawString(margin_x, state["y"], str(text))
            state["y"] -= line_h

        def draw_wrapped(text: str, width_chars: int = 95, font_size: int = 11) -> None:
            lines = str(text or "").splitlines() or [""]
            for line in lines:
                for part in (textwrap.wrap(line, width=width_chars) or [""]):
                    draw_line(part, font_size=font_size)

        draw_line("RootMedicals Clinical Guard - Visit Summary", font_size=15)
        draw_line(f"Generated At (UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
        draw_line("-" * 90)
        draw_line(f"Encounter UUID: {detail['encounter_uuid']}")
        draw_line(f"PID: {detail['pid']}")
        draw_line(f"Patient UID: {detail['patient_uid']}")
        draw_line(f"Patient Label: {detail['patient_label']}")
        draw_line(f"Visit Time: {detail['visit_time']}")
        draw_line("")

        vs = detail.get("vital_signs") or {}
        draw_line("Vital Signs", font_size=13)
        draw_line(
            f"BP={vs.get('bp', '')} | HR={vs.get('hr', '')} | Temp={vs.get('temp', '')} | RR={vs.get('rr', '')} | SpO2={vs.get('spo2', '')}"
        )
        draw_line("")

        soap = detail.get("soap") or {}
        draw_line("SOAP", font_size=13)
        for key in ["S", "O", "A", "P"]:
            draw_line(f"{key}:", font_size=12)
            draw_wrapped(str(soap.get(key) or ""))
            draw_line("")

        alerts = detail.get("alerts") or []
        draw_line("Clinical Guard Alerts", font_size=13)
        if not alerts:
            draw_line("No alerts recorded.")
        else:
            for idx, row in enumerate(alerts, start=1):
                draw_line(
                    f"{idx}. [{str(row.get('severity') or '').upper()}] {row.get('title') or ''} ({row.get('alert_code') or ''})"
                )
                draw_wrapped(f"Message: {row.get('message') or ''}")
                draw_wrapped(f"Suggestion: {row.get('suggestion') or ''}")
                draw_wrapped(f"Physician Action: {row.get('physician_action') or 'ack'} | Ignore Reason: {row.get('ignore_reason') or '-'}")
                draw_line("")

        draw_line("-" * 90)
        draw_line("Disclaimer: This summary supports clinical documentation and reminder workflow only.")
        draw_line("Final diagnosis and treatment decisions require physician judgment.")

        c.save()
        return {
            "status": "success",
            "path": str(output_path),
            "encounter_uuid": detail["encounter_uuid"],
            "alerts_count": len(alerts),
        }

    def count_rows(self, table_name: str) -> int:
        with self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()
            return int(row["c"] if row else 0)

    def list_recent_pids(self, limit: int = 30) -> List[str]:
        cap = int(limit) if int(limit) > 0 else 30
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT pid, MAX(created_at) AS last_seen
                FROM local_encounter
                WHERE TRIM(COALESCE(pid, '')) <> ''
                GROUP BY pid
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (cap,),
            ).fetchall()
        return [str(r["pid"]).strip() for r in rows if str(r["pid"] or "").strip()]


def _try_popup(alerts: List[AlertRow]) -> None:
    if not alerts:
        return
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        lines = [f"{a.severity.upper()} | {a.title}" for a in alerts[:3]]
        messagebox.showwarning("Clinical Guard Warning", "\n".join(lines))
        root.destroy()
    except Exception as exc:
        print(f"[ClinicalGuard] popup skipped: {exc}")


def run_quick_check(args: argparse.Namespace) -> int:
    guard = ClinicalGuardLocal(Path(args.db))
    visit_time = str(args.visit_time or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    soap = {
        "S": str(args.s or ""),
        "O": str(args.o or ""),
        "A": str(args.a or ""),
        "P": str(args.p or ""),
    }
    vitals = {
        "bp": str(args.bp or ""),
        "hr": str(args.hr or ""),
        "temp": str(args.temp or ""),
        "rr": str(args.rr or ""),
        "spo2": str(args.spo2 or ""),
    }

    encounter_uuid, alerts = guard.create_encounter(
        pid=str(args.pid),
        patient_uid=str(args.patient_uid),
        patient_label=str(args.patient_label or ""),
        visit_time=visit_time,
        soap=soap,
        vital_signs=vitals,
    )
    packet = guard.build_packet(encounter_uuid)
    guard.enqueue_packet(encounter_uuid, packet)

    if args.popup:
        _try_popup(alerts)

    print(f"encounter_uuid={encounter_uuid}")
    print(f"alerts={len(alerts)}")
    for row in alerts:
        print(f"- [{row.severity}] {row.alert_code} | {row.title}")

    if args.sync_url:
        result = guard.sync_pending(args.sync_url)
        print(f"sync_result={result}")
    else:
        print("sync_result=skipped (no --sync-url)")
    return 0


def run_sync_pending(args: argparse.Namespace) -> int:
    guard = ClinicalGuardLocal(Path(args.db))
    result = guard.sync_pending(args.sync_url)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("failed", 0) == 0 else 2


def run_export_pdf(args: argparse.Namespace) -> int:
    guard = ClinicalGuardLocal(Path(args.db))
    encounter_uuid = str(args.encounter_uuid or "").strip()
    if not encounter_uuid:
        encounter_uuid = guard.latest_encounter_uuid()
    if not encounter_uuid:
        print("ERROR: no encounter found to export")
        return 2

    out = Path(args.out) if args.out else None
    result = guard.export_encounter_pdf(encounter_uuid, output_path=out)
    if result.get("status") != "success":
        print(json.dumps(result, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


def run_self_test(args: argparse.Namespace) -> int:
    test_db = Path(args.db)
    if test_db.exists():
        test_db.unlink()

    guard = ClinicalGuardLocal(test_db)
    encounter_uuid, alerts = guard.create_encounter(
        pid="demo-p",
        patient_uid="pt-demo-01",
        patient_label="Demo",
        visit_time="2026-04-04T09:00:00Z",
        soap={
            "S": "Fever with cough and shortness of breath.",
            "O": "Temp 39.3C, crackles over right lower lobe.",
            "A": "",
            "P": "Pending",
        },
        vital_signs={"bp": "126/82", "hr": "124", "temp": "39.3", "rr": "26", "spo2": "90"},
    )
    assert encounter_uuid.startswith("enc-"), "encounter_uuid format failed"
    assert len(alerts) >= 3, "alert count too low"

    packet = guard.build_packet(encounter_uuid)
    assert packet["patient_uid"] == "pt-demo-01", "packet patient_uid mismatch"
    guard.enqueue_packet(encounter_uuid, packet)

    pdf_res = guard.export_encounter_pdf(encounter_uuid)
    assert pdf_res.get("status") == "success", f"pdf export failed: {pdf_res}"
    pdf_path = Path(str(pdf_res.get("path") or ""))
    assert pdf_path.exists(), "pdf not generated"
    assert pdf_path.stat().st_size > 0, "pdf is empty"

    assert guard.count_rows("local_encounter") == 1, "local_encounter row mismatch"
    assert guard.count_rows("local_alert_event") >= 3, "local_alert_event row mismatch"
    assert guard.count_rows("local_sync_queue") == 1, "local_sync_queue row mismatch"

    print("SELF_TEST_OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RootMedicals Phase1 Clinical Guard (Local Core)")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Local sqlite db path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("quick-check", help="Create one encounter, evaluate alerts, enqueue packet, optional sync")
    q.add_argument("--pid", required=True)
    q.add_argument("--patient-uid", required=True)
    q.add_argument("--patient-label", default="")
    q.add_argument("--visit-time", default="")
    q.add_argument("--s", default="")
    q.add_argument("--o", default="")
    q.add_argument("--a", default="")
    q.add_argument("--p", default="")
    q.add_argument("--bp", default="")
    q.add_argument("--hr", default="")
    q.add_argument("--temp", default="")
    q.add_argument("--rr", default="")
    q.add_argument("--spo2", default="")
    q.add_argument("--sync-url", default="")
    q.add_argument("--popup", action="store_true")
    q.set_defaults(func=run_quick_check)

    s = sub.add_parser("sync-pending", help="Retry sending pending/failed packets")
    s.add_argument("--sync-url", required=True)
    s.set_defaults(func=run_sync_pending)

    e = sub.add_parser("export-pdf", help="Export encounter summary PDF")
    e.add_argument("--encounter-uuid", default="")
    e.add_argument("--out", default="")
    e.set_defaults(func=run_export_pdf)

    t = sub.add_parser("self-test", help="Run local self test")
    t.set_defaults(func=run_self_test)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
