# 模組定位: self-hosted llmebm Topic Page 的 bounded DOM+screenshot scanner。
# 主要責任: 擷取靜態 slot manifest/截圖；結構變更才生成，視覺變更只刷新 evidence。
# 呼叫來源: 開發/驗收命令與未來受控的 llmebm refresh workflow。
# 輸入契約: allowlisted http(s) origin、Topic Page URL 與 scan-root 目錄。
# 輸出契約: llmebm-topic-manifest.v1、相對 screenshot artifact、sha256 與 scan status。
# 安全邊界: 不掃任意外站、不讀 cookies/localStorage/history；artifact 不得逃出 scan root。
# 維護提醒: DOM selector/互動狀態改動時，先更新 fixture test，再更新 bounded scanner。
# ----------------------------------------------------------------------------------------------------

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit


LLMEBM_ROOT = Path(__file__).resolve().parents[1]
if str(LLMEBM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLMEBM_ROOT))

from app.model.topic_content_model import (
    ALLOWED_BLOCKS,
    TOPIC_SCAN_ROOT,
    load_screenshot_payloads,
    manifest_hash,
    topic_content_db,
)


DEFAULT_ORIGINS = (
    "http://127.0.0.1:8001", "http://localhost:8001",
    "http://127.0.0.1:33300", "http://localhost:33300",
)


def normalized_origin(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Scanner URL must be an HTTP(S) origin without credentials.")
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default_port
    return f"{parsed.scheme}://{parsed.hostname.lower()}:{port}"


def validate_scan_url(url, allow_origins):
    parsed = urlsplit(url)
    allowed = {normalized_origin(origin) for origin in allow_origins}
    if normalized_origin(url) not in allowed:
        raise ValueError("Scanner URL origin is not allowlisted.")
    if not parsed.path.startswith("/topic/"):
        raise ValueError("Scanner only accepts llmebm /topic/ knowledge pages.")
    return url


def canonicalize_dom_slots(raw_slots):
    slots = []
    seen = set()
    for order, raw in enumerate(raw_slots):
        slot_id = str(raw.get("slot_id", "")).strip()
        heading = str(raw.get("heading", "")).strip()
        if not slot_id or not heading or slot_id in seen:
            continue
        allowed_blocks = raw.get("allowed_blocks", ALLOWED_BLOCKS)
        if (
            not isinstance(allowed_blocks, (list, tuple))
            or not allowed_blocks
            or any(block not in ALLOWED_BLOCKS for block in allowed_blocks)
        ):
            raise ValueError(f"Invalid allowed_blocks in DOM slot: {slot_id}")
        seen.add(slot_id)
        slots.append({
            "slot_id": slot_id,
            "heading": heading,
            "heading_path": [str(value).strip() for value in raw.get("heading_path", []) if str(value).strip()],
            "level": int(raw.get("level", 1)),
            "order": order,
            "content_target": True,
            "allowed_blocks": list(dict.fromkeys(allowed_blocks)),
        })
    if not slots:
        raise ValueError("No Topic content slots were found in the DOM.")
    return slots


def should_trigger_generation(scan_status, enabled=True):
    return bool(enabled and scan_status == "saved")


def classify_scan_status(current_manifest, rescanned_manifest, current_artifacts_valid):
    """Separate semantic slot changes from screenshot-only evidence refreshes."""
    if not current_manifest or current_manifest.get("dom_hash") != rescanned_manifest.get("dom_hash"):
        return "saved"
    if not current_artifacts_valid:
        return "visual_updated"

    def visual_contract(manifest):
        return {
            "viewport": manifest.get("viewport"),
            "screenshots": [
                {
                    "sha256": shot.get("sha256"),
                    "mime_type": shot.get("mime_type"),
                    "state": shot.get("state"),
                }
                for shot in manifest.get("screenshots", [])
            ],
        }

    return "unchanged" if visual_contract(current_manifest) == visual_contract(rescanned_manifest) else "visual_updated"


def request_generation(page_url, topic_name):
    parsed = urlsplit(page_url)
    endpoint = f"{parsed.scheme}://{parsed.netloc}/api/v1/topic/{quote(topic_name, safe='')}/content/generate"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.getenv("LLMEBM_TOPIC_GENERATION_TOKEN", "")
    if token:
        headers["X-LLMEBM-Topic-Token"] = token
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"only_slot_ids": [], "force": False, "filters": {}, "top_k": 10}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"llmebm generation request failed with HTTP {exc.code}: {detail}") from exc
    if len(body) > 1024 * 1024:
        raise RuntimeError("llmebm generation response exceeded 1 MiB.")
    return json.loads(body.decode("utf-8"))


