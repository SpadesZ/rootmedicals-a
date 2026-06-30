#!/usr/bin/env python3
# 檔案路徑: rootmedicals-a/llmxx-client/apps/clinicalguard-standalone/src/clinical_guard_app/gui_app.py
# 產生時間: 2026-06-25 16:38 +08:00
# 版本: v0.9-HIS demo 情境快速填表
# 模組定位:
#   ClinicalGuard 是本機閉環驗證用的醫師端 HIS 視窗。它的任務是提供穩定、可重現的 SOAP、
#   vital signs 與 ICD-10 輸入畫面，讓 thin capture client 可以用 Ctrl+Alt+G 擷取畫面後送往
#   llmxx-server。這不是正式 HIS，也不直接做 EBM 判斷；真正的 OCR、RAG、ICD gate 與燈號
#   都在 server 端完成。
# 主要責任:
#   1. 呈現醫師實測會操作的欄位：Patient UID、ICD-10 autocomplete、SOAP、生命徵象。
#   2. 將 ICD code、診斷標籤、normalized diagnosis 與目前 SOAP/vitals 寫成本機 sidecar，
#      讓 server 在 OCR 錯讀時仍可使用 mock HIS 的結構化欄位。
#   3. 保留本機儲存、同步、PDF 匯出與 debug log，方便測試閉環時回查輸入資料。
#   4. 提供接近台灣門診 HIS 的展示版面；畫面資料皆為匿名 demo 欄位，不放真實病患姓名或 ID。
#   5. 透過診斷清單、醫令/處置表格與 EBM 狀態條，讓現場 demo 更像醫師日常工作站。
#   6. 提供三個小型 demo 快速填表按鈕，讓展示者不用現場複製貼上綠/黃/橘燈案例。
# 維護提醒:
#   - 若日後接正式 HIS，請把這個檔案當作測試替身，不要把正式病患資料處理邏輯塞進 GUI。
#   - ICD-10 選取結果是燈號判斷的重要 anchor；改 UI 欄位位置時，請同步檢查 thin capture 的
#     layout hint 與 server OCR。
#   - Debug 區塊預設收合，是為了 demo 時讓醫師端畫面更接近實際使用場景。
#   - 畫面可以增加「院內系統感」的假欄位，但不要新增真病人識別欄位或硬寫個資。
#   - SOAP 四個大型文字框仍保持 Win32 child control，可讓 thin capture 先用 control rect 偵測。
# 驗證方式:
#   - 至少跑 py_compile、GUI self-test，並用 Ctrl+Alt+G 實際觸發一次 doctor alert。
# ----------------------------------------------------------------------------------------------------
"""
ClinicalGuard 本機醫師端視窗。

一般操作會由 RootMedicals-Control 啟動；直接除錯時可用:
  python src/clinical_guard_app/gui_app.py --self-test
"""

from __future__ import annotations

import argparse
import json
from http.client import RemoteDisconnected
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    from .icd10_master import ICD10MasterTable, normalize_icd10_code
    from .local_core import AlertRow, ClinicalGuardLocal, DEFAULT_DB, DEFAULT_EXPORT_DIR
except Exception:
    from clinical_guard_app.icd10_master import ICD10MasterTable, normalize_icd10_code  # type: ignore
    from clinical_guard_app.local_core import AlertRow, ClinicalGuardLocal, DEFAULT_DB, DEFAULT_EXPORT_DIR  # type: ignore


DEFAULT_SYNC_URL = "http://127.0.0.1:10001/api/medical/encounter/import"
PID_CUSTOM_OPTION = "自定義 (Custom PID)"


def gui_self_test(db_path: Path, export_dir: Path) -> int:
    if db_path.exists():
        db_path.unlink()
    export_dir.mkdir(parents=True, exist_ok=True)
    guard = ClinicalGuardLocal(db_path)
    alerts = guard.evaluate_alerts(
        soap={
            "S": "fever cough dyspnea",
            "O": "temp 39.1 crackles",
            "A": "",
            "P": "admit",
        },
        vital_signs={"bp": "128/84", "hr": "122", "temp": "39.1", "rr": "26", "spo2": "90"},
    )
    if len(alerts) < 3:
        raise AssertionError("expected >=3 alerts")

    encounter_uuid, created_alerts = guard.create_encounter(
        pid="demo-p",
        patient_uid="pt-gui-001",
        patient_label="GUI Demo",
        visit_time="2026-04-04T12:00:00Z",
        soap={
            "S": "fever cough dyspnea",
            "O": "temp 39.1 crackles",
            "A": "",
            "P": "admit",
        },
        vital_signs={"bp": "128/84", "hr": "122", "temp": "39.1", "rr": "26", "spo2": "90"},
    )
    if not encounter_uuid.startswith("enc-"):
        raise AssertionError("invalid encounter_uuid")
    if len(created_alerts) < 3:
        raise AssertionError("created alerts too few")

    packet = guard.build_packet(encounter_uuid)
    guard.enqueue_packet(encounter_uuid, packet)
    pdf_path = export_dir / "gui_selftest_summary.pdf"
    pdf_result = guard.export_encounter_pdf(encounter_uuid, output_path=pdf_path)
    if pdf_result.get("status") != "success":
        raise AssertionError(f"pdf export failed: {pdf_result}")
    if not pdf_path.exists() or pdf_path.stat().st_size <= 0:
        raise AssertionError("pdf file not created")

    if guard.count_rows("local_encounter") != 1:
        raise AssertionError("local_encounter row mismatch")
    if guard.count_rows("local_sync_queue") != 1:
        raise AssertionError("local_sync_queue row mismatch")
    print("GUI_SELF_TEST_OK")
    return 0


