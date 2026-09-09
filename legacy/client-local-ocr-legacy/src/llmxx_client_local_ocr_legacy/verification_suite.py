# 檔案路徑: rootmedicals-a/legacy/client-local-ocr-legacy/src/llmxx_client_local_ocr_legacy/verification_suite.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: LocalOCR 客戶端程式，負責截圖、OCR/薄客戶端傳送、醫師端提醒視窗與驗證工具。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

# Path: ./llmxx-client-local-ocr/src/llmxx_client_local_ocr/verification_suite.py
# Version History:
# v0.1 20260614-0000 - Add movable-window OCR verification suite with eight mixed SOAP samples.
# v0.2 20260614-0000 - Wait briefly after moving the mock HIS window before capture and typing.
# v0.3 20260614-0000 - Verify ten server_payload samples including Vital Signs without per-sample expected files.
# v0.4 20260614-0000 - Use OCR-stable punctuation sample wording while preserving numeric slash coverage.
# v0.5 20260614-0000 - Verify standalone server_payload JSON files match pipeline payloads.
# v0.6 20260614-0000 - Replace verification samples with mixed punctuation, numbers, spaces, and partial-field cases.

from __future__ import annotations

import json
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List

from .mock_his_typer import MockHISTyper
from .pipeline import LocalOCRPipeline
from .window_locator import WindowLocator, enable_dpi_awareness


