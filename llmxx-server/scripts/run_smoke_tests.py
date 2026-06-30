# 檔案路徑: rootmedicals-a/llmxx-server/scripts/run_smoke_tests.py
# 產生時間: 2026-06-17 16:10 +08:00
# 版本: v0.1-交付整理
# 說明: llmxx-server 內部維運腳本，供 RootMedicals-Control 或工程診斷呼叫。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SERVER_DIR = SCRIPT_DIR.parent
ROOTMEDICALS_A_DIR = SERVER_DIR.parent
CLIENT_PAYLOAD_ROOT = ROOTMEDICALS_A_DIR / "runtime_reports" / "archived-thin-capture-client-generated" / "server_payloads"


class HttpResult:
    def __init__(self, status_code: int, payload: dict[str, Any]):
        self.status_code = status_code
        self.payload = payload


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _is_usable_payload(payload: dict[str, Any]) -> bool:
    if payload.get("schema_version") != "llmxx-client-local-ocr.v0.1":
        return False
    soap = payload.get("soap")
    if not isinstance(soap, dict):
        return False
    return bool(str(soap.get("A") or "").strip() or str(soap.get("P") or "").strip())


def find_latest_usable_payload() -> Path:
    if not CLIENT_PAYLOAD_ROOT.exists():
        raise FileNotFoundError(f"Payload root not found: {CLIENT_PAYLOAD_ROOT}")
    files = sorted(CLIENT_PAYLOAD_ROOT.rglob("server_payload_*.json"), key=lambda item: str(item), reverse=True)
    for path in files:
        try:
            if _is_usable_payload(_load_json(path)):
                return path
        except Exception:
            continue
    if files:
        return files[0]
    raise FileNotFoundError(f"No server_payload_*.json files found under {CLIENT_PAYLOAD_ROOT}")


def post_json(url: str, payload: dict[str, Any], timeout: float = 12.0) -> HttpResult:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            return HttpResult(response.status, parsed if isinstance(parsed, dict) else {"raw": parsed})
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            parsed = {"error": str(error)}
        return HttpResult(error.code, parsed if isinstance(parsed, dict) else {"raw": parsed})


def get_json(url: str, timeout: float = 12.0) -> HttpResult:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            return HttpResult(response.status, parsed if isinstance(parsed, dict) else {"raw": parsed})
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            parsed = {"error": str(error)}
        return HttpResult(error.code, parsed if isinstance(parsed, dict) else {"raw": parsed})


