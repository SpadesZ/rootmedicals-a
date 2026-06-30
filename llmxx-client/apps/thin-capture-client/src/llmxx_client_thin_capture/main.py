# 檔案路徑: rootmedicals-a/llmxx-client/apps/thin-capture-client/src/llmxx_client_thin_capture/main.py
# 產生時間: 2026-06-18 10:50 +08:00
# 版本: v0.3
# 模組定位:
#   thin capture client 的 CLI 入口。這是醫師端閉環的本機啟動面，可跑單次截圖、全域 hotkey、
#   tray 常駐、ClinicalGuard 啟動與測試文字填入。
# 主要責任:
#   1. 解析命令列參數，載入 config/default_config.json。
#   2. 將不同執行模式導到 HotkeyRunner、TrayClientApp、ThinCapturePipeline。
#   3. CLI 輸出只顯示遮蔽後 payload，避免把完整截圖 base64 印到終端或 log。
# 維護提醒:
#   - 這裡不做 OCR、不解析 SOAP、不做 ICD/RAG 判斷；所有語意處理都在 llmxx-server。
#   - 若要新增 demo 操作指令，請保持 command 小而清楚，不要讓 CLI 承擔 server 的醫學邏輯。
# 驗證方式:
#   - python -m llmxx_client_thin_capture.main print-config
#   - python -m llmxx_client_thin_capture.main run-once --no-send
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .config import DEFAULT_CONFIG_PATH, load_config, write_expected_template
from .hotkey_runner import HotkeyRunner
from .mock_his_launcher import launch_mock_his
from .mock_his_typer import MockHISTyper
from .pipeline import ThinCapturePipeline
from .payload import redact_screenshot_payload_for_local_logs
from .tray_app import TrayClientApp


def build_parser() -> argparse.ArgumentParser:
    # CLI 指令故意維持薄層，方便 RootMedicals-Control 或人工終端呼叫時能快速定位問題。
    parser = argparse.ArgumentParser(description="RootMedicals llmxx-client thin screenshot sender")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to JSON config")
    sub = parser.add_subparsers(dest="command", required=True)

    run_once = sub.add_parser("run-once", help="Capture HIS screenshot, build thin payload, and optionally send once")
    send_group = run_once.add_mutually_exclusive_group()
    send_group.add_argument("--send", action="store_true", help="Force sending JSON to configured server")
    send_group.add_argument("--no-send", action="store_true", help="Force skip server send")
    run_once.add_argument("--expected", default="", help="Legacy argument kept for old scripts; ignored by thin capture")

    sub.add_parser("hotkey", help="Run terminal hotkey mode")
    sub.add_parser("tray", help="Run system tray background mode with hotkey and menu")
    sub.add_parser("launch-mock-his", help="Launch sibling ClinicalGuard_Standalone mock HIS window")
    sub.add_parser("print-config", help="Print resolved config")
    fill_mock = sub.add_parser("fill-mock-his", help="Fill ClinicalGuard mock HIS S/O/A/P fields from expected SOAP JSON")
    fill_mock.add_argument("--expected", default="", help="Expected SOAP JSON file to paste into mock HIS")
    write_expected = sub.add_parser("write-expected", help="Write expected SOAP template")
    write_expected.add_argument("--out", default="config/expected_soap.sample.json", help="Output expected SOAP JSON path")
    write_expected.add_argument("--overwrite", action="store_true", help="Overwrite existing file")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "print-config":
        print(json.dumps(config, ensure_ascii=False, indent=2))
        return 0

    if args.command == "write-expected":
        path = write_expected_template(Path(args.out), overwrite=bool(args.overwrite))
        print(str(path))
        return 0

    if args.command == "hotkey":
        HotkeyRunner(config).run_forever()
        return 0

    if args.command == "tray":
        return TrayClientApp(config).run()

    if args.command == "launch-mock-his":
        result = launch_mock_his(config.get("_project_root", Path.cwd()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "fill-mock-his":
        # 這個輔助只把測試文字打進可見 HIS，不會修改 ClinicalGuard 程式檔。
        expected_path = args.expected or str(config.get("verification", {}).get("expected_soap_path", ""))
        # 回報固定點擊座標，方便排查視窗縮放或欄位定位問題，且不需要保存病歷截圖。
        soap, click_points = MockHISTyper(config).fill_from_expected_file(expected_path)
        print(json.dumps({"status": "success", "filled": soap, "click_points": click_points}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "run-once":
        send_override = None
        if args.send:
            send_override = True
        if args.no_send:
            send_override = False
        result = ThinCapturePipeline(config).run_once(send_override=send_override, expected_path=args.expected or None)
        print(json.dumps({
            # CLI 只能印遮蔽後 payload，避免終端或 log 中出現完整截圖 base64。
            "thin_payload": redact_screenshot_payload_for_local_logs(result.payload),
            "server_payload_path": result.server_payload_path,
            "diagnostics_payload": result.diagnostics_payload,
            "verification": result.verification.to_dict() if result.verification else None,
            "server_response": result.server_response,
            "diagnostics_path": result.diagnostics_path,
        }, ensure_ascii=False, indent=2))
        return 0

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

