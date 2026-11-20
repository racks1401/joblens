"""FastAPI app: router registration, request-id middleware, startup checks."""

from __future__ import annotations

import importlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app.api.errors import install_error_handlers
from app.db import db_status
from app.logs import log_event, request_id_var, setup_logging

log = logging.getLogger("joblens.api")

# Routers are registered if their module exists (they are added phase by phase).
ROUTERS = [
    "app.api.routes_health",
    "app.api.routes_jobs",
    "app.api.routes_profiles",
    "app.api.routes_search",
    "app.api.routes_matches",
    "app.api.routes_chat",
    "app.api.routes_evals",
    "app.api.limits",
]


def create_app() -> FastAPI:
    """Build the app."""
    setup_logging()
    app = FastAPI(title="JobLens API", version="0.1.0")
    install_error_handlers(app)
    for path in ROUTERS:
        try:
            mod = importlib.import_module(path)
        except ModuleNotFoundError as e:
            if e.name != path:
                raise
            continue
        app.include_router(mod.router)

    @app.middleware("http")
    async def request_id(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        try:
            resp = await call_next(request)
        finally:
            request_id_var.reset(token)
        resp.headers["x-request-id"] = rid
        log_event(log, "request", request_id=rid, method=request.method, path=request.url.path,
                  status=resp.status_code, ms=round((time.perf_counter() - t0) * 1000, 1))
        return resp

    @app.on_event("startup")
    def _startup() -> None:
        st = db_status()
        log_event(log, "startup", db_ok=st["ok"], pgvector=st["pgvector"])

    return app


app = create_app()
