# 模組定位: llmebm Topic content 的受控、小批次、可續跑生成工具。
# 主要責任: 依 slot 狀態與 manifest 順序選取工作，逐批提交並保存驗收 receipt。
# 呼叫來源: 開發/驗收命令；中斷後可直接重跑，current slot 狀態即為 checkpoint。
# 輸入契約: 本機或授權 llmebm API、canonical topic slug、明確 statuses 與 bounded batch size。
# 輸出契約: llmebm-topic-generation-run.v1 JSON receipt；每批記錄 job 與前後狀態。
# 安全邊界: 不 force、不並行 job、不重試 active/error job；預設只處理 stale/failed。
# 維護提醒: batch 上限與 planner bounded fallback 一致；擴大前先補 token/timeout 驗收。
# ----------------------------------------------------------------------------------------------------

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "llmebm-topic-generation-run.v1"
ALLOWED_STATUSES = ("stale", "failed", "insufficient_evidence", "empty")
ACTIVE_JOB_STATUSES = {"queued", "running"}
TERMINAL_JOB_STATUSES = {"completed", "completed_with_errors", "failed"}
DEFAULT_RUNTIME_DIR = Path(__file__).resolve().parents[1] / "data" / "runtime" / "topic_generation_runs"


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _request_json(url, *, method="GET", payload=None, timeout=30):
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        token = os.getenv("LLMEBM_TOPIC_GENERATION_TOKEN", "")
        if token:
            headers["X-LLMEBM-Topic-Token"] = token
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Unable to reach {url}: {error.reason}") from error
    if len(body) > 2 * 1024 * 1024:
        raise RuntimeError(f"Response exceeded 2 MiB: {url}")
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid JSON from {url}") from error


def _topic_endpoint(base_url, topic_name, suffix):
    topic = urllib.parse.quote(topic_name.strip(), safe="")
    return f"{base_url.rstrip('/')}/api/v1/topic/{topic}/{suffix.lstrip('/')}"