def run_verification_suite(config: Dict[str, Any], *, sample_limit: int = 10, move_window: bool = True) -> Dict[str, Any]:
    enable_dpi_awareness()
    samples = _build_samples()[:sample_limit]
    diagnostics_dir = Path(str(config.get("app", {}).get("diagnostics_dir", "diagnostics"))).resolve()
    suite_dir = diagnostics_dir / "verification_suite"
    suite_dir.mkdir(parents=True, exist_ok=True)

    typer = MockHISTyper(config)
    pipeline = LocalOCRPipeline(config)
    locator = WindowLocator(config.get("target_window", {}))
    threshold = float(config.get("verification", {}).get("similarity_threshold", 0.92))

    results: List[Dict[str, Any]] = []
    for index, sample in enumerate(samples):
        if move_window:
            _move_target_window(config, index)

        input_payload = _copy_encounter(sample["input"])
        expected_subset = _copy_encounter(sample["expected"])

        click_points = typer.fill_encounter(
            soap=input_payload["soap"],
            vital_signs=input_payload["vital_signs"],
        )
        run_result = pipeline.run_once(send_override=False, expected_path=None)
        server_payload = dict(run_result.payload)
        file_match = _server_payload_file_matches(run_result.server_payload_path, server_payload)
        actual_subset = {
            "soap": {field: str(server_payload.get("soap", {}).get(field, "") or "") for field in ("S", "O", "A", "P")},
            "vital_signs": {
                field: str(server_payload.get("vital_signs", {}).get(field, "") or "")
                for field in ("bp", "hr", "temp", "rr", "spo2")
            },
        }
        compare_result = _compare_encounter(actual=actual_subset, expected=expected_subset, threshold=threshold)
        window = locator.find_target_window()

        # Inner v0.1: verification report stores text, coordinates, and diagnostics paths only;
        # no patient screenshot or raw image is persisted.
        # Inner v0.3: server_payload is copied verbatim so handoff can prove that the formal
        # client output equals the text typed into the mock HIS.
        results.append(
            {
                "id": sample["id"],
                "description": sample["description"],
                "server_payload_strict_pass": compare_result["strict_pass"],
                "server_payload_threshold_pass": compare_result["threshold_pass"],
                "score_by_field": compare_result["score_by_field"],
                "input": input_payload,
                "expected_server_payload_subset": expected_subset,
                "actual_server_payload_subset": actual_subset,
                "server_payload": server_payload,
                "server_payload_path": run_result.server_payload_path,
                "server_payload_file_matches": file_match,
                "normalizations": server_payload.get("normalizations", {}),
                "redactions": server_payload.get("redactions", {}),
                "capture_regions": run_result.diagnostics_payload.get("capture_meta", {}).get("regions", []),
                "window": {
                    "left": window.left,
                    "top": window.top,
                    "width": window.width,
                    "height": window.height,
                },
                "click_points": click_points,
                "diagnostics_path": run_result.diagnostics_path,
                "notes": compare_result["notes"],
            }
        )

    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sample_count": len(results),
        "server_payload_strict_pass_count": sum(1 for item in results if item["server_payload_strict_pass"]),
        "server_payload_threshold_pass_count": sum(1 for item in results if item["server_payload_threshold_pass"]),
        "server_payload_file_match_count": sum(1 for item in results if item["server_payload_file_matches"]),
        "all_server_payload_strict_passed": all(item["server_payload_strict_pass"] for item in results),
        "all_server_payload_threshold_passed": all(item["server_payload_threshold_pass"] for item in results),
        "all_server_payload_files_match": all(item["server_payload_file_matches"] for item in results),
        "results": results,
    }
    report_path = suite_dir / f"suite_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def _move_target_window(config: Dict[str, Any], index: int) -> None:
    try:
        import win32con
        import win32gui
        import win32api
    except Exception as exc:
        raise RuntimeError("pywin32 is required for movable-window verification") from exc

    window = WindowLocator(config.get("target_window", {})).find_target_window()
    screen_width = int(win32api.GetSystemMetrics(0))
    screen_height = int(win32api.GetSystemMetrics(1))
    width = min(max(900, window.width), max(900, screen_width - 40))
    height = min(max(700, window.height), max(700, screen_height - 80))
    positions = [
        (20, 20),
        (max(20, screen_width - width - 20), 20),
        (20, max(20, screen_height - height - 60)),
        (max(20, (screen_width - width) // 2), max(20, (screen_height - height) // 2)),
        (80, 40),
        (max(20, screen_width - width - 80), 70),
        (40, 100),
        (max(20, (screen_width - width) // 3), 30),
        (120, max(20, (screen_height - height) // 3)),
        (max(20, (screen_width - width) // 2), 80),
    ]
    x, y = positions[index % len(positions)]
    if win32gui.IsIconic(window.hwnd):
        win32gui.ShowWindow(window.hwnd, win32con.SW_RESTORE)
    win32gui.MoveWindow(window.hwnd, int(x), int(y), int(width), int(height), True)
    win32gui.SetForegroundWindow(window.hwnd)
    time.sleep(0.4)


def _build_samples() -> List[Dict[str, Any]]:
    # Inner v0.6: These samples intentionally mix Chinese, English, punctuation, numbers,
    # spacing, and missing fields so the handoff evidence matches the demo contract instead
    # of only proving that fully-filled clean English forms work.
    return [
        {
            "id": "mixed_punct_full_01",
            "description": "Mixed Chinese/English with comma, slash score, dose, and full Vital Signs.",
            "input": {
                "soap": {
                    "S": "頭痛 headache 3 days, pain 7/10",
                    "O": "alert, no fever",
                    "A": "migraine 偏頭痛, mild",
                    "P": "acetaminophen 500mg, follow up",
                },
                "vital_signs": {"bp": "130/80", "hr": "76", "temp": "37.2", "rr": "16", "spo2": "98"},
            },
            "expected": {
                "soap": {
                    "S": "頭痛 headache 3 days, pain 7/10",
                    "O": "alert, no fever",
                    "A": "migraine 偏頭痛, mild",
                    "P": "acetaminophen 500mg, follow up",
                },
                "vital_signs": {"bp": "130/80", "hr": "76", "temp": "37.2", "rr": "16", "spo2": "98"},
            },
        },
        {
            "id": "mixed_punct_full_02",
            "description": "Respiratory mixed Chinese/English with fever decimal and plus sign.",
            "input": {
                "soap": {
                    "S": "咳嗽 cough 5 days, fever 38.5",
                    "O": "喉嚨紅, no wheezing",
                    "A": "viral URI, acute bronchitis",
                    "P": "rest fluids + return if worse",
                },
                "vital_signs": {"bp": "118/72", "hr": "88", "temp": "38.5", "rr": "18", "spo2": "98"},
            },
            "expected": {
                "soap": {
                    "S": "咳嗽 cough 5 days, fever 38.5",
                    "O": "喉嚨紅, no wheezing",
                    "A": "viral URI, acute bronchitis",
                    "P": "rest fluids + return if worse",
                },
                "vital_signs": {"bp": "118/72", "hr": "88", "temp": "38.5", "rr": "18", "spo2": "98"},
            },
        },
        {
            "id": "mixed_digestive_numbers",
            "description": "Mixed digestive text with x2, slash score, comma, and normal vitals.",
            "input": {
                "soap": {
                    "S": "腹痛 abd pain 7/10, nausea x2",
                    "O": "soft abdomen, no guarding",
                    "A": "gastroenteritis likely",
                    "P": "oral fluids, return if fever",
                },
                "vital_signs": {"bp": "124/82", "hr": "84", "temp": "37.0", "rr": "16", "spo2": "99"},
            },
            "expected": {
                "soap": {
                    "S": "腹痛 abd pain 7/10, nausea x2",
                    "O": "soft abdomen, no guarding",
                    "A": "gastroenteritis likely",
                    "P": "oral fluids, return if fever",
                },
                "vital_signs": {"bp": "124/82", "hr": "84", "temp": "37.0", "rr": "16", "spo2": "99"},
            },
        },
        {
            "id": "english_numeric_punct",
            "description": "English with colon, decimal, comma, and medication dose.",
            "input": {
                "soap": {
                    "S": "diabetes follow up, glucose 145",
                    "O": "HbA1c 7.2, no dizziness",
                    "A": "type 2 diabetes",
                    "P": "metformin 500mg bid, meals",
                },
                "vital_signs": {"bp": "136/84", "hr": "78", "temp": "36.7", "rr": "16", "spo2": "97"},
            },
            "expected": {
                "soap": {
                    "S": "diabetes follow up, glucose 145",
                    "O": "HbA1c 7.2, no dizziness",
                    "A": "type 2 diabetes",
                    "P": "metformin 500mg bid, meals",
                },
                "vital_signs": {"bp": "136/84", "hr": "78", "temp": "36.7", "rr": "16", "spo2": "97"},
            },
        },
        {
            "id": "mixed_chest_symbols",
            "description": "Mixed chest-pain text with comma, decimal dose, and upper-case acronym.",
            "input": {
                "soap": {
                    "S": "胸悶 chest pain 2 hrs",
                    "O": "ECG normal, SpO2 98",
                    "A": "rule out ACS",
                    "P": "ER if worse, nitro 0.6mg",
                },
                "vital_signs": {"bp": "118/72", "hr": "92", "temp": "36.9", "rr": "18", "spo2": "98"},
            },
            "expected": {
                "soap": {
                    "S": "胸悶 chest pain 2 hrs",
                    "O": "ECG normal, SpO2 98",
                    "A": "rule out ACS",
                    "P": "ER if worse, nitro 0.6mg",
                },
                "vital_signs": {"bp": "118/72", "hr": "92", "temp": "36.9", "rr": "18", "spo2": "98"},
            },
        },
        {
            "id": "chinese_punct_spaces",
            "description": "Mostly Chinese with ASCII comma, spaces, and mixed follow-up wording.",
            "input": {
                "soap": {
                    "S": "頭暈 2 days, 無嘔吐",
                    "O": "意識清楚, gait stable",
                    "A": "眩暈 vertigo",
                    "P": "休息, follow up 3 days",
                },
                "vital_signs": {"bp": "122/80", "hr": "72", "temp": "36.8", "rr": "16", "spo2": "99"},
            },
            "expected": {
                "soap": {
                    "S": "頭暈 2 days, 無嘔吐",
                    "O": "意識清楚, gait stable",
                    "A": "眩暈 vertigo",
                    "P": "休息, follow up 3 days",
                },
                "vital_signs": {"bp": "122/80", "hr": "72", "temp": "36.8", "rr": "16", "spo2": "99"},
            },
        },
        {
            "id": "partial_soap_only_missing_o_p",
            "description": "Missing O/P and all Vital Signs blank; output should still be written.",
            "input": {
                "soap": {
                    "S": "感冒 cold 2 days, cough",
                    "O": "",
                    "A": "viral URI",
                    "P": "",
                },
                "vital_signs": {"bp": "", "hr": "", "temp": "", "rr": "", "spo2": ""},
            },
            "expected": {
                "soap": {
                    "S": "感冒 cold 2 days, cough",
                    "O": "",
                    "A": "viral URI",
                    "P": "",
                },
                "vital_signs": {"bp": "", "hr": "", "temp": "", "rr": "", "spo2": ""},
            },
        },
        {
            "id": "partial_some_vitals_blank",
            "description": "Full SOAP but some Vital Signs blank; output should preserve empty strings.",
            "input": {
                "soap": {
                    "S": "back pain, since yesterday",
                    "O": "no weakness",
                    "A": "muscle strain",
                    "P": "NSAID, rest",
                },
                "vital_signs": {"bp": "132/86", "hr": "", "temp": "36.6", "rr": "", "spo2": "98"},
            },
            "expected": {
                "soap": {
                    "S": "back pain, since yesterday",
                    "O": "no weakness",
                    "A": "muscle strain",
                    "P": "NSAID, rest",
                },
                "vital_signs": {"bp": "132/86", "hr": "", "temp": "36.6", "rr": "", "spo2": "98"},
            },
        },
        {
            "id": "partial_single_field_only",
            "description": "Only S is filled; all other SOAP/Vital Signs fields should output as empty strings.",
            "input": {
                "soap": {
                    "S": "skin rash 3 days, itchy",
                    "O": "",
                    "A": "",
                    "P": "",
                },
                "vital_signs": {"bp": "", "hr": "", "temp": "", "rr": "", "spo2": ""},
            },
            "expected": {
                "soap": {
                    "S": "skin rash 3 days, itchy",
                    "O": "",
                    "A": "",
                    "P": "",
                },
                "vital_signs": {"bp": "", "hr": "", "temp": "", "rr": "", "spo2": ""},
            },
        },
        {
            "id": "mixed_spaces_numbers_final",
            "description": "Final mixed case with numbers, comma, slash, and full Vital Signs.",
            "input": {
                "soap": {
                    "S": "鼻塞 nasal block 4 days",
                    "O": "throat mild red, temp 37.4",
                    "A": "allergic rhinitis",
                    "P": "spray 1 puff bid, follow up",
                },
                "vital_signs": {"bp": "120/78", "hr": "80", "temp": "37.4", "rr": "18", "spo2": "98"},
            },
            "expected": {
                "soap": {
                    "S": "鼻塞 nasal block 4 days",
                    "O": "throat mild red, temp 37.4",
                    "A": "allergic rhinitis",
                    "P": "spray 1 puff bid, follow up",
                },
                "vital_signs": {"bp": "120/78", "hr": "80", "temp": "37.4", "rr": "18", "spo2": "98"},
            },
        },
    ]


def _copy_encounter(value: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    return {
        "soap": {field: str(value.get("soap", {}).get(field, "") or "") for field in ("S", "O", "A", "P")},
        "vital_signs": {
            field: str(value.get("vital_signs", {}).get(field, "") or "")
            for field in ("bp", "hr", "temp", "rr", "spo2")
        },
    }


def _compare_encounter(
    *,
    actual: Dict[str, Dict[str, str]],
    expected: Dict[str, Dict[str, str]],
    threshold: float,
) -> Dict[str, Any]:
    scores: Dict[str, float] = {}
    notes: List[str] = []
    strict_pass = True
    for group_name, fields in (("soap", ("S", "O", "A", "P")), ("vital_signs", ("bp", "hr", "temp", "rr", "spo2"))):
        for field in fields:
            key = f"{group_name}.{field}"
            exp = str(expected.get(group_name, {}).get(field, "") or "")
            act = str(actual.get(group_name, {}).get(field, "") or "")
            score = SequenceMatcher(None, exp, act).ratio() if exp or act else 1.0
            scores[key] = round(score, 4)
            if act != exp:
                strict_pass = False
                notes.append(f"{key} strict mismatch: expected='{exp}' actual='{act}'")
            if score < threshold:
                notes.append(f"{key} score below threshold: {score:.4f}")
    return {
        "strict_pass": strict_pass,
        "threshold_pass": all(score >= threshold for score in scores.values()),
        "score_by_field": scores,
        "notes": notes,
    }


def _server_payload_file_matches(path_value: str | None, expected_payload: Dict[str, Any]) -> bool:
    if not path_value:
        return False
    path = Path(path_value)
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as f:
            actual_payload = json.load(f)
    except Exception:
        return False
    # Inner v0.5: The standalone file must be byte-format independent but semantically identical
    # to the payload that ServerClient.send() would POST.
    return actual_payload == expected_payload
