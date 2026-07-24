# 模組定位: llmebm 結構掃描與逐 slot regeneration 的 durable queue worker。
# 主要責任: reconcile evidence stale、claim scan/regenerate job、執行 DOM+截圖、遵守 mapping gate、追蹤 LLM generation 終態。
# 呼叫來源: llmebm-automation Docker sidecar 或本機 scanner venv 的長駐命令。
# 輸入契約: topic_automation_jobs、allowlisted llmebm base URL、共享 topic_content.db 與 scan root。
# 輸出契約: completed/waiting_review/failed/superseded queue 狀態及既有 topic_generation_jobs 結果。
# 安全邊界: 每次只送一個 affected slot、force=false、require_current_scope_review=true；不持有 review token。
# 維護提醒: queue schema/生成端點/掃描器契約變更時同步 test_topic_automation_queue.py。
# ----------------------------------------------------------------------------------------------------

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote


LLMEBM_ROOT = Path(__file__).resolve().parents[1]
if str(LLMEBM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLMEBM_ROOT))

from app.model.topic_content_model import TOPIC_SCAN_ROOT, topic_content_db
from tools.scan_topic_manifest import scan


TERMINAL_GENERATION_STATUSES = {
    "completed", "completed_with_errors", "failed", "interrupted",
}


class AutomationHttpError(RuntimeError):
    def __init__(self, status_code, detail):
        super().__init__(str(detail))
        self.status_code = int(status_code)
        self.detail = str(detail)


def request_generation(base_url, topic_name, slot_id):
    """Submit one bounded slot to the existing server-side generation gate."""
    endpoint = f"{base_url.rstrip('/')}/api/v1/topic/{quote(topic_name, safe='')}/content/generate"
    token = os.getenv("LLMEBM_TOPIC_GENERATION_TOKEN", "")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["X-LLMEBM-Topic-Token"] = token
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({
            "only_slot_ids": [slot_id],
            "force": False,
            "require_current_scope_review": True,
            "filters": {},
            "top_k": 10,
        }).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read(4096).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            detail = payload.get("detail") or payload.get("message") or raw
        except json.JSONDecodeError:
            detail = raw
        raise AutomationHttpError(exc.code, detail) from exc
    except urllib.error.URLError as exc:
        raise AutomationHttpError(503, f"llmebm unavailable: {exc.reason}") from exc
    if len(body) > 1024 * 1024:
        raise AutomationHttpError(502, "llmebm generation response exceeded 1 MiB")
    return json.loads(body.decode("utf-8"))