class ClinicalGuardWindow:
    def __init__(self, guard: ClinicalGuardLocal, sync_url: str, export_dir: Path):
        self.guard = guard
        self.sync_url = sync_url
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.icd_sidecar_path = Path(self.guard.db_path).parent / "current_icd_selection.json"
        self.last_encounter_uuid = ""
        self.pid_options: List[str] = []
        self.debug_visible = False
        self.icd_master = ICD10MasterTable.load_default()
        self.selected_icd_record: Any | None = None

        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title("Clinical Guard (Phase 1 Local)")
        self.root.geometry("1180x720")
        self.root.minsize(1040, 650)

        self.vars: Dict[str, Any] = {}
        self._build_layout()
        self._write_icd_sidecar()
        self.on_refresh_pid_options(silent=True)
        self._refresh_queue_stats()

    def _build_layout(self) -> None:
        tk = self.tk
        ttk = self.ttk

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Root.TFrame", background="#e6e8ec")
        style.configure("Panel.TLabelframe", background="#e6e8ec")
        style.configure("Panel.TLabelframe.Label", font=("Microsoft JhengHei UI", 10, "bold"))
        style.configure("Toolbar.TButton", font=("Microsoft JhengHei UI", 9), padding=(3, 7))
        style.configure("Primary.TButton", font=("Microsoft JhengHei UI", 10, "bold"), padding=(8, 5))
        style.configure("Dense.TLabel", background="#e6e8ec", font=("Microsoft JhengHei UI", 9))
        style.configure("Muted.TLabel", background="#e6e8ec", foreground="#475569", font=("Microsoft JhengHei UI", 9))
        style.configure("Alert.TLabel", background="#e6e8ec", foreground="#b91c1c", font=("Microsoft JhengHei UI", 9, "bold"))
        style.configure("Status.TLabel", background="#d9d6ce", foreground="#334155", font=("Microsoft JhengHei UI", 9))

        self.root.geometry("1460x900")
        self.root.minsize(1320, 800)
        container = ttk.Frame(self.root, padding=4, style="Root.TFrame")
        container.pack(fill=tk.BOTH, expand=True)

        self.vars["db_path"] = tk.StringVar(value=str(self.guard.db_path))
        self.vars["sync_url"] = tk.StringVar(value=self.sync_url)
        self.vars["export_dir"] = tk.StringVar(value=str(self.export_dir))
        self.vars["department"] = tk.StringVar(value="普通疾病")
        self.vars["visit_type"] = tk.StringVar(value="初診")
        self.vars["room"] = tk.StringVar(value="D01")
        self.vars["doctor_label"] = tk.StringVar(value="")
        self.vars["age_label"] = tk.StringVar(value="--")
        self.vars["insurance_label"] = tk.StringVar(value="健保")
        self.vars["identity_label"] = tk.StringVar(value="一般")
        self.vars["visit_no"] = tk.StringVar(value="--")
        self.vars["triage_label"] = tk.StringVar(value="門診")
        self.vars["allergy_label"] = tk.StringVar(value="未登錄")
        self.vars["ebm_status"] = tk.StringVar(value="EBM：待送審｜ICD：未選｜RAG：未查詢")
        self.vars["order_days"] = tk.StringVar(value="7")
        self.vars["pharmacy_label"] = tk.StringVar(value="院內藥局")

        # 程式筆記:
        # 這個 top bar 只模擬 HIS 工作站的功能密度，按鈕不接正式院內流程。
        # 真正閉環仍由 SOAP/ICD sidecar + Ctrl+Alt+G 觸發，避免展示 UI 影響後端契約。
        menu_bar = ttk.Frame(container, style="Root.TFrame")
        menu_bar.pack(fill=tk.X, padx=2, pady=(0, 4))
        for text in [
            "門診作業",
            "病歷查詢",
            "檢查檢驗",
            "手術及排程",
            "醫囑與處置",
            "參考資料",
            "IC卡",
            "UpToDate",
            "化/放療作業",
            "醫療決策支援",
        ]:
            ttk.Label(menu_bar, text=text, padding=(8, 2), style="Dense.TLabel").pack(side=tk.LEFT)

        safety_strip = ttk.Frame(container, style="Root.TFrame")
        safety_strip.pack(fill=tk.X, padx=2, pady=(0, 4))
        # 維護筆記:
        # 這排 mimic 院內 HIS 的警示/保險/特殊註記列，只提升展示臨場感。
        # 不把 checkbox 狀態寫入 sidecar，避免 demo UI 裝飾污染 EBM 判斷。
        for label, fg in [
            ("□ 過敏已核", "#334155"),
            ("□ 藥害註記", "#b91c1c"),
            ("□ 慢箋", "#334155"),
            ("□ 自費項目", "#334155"),
            ("□ 需衛教", "#334155"),
            ("■ EBM 審查啟用", "#006d77"),
        ]:
            ttk.Label(safety_strip, text=label, foreground=fg, padding=(8, 1), style="Dense.TLabel").pack(side=tk.LEFT)

        body = ttk.Frame(container, style="Root.TFrame")
        body.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(body, width=172, style="Root.TFrame")
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        left_panel.pack_propagate(False)

        center = ttk.Frame(body, style="Root.TFrame")
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_panel = ttk.Frame(body, width=172, style="Root.TFrame")
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))
        right_panel.pack_propagate(False)
        self.demo_buttons: Dict[str, Any] = {}

        clinic_box = ttk.LabelFrame(left_panel, text="門診資訊", style="Panel.TLabelframe")
        clinic_box.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(clinic_box, text="科別").pack(anchor="w", padx=8, pady=(8, 2))
        self.department_combo = ttk.Combobox(
            clinic_box,
            textvariable=self.vars["department"],
            state="readonly",
            values=["普通疾病", "泌尿科", "家醫科", "耳鼻喉科", "心臟內科", "神經內科", "新陳代謝科"],
            width=14,
        )
        self.department_combo.pack(fill=tk.X, padx=8, pady=(0, 6))
        ttk.Label(clinic_box, text="診間").pack(anchor="w", padx=8, pady=(2, 2))
        ttk.Entry(clinic_box, textvariable=self.vars["room"], width=14).pack(fill=tk.X, padx=8, pady=(0, 6))
        ttk.Label(clinic_box, text="掛號類別").pack(anchor="w", padx=8, pady=(2, 2))
        ttk.Combobox(
            clinic_box,
            textvariable=self.vars["visit_type"],
            state="readonly",
            values=["初診", "複診", "轉診", "術後追蹤"],
            width=14,
        ).pack(fill=tk.X, padx=8, pady=(0, 8))

        encounter_state_box = ttk.LabelFrame(left_panel, text="看診狀態", style="Panel.TLabelframe")
        encounter_state_box.pack(fill=tk.X, pady=(0, 6))
        for label, value in [
            ("身分", "一般"),
            ("保險", "健保"),
            ("看診序", "--"),
            ("類別", "門診"),
        ]:
            row = ttk.Frame(encounter_state_box, style="Root.TFrame")
            row.pack(fill=tk.X, padx=8, pady=(4, 0))
            ttk.Label(row, text=label, width=6, style="Muted.TLabel").pack(side=tk.LEFT)
            ttk.Label(row, text=value, style="Dense.TLabel").pack(side=tk.LEFT)

        visit_box = ttk.LabelFrame(left_panel, text="病人摘要", style="Panel.TLabelframe")
        visit_box.pack(fill=tk.X, pady=(0, 6))
        for label, var_key in [("年齡", "age_label"), ("執行醫師", "doctor_label")]:
            ttk.Label(visit_box, text=label).pack(anchor="w", padx=8, pady=(8, 2))
            ttk.Entry(visit_box, textvariable=self.vars[var_key], width=14).pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Label(visit_box, text="過敏/警示").pack(anchor="w", padx=8, pady=(8, 2))
        ttk.Label(visit_box, textvariable=self.vars["allergy_label"], style="Alert.TLabel").pack(
            anchor="w", padx=8, pady=(0, 8)
        )

        planning_box = ttk.LabelFrame(left_panel, text="處置分類", style="Panel.TLabelframe")
        planning_box.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(planning_box, text="藥物 / 檢查 / 衛教").pack(anchor="w", padx=8, pady=8)

        soap_nav_box = ttk.LabelFrame(left_panel, text="病歷段落", style="Panel.TLabelframe")
        soap_nav_box.pack(fill=tk.X, pady=(0, 6))
        # 維護筆記:
        # 左側段落狀態是視覺輔助，不要把它當作 OCR anchor。真正被擷取的是中央四個 Text control。
        for label in ["S 主訴/病史", "O 理學/檢查", "A 診斷評估", "P 處置計畫"]:
            ttk.Label(soap_nav_box, text=label, style="Dense.TLabel").pack(anchor="w", padx=8, pady=(4, 0))

        encounter = ttk.LabelFrame(center, text="就診資料", style="Panel.TLabelframe")
        encounter.pack(fill=tk.X, padx=2, pady=(0, 4))
        self.vars["pid"] = tk.StringVar(value="demo-pid")
        self.vars["pid_choice"] = tk.StringVar(value=PID_CUSTOM_OPTION)
        self.vars["pid_custom"] = tk.StringVar(value="demo-pid")
        self.vars["patient_uid"] = tk.StringVar(value="00000")
        self.vars["patient_label"] = tk.StringVar(value="")
        self.vars["visit_time"] = tk.StringVar(value=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.vars["icd_search"] = tk.StringVar(value="")
        self.vars["icd10_code"] = tk.StringVar(value="")
        self.vars["diagnosis_label"] = tk.StringVar(value="")
        self.vars["icd_selected_display"] = tk.StringVar(value="ICD-10: -")

        self._row_labeled_entry(encounter, 0, "病歷號", "patient_uid", width=24)
        self._row_labeled_entry(encounter, 0, "病人代稱", "patient_label", col=2, width=24)
        self._row_labeled_entry(encounter, 0, "就診時間", "visit_time", col=4, width=34)
        self._row_labeled_entry(encounter, 1, "身分", "identity_label", width=18)
        self._row_labeled_entry(encounter, 1, "保險", "insurance_label", col=2, width=18)
        self._row_labeled_entry(encounter, 1, "看診序", "visit_no", col=4, width=18)
        ttk.Label(encounter, text="診別").grid(row=1, column=6, sticky="w", padx=(8, 4), pady=5)
        ttk.Entry(encounter, textvariable=self.vars["triage_label"], width=14).grid(
            row=1, column=7, sticky="w", padx=(0, 12), pady=5
        )
        ttk.Label(encounter, text="ICD-10").grid(row=2, column=0, sticky="w", padx=(8, 4), pady=5)
        self.icd_search_frame = ttk.Frame(encounter)
        self.icd_search_frame.grid(row=2, column=1, columnspan=7, sticky="ew", padx=(0, 12), pady=5)
        self.icd_search_frame.grid_columnconfigure(0, weight=1)
        self.icd_entry = ttk.Entry(self.icd_search_frame, textvariable=self.vars["icd_search"], width=72)
        self.icd_entry.grid(row=0, column=0, sticky="ew")
        ttk.Label(self.icd_search_frame, textvariable=self.vars["icd_selected_display"], width=38).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        self.icd_suggestions = tk.Listbox(self.icd_search_frame, height=4, exportselection=False)
        self.icd_suggestions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self.icd_suggestions.grid_remove()
        self.icd_entry.bind("<KeyRelease>", self._on_icd_search_changed)
        self.icd_entry.bind("<Return>", self._on_icd_accept_first)
        self.icd_entry.bind("<Escape>", self._hide_icd_suggestions)
        self.icd_entry.bind("<FocusOut>", self._hide_icd_suggestions)
        self.icd_suggestions.bind("<ButtonRelease-1>", self._on_icd_suggestion_selected)
        self.icd_suggestions.bind("<Return>", self._on_icd_suggestion_selected)
        for col in (1, 3, 5, 7):
            encounter.grid_columnconfigure(col, weight=1)

        self.debug_frame = ttk.LabelFrame(center, text="進階 / 除錯", style="Panel.TLabelframe")

        connection_debug = ttk.LabelFrame(self.debug_frame, text="連線 / 匯出", style="Panel.TLabelframe")
        connection_debug.pack(fill=tk.X, padx=6, pady=(6, 4))
        self._row_labeled_entry(connection_debug, 0, "DB Path", "db_path", width=120)
        self._row_labeled_entry(connection_debug, 1, "Sync URL", "sync_url", width=120)
        self._row_labeled_entry(connection_debug, 2, "Export Dir", "export_dir", width=120)

        pid_debug = ttk.LabelFrame(self.debug_frame, text="PID 專案", style="Panel.TLabelframe")
        pid_debug.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(pid_debug, text="PID Project").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=6)
        self.pid_combo = ttk.Combobox(
            pid_debug,
            textvariable=self.vars["pid_choice"],
            state="readonly",
            width=36,
            values=[PID_CUSTOM_OPTION],
        )
        self.pid_combo.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=6)
        self.pid_combo.bind("<<ComboboxSelected>>", self._on_pid_choice_changed)
        ttk.Button(pid_debug, text="Refresh", command=self.on_refresh_pid_options).grid(
            row=0, column=2, sticky="w", padx=(0, 12), pady=6
        )
        ttk.Label(pid_debug, text="Custom PID").grid(row=1, column=0, sticky="w", padx=(8, 4), pady=6)
        self.pid_custom_entry = ttk.Entry(pid_debug, textvariable=self.vars["pid_custom"], width=40)
        self.pid_custom_entry.grid(row=1, column=1, columnspan=2, sticky="w", padx=(0, 12), pady=6)
        self.pid_custom_entry.bind("<KeyRelease>", self._on_pid_custom_changed)

        soap = ttk.LabelFrame(center, text="SOAP 病歷書寫 / EBM 擷取區", style="Panel.TLabelframe")
        soap.pack(fill=tk.BOTH, expand=True, padx=2, pady=4)
        self._add_text_block(soap, "S  SUBJECTIVE", 0, "soap_s")
        self._add_text_block(soap, "O  OBJECTIVE", 1, "soap_o")
        self._add_text_block(soap, "A  ASSESSMENT", 2, "soap_a")
        self._add_text_block(soap, "P  PLANNING", 3, "soap_p")

        diagnosis_panel = ttk.LabelFrame(center, text="診斷清單 / 處置關聯", style="Panel.TLabelframe")
        diagnosis_panel.pack(fill=tk.X, padx=2, pady=4)
        self.diagnosis_tree = ttk.Treeview(
            diagnosis_panel,
            columns=("code", "name", "kind", "status"),
            show="headings",
            height=2,
        )
        for col, heading, width in [
            ("code", "代碼", 110),
            ("name", "疾病名稱", 290),
            ("kind", "分類", 150),
            ("status", "EBM/覆核狀態", 520),
        ]:
            self.diagnosis_tree.heading(col, text=heading)
            self.diagnosis_tree.column(col, width=width, anchor="w")
        self.diagnosis_tree.pack(fill=tk.X, padx=8, pady=6)
        self._refresh_diagnosis_table()

        order_panel = ttk.LabelFrame(center, text="醫令 / 處置明細", style="Panel.TLabelframe")
        order_panel.pack(fill=tk.X, padx=2, pady=4)
        order_tabs = ttk.Frame(order_panel, style="Root.TFrame")
        order_tabs.pack(fill=tk.X, padx=8, pady=(6, 0))
        for label, bg, fg in [
            ("處方", "#facc15", "#111827"),
            ("檢查", "#e5e7eb", "#334155"),
            ("衛教", "#e5e7eb", "#334155"),
            ("手術代碼(一)", "#e5e7eb", "#7f1d1d"),
            ("手術代碼(二)", "#e5e7eb", "#7f1d1d"),
        ]:
            tag = tk.Label(
                order_tabs,
                text=label,
                bg=bg,
                fg=fg,
                relief=tk.GROOVE,
                bd=1,
                padx=10,
                pady=2,
                font=("Microsoft JhengHei UI", 9, "bold" if bg == "#facc15" else "normal"),
            )
            tag.pack(side=tk.LEFT, padx=(0, 2))
        self.order_tree = ttk.Treeview(
            order_panel,
            columns=("kind", "code", "name", "dose", "freq", "route", "days", "note"),
            show="headings",
            height=2,
        )
        for col, heading, width in [
            ("kind", "類別", 70),
            ("code", "代碼", 90),
            ("name", "名稱", 310),
            ("dose", "劑量", 85),
            ("freq", "頻率", 70),
            ("route", "途徑", 70),
            ("days", "天數", 55),
            ("note", "備註", 260),
        ]:
            self.order_tree.heading(col, text=heading)
            self.order_tree.column(col, width=width, anchor="w")
        self.order_tree.pack(fill=tk.X, padx=8, pady=6)
        self._refresh_order_table()

        order_footer = ttk.Frame(order_panel, style="Root.TFrame")
        order_footer.pack(fill=tk.X, padx=8, pady=(0, 6))
        ttk.Label(order_footer, text="給藥天數", style="Muted.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Entry(order_footer, textvariable=self.vars["order_days"], width=6).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(order_footer, text="調劑藥局", style="Muted.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Entry(order_footer, textvariable=self.vars["pharmacy_label"], width=18).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(order_footer, text="費用/申報狀態：待病歷儲存", style="Muted.TLabel").pack(side=tk.LEFT)

        vs = ttk.LabelFrame(center, text="生命徵象 / 院內紀錄", style="Panel.TLabelframe")
        vs.pack(fill=tk.X, padx=2, pady=4)
        for idx, key in enumerate(["bp", "hr", "temp", "rr", "spo2"]):
            self.vars[f"vs_{key}"] = tk.StringVar(value="")
            self.vars[f"vs_{key}"].trace_add("write", self._on_clinical_context_changed)
            ttk.Label(vs, text=key.upper()).grid(row=0, column=idx * 2, sticky="w", padx=(8, 2), pady=6)
            ttk.Entry(vs, textvariable=self.vars[f"vs_{key}"], width=18).grid(
                row=0,
                column=idx * 2 + 1,
                sticky="w",
                padx=(0, 8),
                pady=6,
            )

        actions = ttk.Frame(center)
        actions.pack(fill=tk.X, padx=2, pady=6)
        ttk.Button(actions, text="儲存病歷", command=self.on_save, style="Primary.TButton").pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="儲存並同步", command=self.on_save_and_sync).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="清空表單", command=self.on_clear_form).pack(side=tk.LEFT, padx=4)
        ttk.Label(actions, textvariable=self.vars["ebm_status"], style="Status.TLabel", padding=(8, 3)).pack(
            side=tk.LEFT, padx=18, fill=tk.X, expand=True
        )
        self.debug_button = ttk.Button(actions, text="顯示除錯", command=self._toggle_debug)
        self.debug_button.pack(side=tk.RIGHT, padx=4)

        debug_actions = ttk.Frame(self.debug_frame)
        debug_actions.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(debug_actions, text="院內規則提示", command=self.on_evaluate).pack(side=tk.LEFT, padx=4)
        ttk.Button(debug_actions, text="儲存並匯出 PDF", command=self.on_save_and_export_pdf).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(debug_actions, text="匯出上一筆 PDF", command=self.on_export_last_pdf).pack(side=tk.LEFT, padx=4)
        ttk.Button(debug_actions, text="同步待送資料", command=self.on_sync_pending).pack(side=tk.LEFT, padx=4)

        info = ttk.LabelFrame(self.debug_frame, text="提示 / Log", style="Panel.TLabelframe")
        info.pack(fill=tk.BOTH, expand=True, padx=6, pady=(4, 6))

        self.alert_text = tk.Text(info, height=6, wrap=tk.WORD)
        self.alert_text.pack(fill=tk.BOTH, expand=False, padx=6, pady=(6, 4))
        self.log_text = tk.Text(info, height=7, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=(4, 6))

        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(center, textvariable=self.status_var)
        self.status_label.pack(fill=tk.X, padx=4, pady=(2, 0))

        demo_box = ttk.LabelFrame(right_panel, text="Demo 情境", style="Panel.TLabelframe")
        demo_box.grid(row=0, column=0, columnspan=2, sticky="ew", padx=3, pady=(0, 6))
        # 維護筆記:
        # 這三顆按鈕只負責「替展示者把案例填進 HIS 欄位」，不直接呼叫 EBM server。
        # 這樣可以保留完整閉環：畫面填好後仍由 thin capture 擷取、server OCR/parse、RAG gate 決定燈號。
        for col, (scenario_key, label) in enumerate(
            [("green", "綠燈"), ("yellow", "黃燈"), ("orange", "橘燈")]
        ):
            btn = ttk.Button(
                demo_box,
                text=label,
                style="Toolbar.TButton",
                command=lambda key=scenario_key: self._apply_demo_scenario(key),
            )
            btn.grid(row=0, column=col, sticky="ew", padx=2, pady=3)
            demo_box.grid_columnconfigure(col, weight=1)
            self.demo_buttons[scenario_key] = btn

        # 右側功能列只保留文字與按鈕密度，不顯示快捷鍵，避免 demo 看起來像工程測試工具。
        right_buttons = [
            "警示清單",
            "IC卡更新",
            "全部歷次",
            "雲端病歷",
            "重科歷次",
            "雲端影像",
            "手術預約",
            "熱鍵說明",
            "診斷說明",
            "預約掛號",
            "住院申請",
            "檢查報告",
            "抽血檢驗",
            "文件掃描",
        ]
        for idx, label in enumerate(right_buttons):
            row = idx // 2 + 1
            col = idx % 2
            ttk.Button(right_panel, text=label, style="Toolbar.TButton").grid(
                row=row,
                column=col,
                sticky="nsew",
                padx=3,
                pady=3,
                ipady=2,
            )
        right_panel.grid_columnconfigure(0, weight=1)
        right_panel.grid_columnconfigure(1, weight=1)

    def _apply_demo_scenario(self, scenario_key: str) -> None:
        demo_cases = {
            "green": {
                "label": "綠燈",
                "icd_display": "J30.9 - Allergic rhinitis, unspecified",
                "soap": {
                    "S": "nasal congestion and sneezing for 4 days.",
                    "O": "mild nasal mucosal swelling, temp 37.4. No fever, no dyspnea.",
                    "A": "allergic rhinitis",
                    "P": "intranasal corticosteroid spray 1 puff twice daily, follow up if symptoms persist",
                },
                "vitals": {"bp": "138/82", "hr": "112", "temp": "36.8", "rr": "18", "spo2": "98"},
            },
            "yellow": {
                "label": "黃燈",
                "icd_display": "",
                "soap": {
                    "S": "nasal congestion and sneezing for 4 days.",
                    "O": "mild nasal mucosal swelling, temp 37.4. No fever, no dyspnea.",
                    "A": "allergic rhinitis",
                    "P": "intranasal corticosteroid spray 1 puff twice daily, follow up if symptoms persist",
                },
                "vitals": {"bp": "138/82", "hr": "112", "temp": "36.8", "rr": "18", "spo2": "98"},
            },
            "orange": {
                "label": "橘燈",
                "icd_display": "J30.9 - Allergic rhinitis, unspecified",
                "soap": {
                    "S": "palpitations for 2 days with intermittent dizziness.",
                    "O": "irregular pulse, BP 138/82, HR 112, temp 36.8, SpO2 98. ECG suggests atrial fibrillation.",
                    "A": "atrial fibrillation",
                    "P": "consider oral anticoagulation for stroke prevention, prefer DOAC if no contraindication",
                },
                "vitals": {"bp": "138/82", "hr": "112", "temp": "36.8", "rr": "18", "spo2": "98"},
            },
        }
        case = demo_cases.get(str(scenario_key or "").strip().lower())
        if not case:
            return

        self.vars["department"].set("普通疾病")
        self.vars["patient_uid"].set("00000")
        self.vars["patient_label"].set("")
        self.vars["visit_time"].set(datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))

        # 維護筆記:
        # 黃燈案例刻意不帶 ICD；綠/橘案例則用同一個 J30.9，讓差異集中在 A/P 是否與 ICD 相符。
        # 這裡手動重設 selected_icd_record，避免上一個案例的 ICD selection 殘留到下一個案例。
        self.selected_icd_record = None
        icd_display = str(case.get("icd_display") or "")
        self.vars["icd_search"].set(icd_display)
        self.vars["icd10_code"].set("")
        self.vars["diagnosis_label"].set("")
        self.vars["icd_selected_display"].set("ICD-10: -")
        self.icd_suggestions.grid_remove()
        if icd_display:
            record = self.icd_master.parse_display(icd_display)
            if record:
                self._apply_icd_record(record, update_search=False)

        soap = case.get("soap") if isinstance(case.get("soap"), dict) else {}
        for field_name in ("S", "O", "A", "P"):
            widget = self.vars.get(f"soap_{field_name.lower()}")
            if widget is None:
                continue
            widget.delete("1.0", "end")
            widget.insert("1.0", str(soap.get(field_name) or ""))

        vitals = case.get("vitals") if isinstance(case.get("vitals"), dict) else {}
        for key in ("bp", "hr", "temp", "rr", "spo2"):
            var = self.vars.get(f"vs_{key}")
            if var is not None:
                var.set(str(vitals.get(key) or ""))

        self._refresh_diagnosis_table()
        self._refresh_order_table()
        self._refresh_ebm_status()
        self._write_icd_sidecar()
        self.status_var.set(f"已套用{case.get('label', '')} demo 情境，可送出 EBM 審查。")

    def _toggle_debug(self) -> None:
        self.debug_visible = not self.debug_visible
        if self.debug_visible:
            self.debug_frame.pack(fill=self.tk.BOTH, expand=False, padx=2, pady=4, before=self.status_label)
            self.debug_button.configure(text="隱藏除錯")
            self.root.geometry("1460x940")
        else:
            self.debug_frame.pack_forget()
            self.debug_button.configure(text="顯示除錯")
            self.root.geometry("1460x900")

    def _refresh_diagnosis_table(self) -> None:
        if not hasattr(self, "diagnosis_tree"):
            return
        for item in self.diagnosis_tree.get_children():
            self.diagnosis_tree.delete(item)
        record = self.selected_icd_record
        dx_text = ""
        try:
            dx_text = str(self.vars["soap_a"].get("1.0", "end-1c")).strip()
        except Exception:
            dx_text = ""
        code = record.code if record else ""
        name = record.label if record else dx_text
        disease_family = record.normalized_diagnosis if record else "依 A 欄文字判讀"
        note = "ICD 已選，等待 EBM 審查" if record else "尚未選 ICD；送審後不可直接綠燈"
        self.diagnosis_tree.insert("", "end", values=(code or "-", name or "-", disease_family, note))

    def _refresh_order_table(self) -> None:
        if not hasattr(self, "order_tree"):
            return
        for item in self.order_tree.get_children():
            self.order_tree.delete(item)
        try:
            plan_text = str(self.vars["soap_p"].get("1.0", "end-1c")).strip()
        except Exception:
            plan_text = ""
        lower_plan = plan_text.lower()
        # 維護筆記:
        # 這張醫令表是 demo 的視覺化摘要，真正送審仍以 P 欄原文為準。
        # 不在這裡替醫師改藥名或做臨床決策，避免 UI 摘要與原文產生責任邊界混淆。
        if "intranasal" in lower_plan or "corticosteroid" in lower_plan or "spray" in lower_plan:
            self.order_tree.insert(
                "",
                "end",
                values=("藥物", "DEMO-AR", "Intranasal corticosteroid spray", "1 puff", "BID", "nasal", "7", "待 EBM 燈號確認"),
            )
            return
        if "anticoagulation" in lower_plan or "doac" in lower_plan:
            self.order_tree.insert(
                "",
                "end",
                values=("藥物", "DEMO-AF", "Oral anticoagulation / DOAC", "依腎功能", "QD/BID", "PO", "--", "需先確認 ICD 與風險評估"),
            )
            return
        if plan_text:
            self.order_tree.insert("", "end", values=("處置", "-", plan_text[:72], "-", "-", "-", "-", "待 EBM 審查"))
        else:
            self.order_tree.insert("", "end", values=("-", "-", "尚未輸入處置", "-", "-", "-", "-", "P 欄空白"))

    def _refresh_ebm_status(self) -> None:
        if "ebm_status" not in self.vars:
            return
        icd_text = "已選" if self.selected_icd_record else "未選"
        try:
            has_plan = bool(str(self.vars["soap_p"].get("1.0", "end-1c")).strip())
        except Exception:
            has_plan = False
        plan_text = "P 已填" if has_plan else "P 空白"
        self.vars["ebm_status"].set(f"EBM：待送審｜ICD：{icd_text}｜{plan_text}｜可送出 EBM 審查")

    def _row_labeled_entry(self, parent: Any, row: int, label: str, var_key: str, col: int = 0, width: int = 36) -> None:
        ttk = self.ttk
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(8, 4), pady=6)
        entry = ttk.Entry(parent, textvariable=self.vars[var_key], width=width)
        entry.grid(row=row, column=col + 1, sticky="w", padx=(0, 12), pady=6)

    def _add_text_block(self, parent: Any, title: str, row: int, key: str) -> None:
        tk = self.tk
        ttk = self.ttk
        ttk.Label(parent, text=f"{title}.").grid(row=row, column=0, sticky="nw", padx=(8, 6), pady=(6, 2))
        txt = tk.Text(parent, height=4, wrap=tk.WORD)
        txt.grid(row=row, column=1, sticky="nsew", padx=(0, 8), pady=(6, 2))
        txt.bind("<KeyRelease>", self._on_clinical_context_changed)
        parent.grid_rowconfigure(row, weight=1)
        parent.grid_columnconfigure(1, weight=1)
        self.vars[key] = txt

    @staticmethod
    def _dedupe_keep_order(items: List[str]) -> List[str]:
        out: List[str] = []
        seen: Dict[str, bool] = {}
        for item in items:
            key = str(item or "").strip()
            if not key or seen.get(key):
                continue
            seen[key] = True
            out.append(key)
        return out

    @staticmethod
    def _origin_from_sync_url(sync_url: str) -> str:
        parsed = urlparse(str(sync_url or "").strip())
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return ""

    def _fetch_project_pids(self, sync_url: str) -> List[str]:
        origin = self._origin_from_sync_url(sync_url)
        if not origin:
            return []

        all_pids: List[str] = []
        for status in ["formal", "temp"]:
            endpoint = f"{origin}/api/project/list?status={status}"
            req = Request(endpoint, method="GET", headers={"Accept": "application/json"})
            try:
                with urlopen(req, timeout=6.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            # 維護筆記:
            # 這裡只是啟動畫面時的 PID 清單輔助，不是 HIS 主流程。
            # 後端或 sync endpoint 沒開時，醫師仍然要能輸入 SOAP 並跑 Ctrl+Alt+G，
            # 所以任何連線層錯誤都只能降級成「沒有遠端 PID」，不能讓 GUI 初始化失敗。
            except (HTTPError, URLError, TimeoutError, ValueError, OSError, RemoteDisconnected):
                continue
            if not bool(data.get("success")):
                continue
            for row in data.get("projects") or []:
                if not isinstance(row, dict):
                    continue
                pid = str(row.get("project_id") or "").strip()
                if pid:
                    all_pids.append(pid)
        return self._dedupe_keep_order(all_pids)

    def _resolve_pid(self) -> str:
        choice_var = self.vars.get("pid_choice")
        custom_var = self.vars.get("pid_custom")
        choice = str(choice_var.get() if choice_var else "").strip()
        if choice and choice != PID_CUSTOM_OPTION:
            return choice
        return str(custom_var.get() if custom_var else "").strip()

    def _set_pid_options(self, options: List[str], selected_pid: str = "") -> None:
        self.pid_options = self._dedupe_keep_order(options)
        values = self.pid_options + [PID_CUSTOM_OPTION]
        self.pid_combo.configure(values=values)

        current_pid = selected_pid.strip() if selected_pid else self._resolve_pid()
        if current_pid and current_pid in self.pid_options:
            self.vars["pid_choice"].set(current_pid)
            self.vars["pid_custom"].set(current_pid)
        elif current_pid:
            self.vars["pid_choice"].set(PID_CUSTOM_OPTION)
            self.vars["pid_custom"].set(current_pid)
        elif self.pid_options:
            self.vars["pid_choice"].set(self.pid_options[0])
            self.vars["pid_custom"].set(self.pid_options[0])
        else:
            self.vars["pid_choice"].set(PID_CUSTOM_OPTION)
            self.vars["pid_custom"].set("")
        self._on_pid_choice_changed()

    def _remember_pid(self, pid: str) -> None:
        value = str(pid or "").strip()
        if not value:
            return
        merged = self._dedupe_keep_order([value] + self.pid_options)
        self._set_pid_options(merged, selected_pid=value)

    def _on_pid_choice_changed(self, _event: Optional[Any] = None) -> None:
        choice = str(self.vars["pid_choice"].get() or "").strip()
        if choice and choice != PID_CUSTOM_OPTION:
            self.vars["pid"].set(choice)
            self.vars["pid_custom"].set(choice)
            self.pid_custom_entry.configure(state="disabled")
        else:
            self.pid_custom_entry.configure(state="normal")
            self.vars["pid"].set(str(self.vars["pid_custom"].get() or "").strip())

    def _on_pid_custom_changed(self, _event: Optional[Any] = None) -> None:
        if str(self.vars["pid_choice"].get() or "").strip() == PID_CUSTOM_OPTION:
            self.vars["pid"].set(str(self.vars["pid_custom"].get() or "").strip())

    def on_refresh_pid_options(self, silent: bool = False) -> None:
        sync_url = str(self.vars["sync_url"].get() or "").strip()
        selected_pid = self._resolve_pid()
        local_pids = self.guard.list_recent_pids(limit=30)
        remote_pids = self._fetch_project_pids(sync_url) if sync_url else []
        merged = self._dedupe_keep_order(remote_pids + local_pids)
        self._set_pid_options(merged, selected_pid=selected_pid)

        if remote_pids:
            self._append_log(f"PID options refreshed from web: {len(remote_pids)} project(s).")
        elif local_pids:
            self._append_log(f"PID options loaded from local history: {len(local_pids)} project(s).")
        else:
            self._append_log("PID options empty. Select Custom PID to input manually.")
            if not silent:
                from tkinter import messagebox

                messagebox.showinfo("PID Options", "No project list from web. Please use Custom PID.")

    def _collect_payload(self) -> Dict[str, Any]:
        icd_record = self._current_icd_record()
        dx_text = str(self.vars["soap_a"].get("1.0", "end-1c")).strip()
        icd10_code = icd_record.code if icd_record else ""
        diagnosis_label = icd_record.label if icd_record else ""
        # 維護筆記：這份 payload 是 ClinicalGuard 自己的儲存/同步資料，不是送往 EBM gate 的正式
        # server contract。Ctrl+Alt+G 流程會由 thin capture 送截圖，再由 server OCR 重建正式 payload。
        # 但 ICD 欄位仍要放在這裡，因為 Save + Sync 與 debug 路徑也需要同一份臨床 anchor。
        return {
            "pid": self._resolve_pid(),
            "patient_uid": str(self.vars["patient_uid"].get()).strip(),
            "patient_label": str(self.vars["patient_label"].get()).strip(),
            "visit_time": str(self.vars["visit_time"].get()).strip() or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "icd_code": icd10_code,
            "icd10_code": icd10_code,
            "diagnosis_label": diagnosis_label,
            "normalized_diagnosis": icd_record.normalized_diagnosis if icd_record else "",
            "dx_text": dx_text,
            "soap": {
                "S": str(self.vars["soap_s"].get("1.0", "end-1c")).strip(),
                "O": str(self.vars["soap_o"].get("1.0", "end-1c")).strip(),
                "A": dx_text,
                "P": str(self.vars["soap_p"].get("1.0", "end-1c")).strip(),
            },
            "vital_signs": {
                "bp": str(self.vars["vs_bp"].get()).strip(),
                "hr": str(self.vars["vs_hr"].get()).strip(),
                "temp": str(self.vars["vs_temp"].get()).strip(),
                "rr": str(self.vars["vs_rr"].get()).strip(),
                "spo2": str(self.vars["vs_spo2"].get()).strip(),
            },
        }

    def _current_icd_record(self) -> Any | None:
        if self.selected_icd_record is not None:
            return self.selected_icd_record
        typed = str(self.vars["icd_search"].get() or "").strip()
        # 醫師可能貼上完整顯示字串、只輸入 code，或輸入疾病名稱。這裡盡量在本機解析，
        # 讓 ICD sidecar 永遠保存 canonical code，server 端才不用猜格式。
        record = self.icd_master.parse_display(typed)
        if record:
            self._apply_icd_record(record, update_search=False)
            return record
        code = normalize_icd10_code(typed)
        if code:
            record = self.icd_master.lookup_code(code)
            if record:
                self._apply_icd_record(record, update_search=True)
                return record
        return None

    def _write_icd_sidecar(self) -> None:
        record = self.selected_icd_record
        # 維護筆記:
        # 這份 sidecar 是 mock HIS 與 thin capture 之間的本機橋接，不是正式病歷資料庫。
        # 它不包含 patient_uid、patient_label 或 visit_time；只放目前畫面已可見的 ICD、
        # SOAP 與 vitals，讓 server 在 OCR 錯讀時仍能重建同一筆 demo case。
        data = {
            "schema_version": "clinicalguard-context.v0.2",
            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "clinicalguard_standalone",
            "icd_code": record.code if record else "",
            "icd10_code": record.code if record else "",
            "diagnosis_label": record.label if record else "",
            "normalized_diagnosis": record.normalized_diagnosis if record else "",
            "dx_text": str(self.vars["soap_a"].get("1.0", "end-1c")).strip(),
            "soap": {
                "S": str(self.vars["soap_s"].get("1.0", "end-1c")).strip(),
                "O": str(self.vars["soap_o"].get("1.0", "end-1c")).strip(),
                "A": str(self.vars["soap_a"].get("1.0", "end-1c")).strip(),
                "P": str(self.vars["soap_p"].get("1.0", "end-1c")).strip(),
            },
            "vital_signs": {
                "bp": str(self.vars["vs_bp"].get()).strip(),
                "hr": str(self.vars["vs_hr"].get()).strip(),
                "temp": str(self.vars["vs_temp"].get()).strip(),
                "rr": str(self.vars["vs_rr"].get()).strip(),
                "spo2": str(self.vars["vs_spo2"].get()).strip(),
            },
        }
        try:
            self.icd_sidecar_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.icd_sidecar_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            # 先寫暫存檔再 replace，避免 hotkey 剛好讀到半寫入 JSON。
            tmp_path.replace(self.icd_sidecar_path)
        except OSError:
            return

    def _on_clinical_context_changed(self, *_args: Any) -> None:
        # Ctrl+Alt+G 可能在醫師打完文字後立刻觸發，因此 sidecar 要跟著欄位內容即時更新。
        # 這裡只寫本機 JSON，不呼叫 server，也不做任何醫學判斷。
        self._refresh_diagnosis_table()
        self._refresh_order_table()
        self._refresh_ebm_status()
        self._write_icd_sidecar()

    def _on_icd_search_changed(self, event: Any | None = None) -> None:
        typed = str(self.vars["icd_search"].get() or "").strip()
        if self.selected_icd_record is not None and typed != self.selected_icd_record.display:
            # 使用者手動改過搜尋框後，舊的 ICD selection 就不能再沿用，否則會造成「畫面文字」
            # 與 sidecar code 不一致，後續燈號會難以解釋。
            self.selected_icd_record = None
            self.vars["icd10_code"].set("")
            self.vars["diagnosis_label"].set("")
            self.vars["icd_selected_display"].set("ICD-10: -")
            self._refresh_diagnosis_table()
            self._write_icd_sidecar()
        exact_record = self.icd_master.parse_display(typed)
        if exact_record:
            # 醫師或測試腳本常會直接貼上 "J30.9 - Allergic rhinitis, unspecified"。
            # 這種情況已經是完整選項，不需要再把候選清單掛在畫面上。
            self._apply_icd_record(exact_record, update_search=False)
            return
        matches = self.icd_master.search(typed, limit=8)
        self.icd_suggestions.delete(0, "end")
        if not typed or not matches:
            self.icd_suggestions.grid_remove()
            return
        for record in matches:
            self.icd_suggestions.insert("end", record.display)
        self.icd_suggestions.selection_set(0)
        self.icd_suggestions.grid()

    def _on_icd_accept_first(self, event: Any | None = None) -> str:
        if self.icd_suggestions.winfo_ismapped() and self.icd_suggestions.size() > 0:
            self.icd_suggestions.selection_clear(0, "end")
            self.icd_suggestions.selection_set(0)
            self._on_icd_suggestion_selected(event)
        return "break"

    def _on_icd_suggestion_selected(self, event: Any | None = None) -> str:
        selection = self.icd_suggestions.curselection()
        if not selection:
            return "break"
        display = str(self.icd_suggestions.get(selection[0]) or "")
        record = self.icd_master.parse_display(display)
        if record:
            self._apply_icd_record(record, update_search=True)
        return "break"

    def _apply_icd_record(self, record: Any, update_search: bool = True) -> None:
        self.selected_icd_record = record
        self.vars["icd10_code"].set(record.code)
        self.vars["diagnosis_label"].set(record.label)
        self.vars["icd_selected_display"].set(f"{record.code} | {record.normalized_diagnosis}")
        if update_search:
            self.vars["icd_search"].set(record.display)
        self.icd_suggestions.grid_remove()
        # 每次選定 ICD 都立即落 sidecar，讓醫師不必先按 Save，Ctrl+Alt+G 仍可帶到結構化 ICD。
        self._refresh_diagnosis_table()
        self._refresh_order_table()
        self._refresh_ebm_status()
        self._write_icd_sidecar()

    def _hide_icd_suggestions(self, event: Any | None = None) -> str:
        self.icd_suggestions.grid_remove()
        return "break"

    @staticmethod
    def _normalize_alert_rows(rows: Any) -> List[AlertRow]:
        out: List[AlertRow] = []
        if not isinstance(rows, list):
            return out
        for idx, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            code = str(row.get("alert_code") or row.get("code") or f"alert_{idx}").strip().lower()
            title = str(row.get("title") or "").strip()
            message = str(row.get("message") or row.get("msg") or "").strip()
            if not (code and title and message):
                continue
            out.append(
                AlertRow(
                    alert_code=code,
                    severity=str(row.get("severity") or "medium").strip().lower() or "medium",
                    title=title,
                    message=message,
                    suggestion=str(row.get("suggestion") or row.get("next") or "").strip(),
                )
            )
        return out

    def _evaluate_via_web_hybrid(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sync_url = str(self.vars.get("sync_url").get() if self.vars.get("sync_url") else "").strip()
        origin = self._origin_from_sync_url(sync_url)
        if not origin:
            return {"ok": False, "message": "Sync URL is empty or invalid."}

        endpoint = f"{origin}/api/medical/alerts/evaluate"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(endpoint, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")

        try:
            with urlopen(req, timeout=10.0) as resp:
                text = resp.read().decode("utf-8")
                parsed = json.loads(text) if text else {}
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="ignore")
            return {"ok": False, "message": f"HTTP {exc.code}: {err_body[:200]}"}
        except (URLError, TimeoutError, ValueError) as exc:
            return {"ok": False, "message": str(exc)}

        if str(parsed.get("status") or "").lower() != "success":
            return {"ok": False, "message": str(parsed.get("message") or "hybrid evaluate failed")}

        final_alerts = self._normalize_alert_rows(parsed.get("final_alerts"))
        local_alerts = self._normalize_alert_rows(parsed.get("local_rule_alerts"))
        llm_alerts = self._normalize_alert_rows(parsed.get("llm_alerts"))
        return {
            "ok": True,
            "used_path": str(parsed.get("used_path") or ""),
            "alerts": final_alerts or local_alerts,
            "local_alerts": local_alerts,
            "llm_alerts": llm_alerts,
            "llm_meta": parsed.get("llm") if isinstance(parsed.get("llm"), dict) else {},
        }

    def _validate_required(self, payload: Dict[str, Any]) -> Optional[str]:
        if not payload["pid"]:
            return "PID is required."
        if not payload["patient_uid"]:
            return "Patient UID is required."
        soap = payload["soap"]
        if not any([soap["S"], soap["O"], soap["A"], soap["P"]]):
            return "At least one SOAP section is required."
        return None

    def _render_alerts(self, alerts: List[AlertRow]) -> None:
        self.alert_text.delete("1.0", "end")
        if not alerts:
            self.alert_text.insert("end", "No alerts.\n")
            return
        for idx, row in enumerate(alerts, start=1):
            self.alert_text.insert(
                "end",
                f"{idx}. [{row.severity.upper()}] {row.title}\n"
                f"   code: {row.alert_code}\n"
                f"   msg : {row.message}\n"
                f"   next: {row.suggestion}\n\n",
            )

    def _append_log(self, text: str) -> None:
        ts = datetime.utcnow().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {text}\n")
        self.log_text.see("end")

    def _show_warning_popup(self, alerts: List[AlertRow]) -> None:
        if not alerts:
            return
        from tkinter import messagebox

        lines = [f"{a.severity.upper()} | {a.title}" for a in alerts[:5]]
        messagebox.showwarning("Clinical Guard Warning", "\n".join(lines))

    def _refresh_queue_stats(self) -> None:
        pending = self.guard.count_rows("local_sync_queue")
        encounters = self.guard.count_rows("local_encounter")
        self.status_var.set(
            f"Ready | encounters={encounters} queue_rows={pending} last_encounter={self.last_encounter_uuid or '-'}"
        )

    def on_evaluate(self) -> None:
        from tkinter import messagebox

        payload = self._collect_payload()
        err = self._validate_required(payload)
        if err:
            messagebox.showerror("Input Error", err)
            return

        local_alerts = self.guard.evaluate_alerts(payload["soap"], payload["vital_signs"])
        alerts = list(local_alerts)
        source = "local_rule"
        web_res = self._evaluate_via_web_hybrid(payload)
        if web_res.get("ok"):
            source = str(web_res.get("used_path") or "hybrid")
            alerts = list(web_res.get("alerts") or alerts)
            llm_meta = web_res.get("llm_meta") if isinstance(web_res.get("llm_meta"), dict) else {}
            self._append_log(
                "Hybrid evaluate done: "
                f"path={source}, local={len(web_res.get('local_alerts') or [])}, "
                f"llm={len(web_res.get('llm_alerts') or [])}, final={len(alerts)}"
            )
            if llm_meta.get("ok") is False and llm_meta.get("message"):
                self._append_log(f"LLM fallback note: {llm_meta.get('message')}")
        else:
            # 本機規則是 ClinicalGuard 早期安全提示；EBM/RAG 燈號走另一條 closed-loop pipeline。
            # 這裡失敗不能阻斷醫師端輸入，也不能冒充 evidence-backed。
            self._append_log(f"Hybrid evaluate unavailable; fallback local rules ({web_res.get('message', 'unknown error')}).")

        self._render_alerts(alerts)
        if alerts:
            self._show_warning_popup(alerts)
            self._append_log(f"Evaluated {len(alerts)} alert(s) via {source}.")
        else:
            self._append_log(f"Evaluated with no alert via {source}.")
            messagebox.showinfo("Clinical Guard", "No alert triggered.")
        self._refresh_queue_stats()

    def _save_encounter_core(self) -> str:
        from tkinter import messagebox

        payload = self._collect_payload()
        err = self._validate_required(payload)
        if err:
            messagebox.showerror("Input Error", err)
            return ""
        self._remember_pid(payload["pid"])

        encounter_uuid, alerts = self.guard.create_encounter(
            pid=payload["pid"],
            patient_uid=payload["patient_uid"],
            patient_label=payload["patient_label"],
            visit_time=payload["visit_time"],
            soap=payload["soap"],
            vital_signs=payload["vital_signs"],
        )
        packet = self.guard.build_packet(encounter_uuid)
        # Sync queue 是模擬 HIS 後送；與 Ctrl+Alt+G 的截圖送審是不同路徑。兩者都保留，是為了 demo
        # 可以同時展示「醫師端輸入」與「後端資料匯入」兩種整合方式。
        self.guard.enqueue_packet(encounter_uuid, packet)

        self.last_encounter_uuid = encounter_uuid
        self._render_alerts(alerts)
        self._show_warning_popup(alerts)
        self._append_log(f"Saved encounter={encounter_uuid}, alerts={len(alerts)}, queued for sync.")
        self._refresh_queue_stats()
        return encounter_uuid

    def on_save(self) -> None:
        self._save_encounter_core()

    def on_save_and_sync(self) -> None:
        encounter_uuid = self._save_encounter_core()
        if not encounter_uuid:
            return
        self.on_sync_pending()

    def _export_pdf(self, encounter_uuid: str, choose_path: bool = False) -> None:
        from tkinter import filedialog, messagebox

        if not encounter_uuid:
            messagebox.showerror("Export Error", "No encounter available for export.")
            return

        out_path = None
        export_dir = Path(str(self.vars["export_dir"].get()).strip() or self.export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir = export_dir
        if choose_path:
            suggested = export_dir / f"visit_summary_{encounter_uuid}.pdf"
            selected = filedialog.asksaveasfilename(
                title="Export Visit Summary PDF",
                initialdir=str(export_dir),
                initialfile=suggested.name,
                defaultextension=".pdf",
                filetypes=[("PDF", "*.pdf")],
            )
            if not selected:
                return
            out_path = Path(selected)

        result = self.guard.export_encounter_pdf(encounter_uuid, output_path=out_path)
        if result.get("status") != "success":
            messagebox.showerror("Export Error", str(result.get("message") or "Export failed"))
            self._append_log(f"Export failed: {result}")
            return

        pdf_path = str(result.get("path") or "")
        self._append_log(f"PDF exported: {pdf_path}")
        messagebox.showinfo("Export Success", f"Visit summary PDF exported:\n{pdf_path}")
        self._refresh_queue_stats()

    def on_save_and_export_pdf(self) -> None:
        encounter_uuid = self._save_encounter_core()
        if not encounter_uuid:
            return
        self._export_pdf(encounter_uuid, choose_path=True)

    def on_export_last_pdf(self) -> None:
        encounter_uuid = self.last_encounter_uuid or self.guard.latest_encounter_uuid()
        self._export_pdf(encounter_uuid, choose_path=True)

    def on_sync_pending(self) -> None:
        from tkinter import messagebox

        sync_url = str(self.vars["sync_url"].get()).strip()
        if not sync_url:
            messagebox.showerror("Input Error", "Sync URL is required.")
            return
        result = self.guard.sync_pending(sync_url)
        self._append_log(
            f"Sync done: sent={result.get('sent', 0)} failed={result.get('failed', 0)} total={result.get('total', 0)}"
        )
        if int(result.get("failed", 0)) > 0:
            messagebox.showwarning("Sync Result", f"Sync completed with failures: {result}")
        else:
            messagebox.showinfo("Sync Result", f"Sync success: {result}")
        self._refresh_queue_stats()

    def on_clear_form(self) -> None:
        self.vars["patient_uid"].set("")
        self.vars["patient_label"].set("")
        self.vars["visit_time"].set(datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.selected_icd_record = None
        self.vars["icd_search"].set("")
        self.vars["icd10_code"].set("")
        self.vars["diagnosis_label"].set("")
        self.vars["icd_selected_display"].set("ICD-10: -")
        self.icd_suggestions.grid_remove()
        for key in ["vs_bp", "vs_hr", "vs_temp", "vs_rr", "vs_spo2"]:
            self.vars[key].set("")
        for key in ["soap_s", "soap_o", "soap_a", "soap_p"]:
            self.vars[key].delete("1.0", "end")
        self._refresh_diagnosis_table()
        self._refresh_order_table()
        self._refresh_ebm_status()
        self._write_icd_sidecar()
        self.alert_text.delete("1.0", "end")
        self._append_log("Form cleared.")
        self._refresh_queue_stats()

    def run(self) -> None:
        self.root.mainloop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clinical Guard GUI")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Local sqlite db path")
    parser.add_argument("--sync-url", default=DEFAULT_SYNC_URL, help="Medical import API URL")
    parser.add_argument("--export-dir", default=str(DEFAULT_EXPORT_DIR), help="Default PDF export directory")
    parser.add_argument("--self-test", action="store_true", help="Run non-GUI self test and exit")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    export_dir = Path(args.export_dir)
    if args.self_test:
        return gui_self_test(db_path, export_dir)

    guard = ClinicalGuardLocal(db_path)
    app = ClinicalGuardWindow(guard, str(args.sync_url), export_dir=export_dir)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
