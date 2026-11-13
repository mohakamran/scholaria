"""FastAPI application entrypoint.

Run (from the project root):
    uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import auth, chat, datasets, documents, health, models, research, writing
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.database.connection import get_sessionmaker, run_migrations
from app.services.system_service import seed_defaults

logger = get_logger("main")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    settings.ensure_directories()
    run_migrations()
    with get_sessionmaker()() as db:
        seed_defaults(db)
    logger.info("%s %s started", settings.app_name, settings.app_version)
    yield
    logger.info("Shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Scholaria — local-first AI research assistant API. All inference runs on the local Ollama server.",
        lifespan=lifespan,
    )
    # Only the local Streamlit frontend may call the API from a browser.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8501", "http://localhost:8501"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in err.get("loc", []) if p != "body"), "message": err.get("msg")}
            for err in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"error": {
            "code": "validation_failed", "message": "; ".join(f"{d['field']}: {d['message']}" for d in details),
            "details": details}})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": "http_error", "message": str(exc.detail)}})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error: %s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal_error", "message": "An unexpected error occurred. See storage/logs/backend.log."}})

    for router in (health.router, auth.router, models.router, chat.router, documents.router, research.router,
                   datasets.router, writing.router):
        app.include_router(router, prefix="/api")
    return app


app = create_app()