def request_topic_status(base_url, topic_name):
    """Read llmebm's own computed slot status; this endpoint never invokes an LLM."""
    endpoint = f"{base_url.rstrip('/')}/api/v1/topic/{quote(topic_name, safe='')}/content/status"
    request = urllib.request.Request(endpoint, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise AutomationHttpError(exc.code, exc.read(4096).decode("utf-8", errors="replace")) from exc
    except urllib.error.URLError as exc:
        raise AutomationHttpError(503, f"llmebm unavailable: {exc.reason}") from exc
    if len(body) > 1024 * 1024:
        raise AutomationHttpError(502, "llmebm topic status response exceeded 1 MiB")
    return json.loads(body.decode("utf-8"))


def reconcile_evidence_staleness(database, *, base_url=None, status_request=None):
    """Queue only evidence-only stale slots; structure changes stay owned by the visual scan path."""
    base_url = (base_url or os.getenv("LLMEBM_AUTOMATION_BASE_URL", "http://127.0.0.1:33300")).rstrip("/")
    queued = []
    for manifest in database.list_current_manifests():
        response = (
            status_request(manifest["topic_name"])
            if status_request
            else request_topic_status(base_url, manifest["topic_name"])
        )
        revisions = response.get("evidence_revisions") or {}
        content_targets = {
            str(slot["slot_id"])
            for slot in manifest.get("slots", [])
            if slot.get("content_target", True)
        }
        for slot_id, slot_status in (response.get("slots") or {}).items():
            if (
                slot_id not in content_targets
                or slot_status.get("status") != "stale"
                or slot_status.get("stale_reasons") != ["evidence"]
            ):
                continue
            revision = revisions.get(slot_id) or response.get("evidence_revision")
            if not revision:
                continue
            queued.append(database.enqueue_automation_job(
                topic_uid=manifest["topic_uid"],
                topic_name=manifest["topic_name"],
                job_type="regenerate",
                dedupe_key=(
                    f"regenerate:{manifest['topic_uid']}:{manifest['dom_hash']}:{slot_id}:evidence:{revision}"
                ),
                payload={
                    "manifest_hash": manifest["dom_hash"],
                    "slot_id": slot_id,
                    "evidence_revision": revision,
                    "trigger": "evidence_revision_change",
                },
                reuse_terminal=True,
            ))
    return queued


def wait_for_generation_job(database, generation_job_id, timeout_seconds=1900, poll_seconds=2):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = database.get_generation_job(generation_job_id)
        if job and job["status"] in TERMINAL_GENERATION_STATUSES:
            return job
        time.sleep(poll_seconds)
    raise TimeoutError(f"Generation job {generation_job_id} did not finish before timeout.")


def process_automation_job(
    database,
    job,
    *,
    scan_func=scan,
    generation_request=None,
    generation_wait=None,
    status_request=None,
    base_url=None,
    output_root=None,
    retry_delay_seconds=15,
):
    """Process exactly one claimed job; callers own retry scheduling and the outer loop."""
    base_url = (base_url or os.getenv("LLMEBM_AUTOMATION_BASE_URL", "http://127.0.0.1:33300")).rstrip("/")
    output_root = output_root or os.getenv("LLMEBM_TOPIC_SCAN_ROOT", TOPIC_SCAN_ROOT)
    payload = job.get("payload") or {}
    try:
        if job["job_type"] == "scan":
            result = scan_func(
                url=f"{base_url}/topic/{quote(job['topic_name'], safe='')}",
                output_root=output_root,
                allow_origins=(base_url,),
                generate=False,
            )
            manifest = result["manifest"]
            expected_hash = payload.get("expected_manifest_hash")
            if manifest.get("dom_hash") != expected_hash:
                database.update_automation_job(
                    job["job_id"], "superseded",
                    result={"expected_manifest_hash": expected_hash, "actual_manifest_hash": manifest.get("dom_hash")},
                )
                return
            allowed_slots = {
                str(slot["slot_id"])
                for slot in manifest.get("slots", [])
                if slot.get("content_target", True)
            }
            queued = []
            for slot_id in payload.get("affected_slot_ids", []):
                if slot_id not in allowed_slots:
                    continue
                queued.append(database.enqueue_automation_job(
                    topic_uid=manifest["topic_uid"],
                    topic_name=manifest["topic_name"],
                    job_type="regenerate",
                    dedupe_key=f"regenerate:{manifest['topic_uid']}:{manifest['dom_hash']}:{slot_id}",
                    payload={"manifest_hash": manifest["dom_hash"], "slot_id": slot_id},
                ))
            database.update_automation_job(
                job["job_id"], "completed",
                result={"scan_status": result["status"], "manifest_hash": manifest["dom_hash"], "queued_job_ids": queued},
            )
            return

        if job["job_type"] != "regenerate":
            raise ValueError(f"Unsupported automation job type: {job['job_type']}")
        current_manifest = database.get_current_manifest(job["topic_name"])
        if not current_manifest or current_manifest.get("dom_hash") != payload.get("manifest_hash"):
            database.update_automation_job(job["job_id"], "superseded")
            return
        slot_id = payload["slot_id"]
        expected_evidence_revision = payload.get("evidence_revision")
        if expected_evidence_revision:
            current_status = (
                status_request(job["topic_name"])
                if status_request
                else request_topic_status(base_url, job["topic_name"])
            )
            current_revision = (
                (current_status.get("evidence_revisions") or {}).get(slot_id)
                or current_status.get("evidence_revision")
            )
            if current_revision != expected_evidence_revision:
                database.update_automation_job(
                    job["job_id"], "superseded",
                    result={
                        "expected_evidence_revision": expected_evidence_revision,
                        "current_evidence_revision": current_revision,
                    },
                )
                return
        response = (
            generation_request(job["topic_name"], slot_id)
            if generation_request
            else request_generation(base_url, job["topic_name"], slot_id)
        )
        if response.get("status") == "unchanged":
            database.update_automation_job(job["job_id"], "completed", result=response)
            return
        generation_job_id = response.get("job_id")
        if response.get("status") != "accepted" or not generation_job_id:
            raise RuntimeError("llmebm returned an invalid generation acceptance response.")
        terminal = (
            generation_wait(generation_job_id)
            if generation_wait
            else wait_for_generation_job(database, generation_job_id)
        )
        terminal_status = terminal.get("status")
        if terminal_status not in {"completed", "completed_with_errors"}:
            raise RuntimeError(f"Generation job ended as {terminal_status}: {terminal.get('error')}")
        database.update_automation_job(
            job["job_id"], "completed",
            result={"generation_job_id": generation_job_id, "generation_status": terminal_status},
        )
    except AutomationHttpError as exc:
        if exc.status_code == 409 and "mapping review" in exc.detail.lower():
            database.update_automation_job(
                job["job_id"], "waiting_review",
                error={"status_code": exc.status_code, "detail": exc.detail},
            )
            return
        error = {"status_code": exc.status_code, "detail": exc.detail}
        if exc.status_code in {408, 425, 429, 500, 502, 503, 504}:
            database.retry_automation_job(job["job_id"], error, retry_delay_seconds)
        else:
            database.update_automation_job(job["job_id"], "failed", error=error)
    except Exception as exc:
        database.retry_automation_job(
            job["job_id"],
            {"type": type(exc).__name__, "detail": str(exc)},
            retry_delay_seconds,
        )


def run_forever(database=topic_content_db):
    worker_id = os.getenv("LLMEBM_AUTOMATION_WORKER_ID", f"{socket.gethostname()}:{os.getpid()}")
    poll_seconds = max(1.0, float(os.getenv("LLMEBM_AUTOMATION_POLL_SECONDS", "5")))
    reconcile_seconds = max(5.0, float(os.getenv("LLMEBM_AUTOMATION_RECONCILE_SECONDS", "300")))
    next_reconcile_at = time.monotonic() + reconcile_seconds
    recovered = database.recover_running_automation_jobs()
    print(json.dumps({"event": "automation_worker_started", "worker_id": worker_id, "recovered": recovered}), flush=True)
    while True:
        if time.monotonic() >= next_reconcile_at:
            try:
                # ponytail: polling is the smallest decoupled closure; replace with an ingestion event when topic scale makes O(topics) checks costly.
                ensured = reconcile_evidence_staleness(database)
                print(json.dumps({"event": "evidence_reconcile_completed", "revision_jobs_ensured": len(ensured)}), flush=True)
            except Exception as exc:
                print(json.dumps({
                    "event": "evidence_reconcile_failed",
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                }), flush=True)
            next_reconcile_at = time.monotonic() + reconcile_seconds
        job = database.claim_automation_job(worker_id)
        if not job:
            time.sleep(poll_seconds)
            continue
        print(json.dumps({"event": "automation_job_claimed", "job_id": job["job_id"], "job_type": job["job_type"]}), flush=True)
        process_automation_job(database, job)


if __name__ == "__main__":
    run_forever()
