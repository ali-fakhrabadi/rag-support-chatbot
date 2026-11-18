# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# Pick up Debian security fixes published after the base image was built.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

FROM base AS deps
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --index-url "$PIP_INDEX_URL" --require-hashes -r /tmp/requirements.txt

# Development and test image: adds pytest, ruff, mypy, pip-audit. Source is mounted at /app.
FROM deps AS dev
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN /opt/venv/bin/pip install --index-url "$PIP_INDEX_URL" --require-hashes -r /tmp/requirements-dev.txt
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /app

FROM base AS runtime
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
ENV PATH=/opt/venv/bin:$PATH
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-server-header"]