def fresh_payload(base_payload: dict[str, Any], suffix: str) -> dict[str, Any]:
    payload = copy.deepcopy(base_payload)
    payload["session_id"] = f"smoke-{int(time.time() * 1000)}-{suffix}"
    payload["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return payload


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def run_tests(server_url: str, payload_path: Path, timeout: float) -> int:
    base_url = server_url.rstrip("/")
    payload = _load_json(payload_path)
    failures: list[str] = []
    notes: list[str] = []

    health = get_json(f"{base_url}/api/health", timeout=timeout)
    require(health.status_code == 200, f"health status expected 200, got {health.status_code}", failures)
    require(bool(health.payload.get("ok")), "health ok expected true", failures)

    intake_payload = fresh_payload(payload, "main")
    intake = post_json(f"{base_url}/api/intake", intake_payload, timeout=timeout)
    require(intake.status_code in {200, 202}, f"intake expected 200/202, got {intake.status_code}: {intake.payload}", failures)
    require(bool(intake.payload.get("ok")), "intake ok expected true for formal usable payload", failures)
    session_id = str(intake.payload.get("session_id") or "")
    require(bool(session_id), "intake response session_id missing", failures)
    notes.append(f"main_intake_status={intake.payload.get('status')} http={intake.status_code} error={intake.payload.get('error_code')}")

    repeated = post_json(f"{base_url}/api/intake", intake_payload, timeout=timeout)
    require(repeated.status_code == intake.status_code, "idempotent replay HTTP status changed", failures)
    require(repeated.payload.get("session_id") == session_id, "idempotent replay returned a different session_id", failures)

    conflict_payload = copy.deepcopy(intake_payload)
    conflict_payload["soap"]["P"] = str(conflict_payload.get("soap", {}).get("P") or "") + " smoke-conflict"
    conflict = post_json(f"{base_url}/api/intake", conflict_payload, timeout=timeout)
    require(conflict.status_code == 409, f"conflict expected 409, got {conflict.status_code}", failures)
    require(conflict.payload.get("error_code") == "client_session_conflict", "conflict error_code mismatch", failures)

    demo = get_json(f"{base_url}/api/demo/latest", timeout=timeout)
    require(demo.status_code == 200, f"demo/latest expected 200, got {demo.status_code}", failures)
    require(bool(demo.payload.get("ok")), "demo/latest ok expected true after intake", failures)

    session_detail = get_json(f"{base_url}/api/sessions/{session_id}", timeout=timeout)
    require(session_detail.status_code == 200, f"session detail expected 200, got {session_detail.status_code}", failures)
    require(session_detail.payload.get("response", {}).get("session_id") == session_id, "session detail did not return smoke session response", failures)

    events = get_json(f"{base_url}/api/sessions/{session_id}/events", timeout=timeout)
    require(events.status_code == 200, f"events expected 200, got {events.status_code}", failures)
    event_items = events.payload.get("events")
    require(isinstance(event_items, list) and len(event_items) >= 3, "events should contain intake/clinical/rag stages", failures)

    diagnostics_payload = fresh_payload(payload, "diagnostics")
    diagnostics_payload["diagnostics"] = {"debug": True}
    diagnostics = post_json(f"{base_url}/api/intake", diagnostics_payload, timeout=timeout)
    require(diagnostics.status_code == 400, f"diagnostics reject expected 400, got {diagnostics.status_code}", failures)
    require(diagnostics.payload.get("error_code") == "diagnostics_wrapper_rejected", "diagnostics reject error_code mismatch", failures)

    image_payload = fresh_payload(payload, "image")
    image_payload["image_b64"] = "A" * 2048
    image = post_json(f"{base_url}/api/intake", image_payload, timeout=timeout)
    require(image.status_code == 400, f"image reject expected 400, got {image.status_code}", failures)
    require(image.payload.get("error_code") == "image_payload_rejected", "image reject error_code mismatch", failures)

    unsupported_payload = fresh_payload(payload, "schema")
    unsupported_payload["schema_version"] = "llmxx-client-local-ocr.v9.9"
    unsupported = post_json(f"{base_url}/api/intake", unsupported_payload, timeout=timeout)
    require(unsupported.status_code == 400, f"unsupported schema expected 400, got {unsupported.status_code}", failures)
    require(unsupported.payload.get("error_code") == "unsupported_schema_version", "unsupported schema error_code mismatch", failures)

    missing_dx_payload = fresh_payload(payload, "missing-dx")
    missing_dx_payload["soap"]["A"] = ""
    missing_dx = post_json(f"{base_url}/api/intake", missing_dx_payload, timeout=timeout)
    require(missing_dx.status_code in {400, 202}, f"missing dx expected 400/202 degraded, got {missing_dx.status_code}", failures)
    require(missing_dx.payload.get("error_code") in {"invalid_payload", "rag_not_ready", "rag_unavailable"}, "missing dx error_code mismatch", failures)

    restore_payload = fresh_payload(payload, "restore-demo")
    restore = post_json(f"{base_url}/api/intake", restore_payload, timeout=timeout)
    require(restore.status_code in {200, 202}, f"restore demo expected 200/202, got {restore.status_code}: {restore.payload}", failures)
    require(bool(restore.payload.get("ok")), "restore demo ok expected true for formal usable payload", failures)
    notes.append(f"restore_demo_session={restore.payload.get('session_id')} status={restore.payload.get('status')}")

    print(json.dumps({
        "ok": not failures,
        "server_url": base_url,
        "payload_path": str(payload_path),
        "session_id": session_id,
        "notes": notes,
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run llmxx-server HTTP smoke tests.")
    parser.add_argument("--server-url", default="http://127.0.0.1:8017")
    parser.add_argument("--payload-path", default="")
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()
    payload_path = Path(args.payload_path).resolve() if args.payload_path else find_latest_usable_payload()
    return run_tests(args.server_url, payload_path, args.timeout)


if __name__ == "__main__":
    sys.exit(main())

