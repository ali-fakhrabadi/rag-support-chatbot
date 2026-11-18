#!/bin/sh
# Regenerate hash-pinned lock files inside the Python base image, so the lock matches the
# runtime's Python version and platform:
#
#   docker run --rm -u "$(id -u)" -e HOME=/tmp -v "$PWD:/repo" -w /repo/backend \
#     python:3.12.15-slim-trixie sh ../scripts/lock.sh
#
# Set PIP_INDEX_URL to use a mirror; the hashes in the lock files are checked either way.
set -eu
INDEX="${PIP_INDEX_URL:-https://pypi.org/simple}"
pip install --quiet --index-url "$INDEX" "uv==0.13.7"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"
python -m uv pip compile requirements.in --generate-hashes --python-version 3.12 \
    --python-platform x86_64-unknown-linux-gnu --index-url "$INDEX" --no-header \
    -o requirements.txt
python -m uv pip compile requirements-dev.in --generate-hashes --python-version 3.12 \
    --python-platform x86_64-unknown-linux-gnu --index-url "$INDEX" --no-header \
    -o requirements-dev.txt
echo "lock files updated"
