#!/bin/sh
# Regenerate e2e/requirements.txt (hash-pinned) inside the Python base image:
#   docker run --rm -v "$PWD/e2e:/src" -w /src python:3.12.15-slim-trixie sh lock.sh
set -eu
INDEX="${PIP_INDEX_URL:-https://pypi.org/simple}"
pip install --quiet --index-url "$INDEX" "uv==0.13.7"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"
python -m uv pip compile requirements.in --generate-hashes --python-version 3.12 \
    --python-platform x86_64-unknown-linux-gnu --index-url "$INDEX" --no-header \
    -o requirements.txt