def scan(url, output_root, allow_origins, generate=True):
    validate_scan_url(url, allow_origins)
    try:
        from scrapling.fetchers import DynamicSession
    except ImportError as exc:
        raise RuntimeError("Install scanner dependencies from requirements-scan.txt first.") from exc

    output_root = Path(output_root).resolve()
    allowed_artifact_root = Path(TOPIC_SCAN_ROOT).resolve()
    if not output_root.is_relative_to(allowed_artifact_root):
        raise ValueError(f"Output root must stay under {allowed_artifact_root}.")
    output_root.mkdir(parents=True, exist_ok=True)
    capture = {}

    def capture_expanded_state(page):
        validate_scan_url(page.url, allow_origins)
        page.locator(".sb-expand[aria-expanded='false']").evaluate_all("els => els.forEach(el => el.click())")
        page.wait_for_timeout(150)
        capture["topic_name"] = page.locator("body").get_attribute("data-topic-name") or ""
        capture["topic_uid"] = page.locator("body").get_attribute("data-topic-uid") or ""
        capture["viewport"] = page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
        capture["slots"] = page.evaluate("""
            () => {
                const result = [];
                function walk(container, path) {
                    for (const child of container.children) {
                        if (!child.classList.contains('sb-node')) continue;
                        const button = child.querySelector(':scope > .sb-title > .sb-select, :scope > .sb-leaf');
                        if (!button) continue;
                        const heading = button.textContent.trim();
                        const headingPath = [...path, heading];
                        result.push({
                            slot_id: button.dataset.slotId,
                            heading,
                            heading_path: headingPath,
                            level: Number(button.dataset.slotLevel || 1),
                            allowed_blocks: JSON.parse(button.dataset.allowedBlocks || '[]')
                        });
                        const nested = child.querySelector(':scope > .sb-children');
                        if (nested) walk(nested, headingPath);
                    }
                }
                walk(document.getElementById('sidebar-menu-container'), []);
                return result;
            }
        """)
        safe_uid = re.sub(r"[^a-zA-Z0-9_.-]+", "_", capture["topic_uid"] or "topic")[:100]
        capture["screenshot_path"] = output_root / f".{safe_uid}-sidebar-expanded.pending.png"
        page.screenshot(path=str(capture["screenshot_path"]), full_page=True, type="png")

    with DynamicSession(headless=True, disable_resources=False, network_idle=True) as session:
        session.fetch(
            url,
            wait_selector="[data-slot-id]",
            page_action=capture_expanded_state,
            load_dom=False,
            timeout=30000,
        )

    screenshot_bytes = capture["screenshot_path"].read_bytes()
    if len(screenshot_bytes) > 5 * 1024 * 1024:
        capture["screenshot_path"].unlink(missing_ok=True)
        raise ValueError("Screenshot exceeds the 5 MiB Topic generation limit.")
    screenshot_sha = hashlib.sha256(screenshot_bytes).hexdigest()
    final_screenshot_path = output_root / f"{screenshot_sha}.png"
    if final_screenshot_path.exists():
        capture["screenshot_path"].unlink()
    else:
        capture["screenshot_path"].replace(final_screenshot_path)
    capture["screenshot_path"] = final_screenshot_path
    manifest = {
        "schema": "llmebm-topic-manifest.v1",
        "topic_uid": capture["topic_uid"],
        "topic_name": capture["topic_name"],
        "template_version": "condition.v1",
        "viewport": capture["viewport"],
        "screenshots": [{
            "sha256": screenshot_sha,
            "mime_type": "image/png",
            "state": "sidebar-expanded",
            "artifact_path": capture["screenshot_path"].relative_to(allowed_artifact_root).as_posix(),
        }],
        "slots": canonicalize_dom_slots(capture["slots"]),
    }
    manifest["dom_hash"] = manifest_hash(manifest)
    manifest_path = output_root / f"{manifest['dom_hash'].split(':', 1)[1]}.json"
    current_manifest = topic_content_db.get_current_manifest(manifest["topic_name"])
    current_artifacts_valid = False
    if current_manifest and current_manifest.get("dom_hash") == manifest["dom_hash"]:
        try:
            load_screenshot_payloads(current_manifest)
            current_artifacts_valid = True
        except ValueError:
            pass
    generation = None
    scan_status = classify_scan_status(current_manifest, manifest, current_artifacts_valid)
    if scan_status != "unchanged":
        topic_content_db.save_manifest(manifest)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        current_artifacts = {shot.get("artifact_path") for shot in current_manifest.get("screenshots", [])}
        new_artifact = manifest["screenshots"][0]["artifact_path"]
        if new_artifact not in current_artifacts:
            capture["screenshot_path"].unlink(missing_ok=True)
        manifest = current_manifest
    if should_trigger_generation(scan_status, generate):
        generation = request_generation(url, manifest["topic_name"])
    return {"status": scan_status, "manifest": manifest, "path": str(manifest_path), "generation": generation}


def main():
    parser = argparse.ArgumentParser(description="Scan an allowlisted local llmebm Topic Page.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--allow-origin", action="append", dest="allow_origins")
    parser.add_argument("--no-generate", action="store_true", help="Save a changed manifest without starting RAG generation.")
    args = parser.parse_args()
    result = scan(args.url, args.output_root, args.allow_origins or DEFAULT_ORIGINS, generate=not args.no_generate)
    print(json.dumps({
        "status": result["status"], "dom_hash": result["manifest"]["dom_hash"],
        "path": result["path"], "generation": result["generation"],
    }))


if __name__ == "__main__":
    main()
