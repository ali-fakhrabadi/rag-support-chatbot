#!/usr/bin/env bash
# Create the two evaluation workspaces and load the corpus into the running Compose deployment.
#
#   evals/seed.sh
#
# The shared password of the evaluation accounts is generated once into secrets/eval_password
# (git-ignored). The script talks to the deployment through the web container, like a browser.
set -euo pipefail
cd "$(dirname "$0")/.."

PASSWORD_FILE=secrets/eval_password
NETWORK=${EVAL_NETWORK:-rag-support-chatbot_egress}
PYTHON_IMAGE=${EVAL_PYTHON_IMAGE:-python:3.12.15-slim-trixie}

if [ ! -s "$PASSWORD_FILE" ]; then
  (umask 077 && openssl rand -base64 24 | tr -d '\n' > "$PASSWORD_FILE")
fi

python3 - <<'EOF' > /tmp/rag-eval-tenants
import json
for t in json.load(open("evals/corpus/manifest.json"))["tenants"]:
    admin = next(u for u in t["users"] if u["role"] == "tenant_admin")
    print("|".join([t["slug"], t["name"], admin["email"], admin["full_name"]]))
EOF

# stdin is redirected so `docker compose exec` cannot swallow the rest of the tenant list.
while IFS='|' read -r slug name email full_name; do
  if ! output=$(docker compose exec -T -e RAG_CLI_PASSWORD="$(cat "$PASSWORD_FILE")" api \
      python -m app.cli create-workspace --slug "$slug" --name "$name" \
        --admin-email "$email" --full-name "$full_name" 2>&1 </dev/null); then
    if grep -q "already exists" <<<"$output"; then
      echo "workspace $slug already exists"
    else
      echo "$output" >&2
      exit 1
    fi
  else
    echo "$output"
  fi
done < /tmp/rag-eval-tenants

docker run --rm --network "$NETWORK" \
  -v "$PWD/evals:/evals:ro" -v "$PWD/$PASSWORD_FILE:/run/secrets/eval_password:ro" \
  "$PYTHON_IMAGE" python /evals/seed.py --base-url http://web:8080 \
    --password-file /run/secrets/eval_password "$@"
