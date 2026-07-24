#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HOME}/apps/rootmedicals-a"
PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RELATIVE_PATH="llmxx-server/server_app/ocr/server_ocr.py"
SOURCE_FILE="${PACKAGE_ROOT}/${RELATIVE_PATH}"
TARGET_FILE="${APP_DIR}/${RELATIVE_PATH}"
BACKUP_DIR="${APP_DIR}/artifacts/ocr-persistence-backup-$(date -u +%Y%m%dT%H%M%SZ)"

test -d "${APP_DIR}"
test -f "${SOURCE_FILE}"
test -f "${TARGET_FILE}"

mkdir -p "${BACKUP_DIR}/$(dirname "${RELATIVE_PATH}")"
cp "${TARGET_FILE}" "${BACKUP_DIR}/${RELATIVE_PATH}"
install -m 0644 "${SOURCE_FILE}" "${TARGET_FILE}"
python3 -m py_compile "${TARGET_FILE}"

cd "${APP_DIR}"
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-public.yml \
  up -d --build --force-recreate llmxx-server

python3 - <<'PY'
import base64
import json
import time
import urllib.request


def get_json(url: str, timeout: int = 20):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


deadline = time.time() + 180
while True:
    try:
        health = get_json("http://127.0.0.1:8000/api/health")
        if health.get("status") == "ok":
            break
    except Exception:
        pass
    if time.time() >= deadline:
        raise SystemExit("Timed out waiting for llmxx-server")
    time.sleep(2)

payload = {
    "schema_version": "llmxx-client-screenshot.v0.1",
    "session_id": f"vm-sidecar-green-{int(time.time())}",
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "source": "vm-persistent-sidecar-acceptance",
    "input_origin": "clinical_guard_standalone",
    "zero_disk_image_io": True,
    "patient_uid": "[REDACTED]",
    "window": {"title": "Clinical Guard (Phase 1 Local)", "width": 1, "height": 1},
    "screenshot": {
        "format": "png",
        "image_b64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        "width": 1,
        "height": 1,
    },
    "layout_regions": [],
    "clinical_metadata": {
        "icd_code": "I48.91",
        "icd10_code": "I48.91",
        "diagnosis_label": "Unspecified atrial fibrillation",
        "normalized_diagnosis": "atrial fibrillation",
        "dx_text": "Atrial fibrillation",
        "soap": {
            "S": "Palpitations and dizziness",
            "O": "Irregular pulse, heart rate 118, no active bleeding",
            "A": "Atrial fibrillation",
            "P": "Anticoagulation for stroke prevention",
        },
        "vital_signs": {"bp": "128/76", "hr": "118", "temp": "36.7", "rr": "18", "spo2": "98"},
        "metadata_source": "clinicalguard_standalone",
    },
}
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/intake",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=300) as response:
    result = json.load(response)

gate = result.get("final_gate") or {}
summary = {
    "status": result.get("status"),
    "light_color": gate.get("light_color"),
    "evidence_backed": gate.get("evidence_backed"),
    "error_code": result.get("error_code"),
    "client_session_id": result.get("client_session_id"),
    "server_session_id": result.get("session_id"),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
if not (
    summary["status"] == "completed"
    and summary["light_color"] == "green"
    and summary["evidence_backed"] is True
    and not summary["error_code"]
):
    raise SystemExit("Persistent screenshot-sidecar green acceptance failed")
PY

printf 'Persistent source updated and image rebuilt.\n'
printf 'Backup: %s\n' "${BACKUP_DIR}"
