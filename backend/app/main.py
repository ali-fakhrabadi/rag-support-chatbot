"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.api.v1.routes import router as v1
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.ratelimit import get_redis
from app.db.models import EMBEDDING_DIM
from app.db.session import get_engine

log = logging.getLogger("rag.http")
_DOC_PATHS = ("/api/docs", "/api/openapi.json", "/api/redoc")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.embedding_dim != EMBEDDING_DIM:
        raise RuntimeError(
            f"RAG_EMBEDDING_DIM={settings.embedding_dim} but the database column has "
            f"{EMBEDDING_DIM} dimensions; see docs/rag-pipeline.md (changing embedding models)"
        )

    app = FastAPI(
        title="Secure RAG Knowledge Assistant",
        version=__version__,
        description=(
            "Ask questions about your organisation's documents and get answers that cite "
            "their sources. All data is scoped to the caller's workspace."
        ),
        docs_url="/api/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.enable_docs else None,
    )
    install_error_handlers(app)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def _request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if not request.url.path.startswith(_DOC_PATHS):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'"
            )
            response.headers["Cache-Control"] = "no-store"
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "tenant_id": getattr(request.state, "tenant_id", None),
            },
        )
        return response

    @app.get("/api/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness: the process is up."""
        return {"status": "ok", "version": __version__}

    @app.get("/api/ready", tags=["ops"])
    def ready() -> JSONResponse:
        """Readiness: database (with pgvector) and Redis respond."""
        checks: dict[str, str] = {}
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
                version = conn.scalar(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
            checks["database"] = "ok" if version else "pgvector missing"
        except Exception as exc:
            checks["database"] = f"error: {type(exc).__name__}"
        try:
            get_redis().ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            {"status": "ok" if ok else "unavailable", "checks": checks},
            status_code=200 if ok else 503,
        )

    app.include_router(v1)
    return app


app = create_app()
