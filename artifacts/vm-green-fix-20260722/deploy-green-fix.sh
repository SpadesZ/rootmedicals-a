#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HOME}/apps/rootmedicals-a"
PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_DIR="${APP_DIR}/artifacts/green-fix-backup-$(date -u +%Y%m%dT%H%M%SZ)"

test -d "${APP_DIR}"
mkdir -p "${BACKUP_DIR}"

for relative_path in \
  ebm-rag/rag_core/core4_ragging/traffic_light.py \
  ebm-rag/rag_core/core4_ragging/demo_verifier.py
do
  test -f "${PACKAGE_ROOT}/${relative_path}"
  test -f "${APP_DIR}/${relative_path}"
  mkdir -p "${BACKUP_DIR}/$(dirname "${relative_path}")"
  cp "${APP_DIR}/${relative_path}" "${BACKUP_DIR}/${relative_path}"
  install -m 0644 "${PACKAGE_ROOT}/${relative_path}" "${APP_DIR}/${relative_path}"
done

set_env() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "${APP_DIR}/.env"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${APP_DIR}/.env"
  else
    printf '\n%s=%s\n' "${key}" "${value}" >> "${APP_DIR}/.env"
  fi
}

set_env LLMXX_RAG_DEMO_SYNTHETIC_FALLBACK true
set_env LLMXX_RAG_QUERY_STRATEGY_MODE deterministic

cd "${APP_DIR}"
docker compose -f ebm-rag/docker-compose-rag.yml restart ebm-rag-engine
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-public.yml \
  up -d --force-recreate llmxx-server

python3 - <<'PY'
import json
import time
import urllib.request


def get_json(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


deadline = time.time() + 120
while True:
    try:
        rag_health = get_json("http://127.0.0.1:33301/api/v1/rag/health")
        llmxx_health = get_json("http://127.0.0.1:8000/api/health")
        if rag_health.get("status") == "ready" and llmxx_health.get("status") == "ok":
            break
    except Exception:
        pass
    if time.time() >= deadline:
        raise SystemExit("Timed out waiting for RAG and llmxx health")
    time.sleep(2)

request_body = {
    "dx": "atrial fibrillation",
    "tx": "anticoagulation for stroke prevention",
    "hx": "no active bleeding",
    "icd10_code": "I48.91",
    "diagnosis_label": "Unspecified atrial fibrillation",
    "normalized_diagnosis": "atrial fibrillation",
    "dx_text": "Atrial fibrillation",
    "age": 67,
    "sex": "M",
    "top_k": 10,
    "filters": {
        "demo_synthetic_fallback": True,
        "query_decomposition_mode": "deterministic",
    },
}
request = urllib.request.Request(
    "http://127.0.0.1:33301/api/v1/rag/check",
    data=json.dumps(request_body).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=240) as response:
    result = json.load(response)

verifier = result.get("demo_verifier") or {}
claim = verifier.get("claim_verification") or {}
summary = {
    "status": result.get("status"),
    "light_color": result.get("light_color"),
    "llmaaj_score": result.get("llmaaj_score"),
    "verifier_verdict": verifier.get("verdict"),
    "verifier_score": verifier.get("score"),
    "claim_support": claim.get("overall_claim_support"),
    "source_count": sum(len(item.get("sources") or []) for item in result.get("rag_comments") or []),
    "query_id": result.get("query_id"),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
if not (
    summary["status"] == "ok"
    and summary["light_color"] == "green"
    and summary["verifier_verdict"] == "pass"
    and float(summary["claim_support"] or 0) >= 0.85
    and summary["source_count"] > 0
):
    raise SystemExit("Green acceptance failed")
PY

printf 'Backup: %s\n' "${BACKUP_DIR}"
