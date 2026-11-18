#!/bin/sh
# Generate local secrets once. Files are git-ignored and mounted as Docker secrets.
set -eu
cd "$(dirname "$0")/.."
mkdir -p secrets
# The directory is private to the owner; the files are world-readable so the non-root users in
# the containers (uid 10001, postgres) can read their bind-mounted copies. Nobody else on the host
# can reach them through the 0700 directory.
chmod 700 secrets
umask 022
for name in db_owner_password db_app_password jwt_secret demo_password; do
    if [ ! -s "secrets/$name" ]; then
        # 48 random bytes, base64url without padding: no quoting problems in URLs or SQL.
        openssl rand -base64 48 | tr '+/' '-_' | tr -d '=\n' > "secrets/$name"
        echo "created secrets/$name"
    fi
done
chmod 644 secrets/*
if [ ! -s secrets/openai_api_key ]; then
    : > secrets/openai_api_key
    echo "created empty secrets/openai_api_key (only needed with the OpenAI provider)"
fi
