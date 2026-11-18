#!/usr/bin/env bash
# Build the e2e image and run the browser tests against the running Compose deployment.
#
#   e2e/run.sh                    # everything (needs the models; slow on CPU)
#   e2e/run.sh -m "not models"    # UI, permissions and isolation checks only
#
# Requires evals/seed.sh to have created the evaluation workspaces, and a `demo` workspace whose
# admin password is in secrets/demo_password. Screenshots are written to e2e/out/screenshots.
set -euo pipefail
cd "$(dirname "$0")/.."

docker build -q -t rag-support-chatbot-e2e \
  --build-arg PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}" e2e >/dev/null
mkdir -p e2e/out
docker run --rm --network "${E2E_NETWORK:-rag-support-chatbot_egress}" --ipc=host \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD/e2e:/e2e:ro" -v "$PWD/e2e/out:/out" \
  -v "$PWD/secrets/eval_password:/run/secrets/eval_password:ro" \
  -v "$PWD/secrets/demo_password:/run/secrets/demo_password:ro" \
  rag-support-chatbot-e2e python -m pytest -p no:cacheprovider -v "$@"
