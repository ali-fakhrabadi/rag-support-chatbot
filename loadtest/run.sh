#!/usr/bin/env bash
# Run a k6 script against the Compose deployment from a container on its network.
#
#   loadtest/run.sh search.js -e VUS=10 -e DURATION=60s
#   loadtest/run.sh chat.js -e VUS=3 -e ITERATIONS=6
#
# Uses the evaluation member account (evals/seed.sh) and writes a JSON summary to loadtest/out.
set -euo pipefail
cd "$(dirname "$0")/.."
script=$1
shift
mkdir -p loadtest/out
status=0
docker run --rm --network "${LOADTEST_NETWORK:-rag-support-chatbot_egress}" \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/loadtest:/scripts:ro" -v "$PWD/loadtest/out:/out" \
  -v "$PWD/secrets/eval_password:/run/secrets/eval_password:ro" \
  "${K6_IMAGE:-grafana/k6:2.3.0}" run --quiet \
  -e BASE_URL=http://web:8080 -e EMAIL=agent@larkspur.example -e WORKSPACE=larkspur \
  -e PASSWORD_FILE=/run/secrets/eval_password \
  --summary-export "/out/${script%.js}-summary.json" "$@" "/scripts/$script" || status=$?

# k6 also exports whatever setup() returned, here the access token: remove it from the summary.
python3 - "loadtest/out/${script%.js}-summary.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    summary = json.load(f)
summary.pop("setup_data", None)
with open(path, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)
PY
exit "$status"