def status_counts(status_payload):
    counts = {}
    for item in (status_payload.get("slots") or {}).values():
        state = str(item.get("status") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return dict(sorted(counts.items()))


def select_generation_candidates(manifest, status_payload, statuses=("stale", "failed")):
    requested = tuple(statuses)
    unknown = set(requested).difference(ALLOWED_STATUSES)
    if unknown:
        raise ValueError(f"unknown statuses: {', '.join(sorted(unknown))}")
    slot_states = status_payload.get("slots") or {}
    ordered_ids = [slot["slot_id"] for slot in manifest.get("slots", []) if slot.get("content_target", True)]
    # ponytail: status priority plus manifest order is enough; a durable queue is only needed for multi-worker execution.
    return [
        slot_id
        for status in requested
        for slot_id in ordered_ids
        if (slot_states.get(slot_id) or {}).get("status") == status
    ]


def build_batches(slot_ids, batch_size):
    if not 1 <= int(batch_size) <= 6:
        raise ValueError("batch_size must be between 1 and 6")
    return [list(slot_ids[index:index + batch_size]) for index in range(0, len(slot_ids), batch_size)]


def restrict_candidates(candidates, requested_slot_ids):
    requested = list(dict.fromkeys(requested_slot_ids))
    ineligible = set(requested).difference(candidates)
    if ineligible:
        raise ValueError(f"explicit slot IDs are not currently eligible: {', '.join(sorted(ineligible))}")
    requested_set = set(requested)
    return [slot_id for slot_id in candidates if slot_id in requested_set]


def _write_receipt(receipt, path):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(pending, path)


def _snapshot(base_url, topic_name):
    manifest_payload = _request_json(_topic_endpoint(base_url, topic_name, "manifest"))
    status_payload = _request_json(_topic_endpoint(base_url, topic_name, "content/status"))
    manifest = manifest_payload.get("manifest")
    if not isinstance(manifest, dict) or not isinstance(status_payload.get("slots"), dict):
        raise RuntimeError("llmebm returned an invalid manifest/status contract")
    return manifest, status_payload


def _wait_for_job(base_url, topic_name, job_id, poll_seconds, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status_payload = _request_json(_topic_endpoint(base_url, topic_name, "content/status"))
        job = status_payload.get("latest_job") or {}
        if job.get("job_id") != job_id:
            raise RuntimeError("latest_job changed while the controlled batch was running")
        if job.get("status") in TERMINAL_JOB_STATUSES:
            return status_payload
        time.sleep(poll_seconds)
    raise TimeoutError(f"job {job_id} exceeded {timeout_seconds} seconds")


def run(args):
    statuses = tuple(value.strip() for value in args.statuses.split(",") if value.strip())
    manifest, initial_status = _snapshot(args.base_url, args.topic)
    latest_job = initial_status.get("latest_job") or {}
    if latest_job.get("status") in ACTIVE_JOB_STATUSES:
        raise RuntimeError(f"active topic generation job already exists: {latest_job.get('job_id')}")
    candidates = select_generation_candidates(manifest, initial_status, statuses)
    if args.slot_ids:
        candidates = restrict_candidates(
            candidates, (value.strip() for value in args.slot_ids.split(",") if value.strip())
        )
    batches = build_batches(candidates, args.batch_size)[:args.max_batches]
    receipt_path = Path(args.receipt) if args.receipt else (
        DEFAULT_RUNTIME_DIR / f"{args.topic}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    receipt = {
        "schema": SCHEMA,
        "topic_name": args.topic,
        "topic_uid": manifest.get("topic_uid"),
        "manifest_hash": manifest.get("dom_hash"),
        "base_url": args.base_url,
        "requested_statuses": list(statuses),
        "batch_size": args.batch_size,
        "max_batches": args.max_batches,
        "dry_run": args.dry_run,
        "started_at": _utc_now(),
        "initial_counts": status_counts(initial_status),
        "selected_slot_ids": [slot_id for batch in batches for slot_id in batch],
        "remaining_candidate_count": max(0, len(candidates) - sum(map(len, batches))),
        "batches": [],
        "status": "planned" if args.dry_run else "running",
    }
    _write_receipt(receipt, receipt_path)
    if args.dry_run or not batches:
        receipt["status"] = "dry_run" if args.dry_run else "unchanged"
        receipt["finished_at"] = _utc_now()
        receipt["final_counts"] = receipt["initial_counts"]
        _write_receipt(receipt, receipt_path)
        return receipt_path, receipt

    try:
        for index, slot_ids in enumerate(batches, start=1):
            accepted = _request_json(
                _topic_endpoint(args.base_url, args.topic, "content/generate"),
                method="POST",
                payload={
                    "only_slot_ids": slot_ids,
                    "force": False,
                    "require_current_scope_review": True,
                    "filters": {},
                    "top_k": args.top_k,
                },
                timeout=30,
            )
            job_id = accepted.get("job_id")
            if accepted.get("status") != "accepted" or not job_id:
                raise RuntimeError(f"batch {index} was not accepted: {accepted}")
            batch_record = {"index": index, "slot_ids": slot_ids, "job_id": job_id, "accepted_at": _utc_now()}
            receipt["batches"].append(batch_record)
            _write_receipt(receipt, receipt_path)
            completed_status = _wait_for_job(
                args.base_url, args.topic, job_id, args.poll_seconds, args.timeout_seconds
            )
            job = completed_status.get("latest_job") or {}
            batch_record.update({
                "finished_at": _utc_now(),
                "job_status": job.get("status"),
                "job_error": job.get("error"),
                "slot_outcomes": {
                    slot_id: (completed_status["slots"].get(slot_id) or {}).get("status") for slot_id in slot_ids
                },
                "counts_after": status_counts(completed_status),
            })
            _write_receipt(receipt, receipt_path)
            if job.get("status") != "completed":
                receipt["status"] = "halted"
                receipt["halt_reason"] = f"batch {index} ended as {job.get('status')}"
                break
        else:
            receipt["status"] = "completed"
    except (RuntimeError, TimeoutError) as error:
        # A failed submission/wait is terminal for this run; the next invocation resumes from DB status.
        receipt["status"] = "halted"
        receipt["halt_reason"] = str(error)

    _, final_status = _snapshot(args.base_url, args.topic)
    receipt["finished_at"] = _utc_now()
    receipt["final_counts"] = status_counts(final_status)
    _write_receipt(receipt, receipt_path)
    return receipt_path, receipt


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Generate llmebm Topic content in bounded resumable batches.")
    parser.add_argument("--base-url", default="http://127.0.0.1:33300")
    parser.add_argument("--topic", default="atrial-fibrillation")
    parser.add_argument("--statuses", default="stale,failed", help=f"comma-separated: {','.join(ALLOWED_STATUSES)}")
    parser.add_argument("--slot-ids", help="comma-separated eligible slot IDs; never expands the selected statuses")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-batches", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--receipt")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.max_batches < 1:
        parser.error("--max-batches must be at least 1")
    if not 1 <= args.top_k <= 25:
        parser.error("--top-k must be between 1 and 25")
    if args.poll_seconds <= 0 or args.timeout_seconds <= 0:
        parser.error("poll/timeout seconds must be positive")
    build_batches([], args.batch_size)
    return args


def main(argv=None):
    receipt_path, receipt = run(parse_args(argv))
    print(json.dumps({
        "status": receipt["status"],
        "receipt": str(receipt_path.resolve()),
        "initial_counts": receipt["initial_counts"],
        "final_counts": receipt.get("final_counts"),
        "selected_slot_ids": receipt["selected_slot_ids"],
        "halt_reason": receipt.get("halt_reason"),
    }, ensure_ascii=False, indent=2))
    return 0 if receipt["status"] in {"completed", "dry_run", "unchanged"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
