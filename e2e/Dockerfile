# syntax=docker/dockerfile:1
# Browser tests against a running deployment. The base image ships the browsers for the pinned
# Playwright version; the Python packages are installed from a hash-checked lock file.
FROM mcr.microsoft.com/playwright/python@sha256:330d401ae23c905896a0c26c66c36fc971b621519e7b11b435a8328d7bf6e031
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --break-system-packages --require-hashes \
      --index-url "$PIP_INDEX_URL" -r /tmp/requirements.txt
WORKDIR /e2e
USER pwuser
