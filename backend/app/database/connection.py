"""SQLite engine/session management, FTS5 setup and migrations bootstrap."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import PROJECT_ROOT, get_settings
from app.core.logging import get_logger

logger = get_logger("database")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None

# FTS5 index over document_chunks (external-content table kept in sync by triggers).
FTS_STATEMENTS: list[str] = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
        content, content='document_chunks', content_rowid='id', tokenize='porter unicode61'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS document_chunks_ai AFTER INSERT ON document_chunks BEGIN
        INSERT INTO document_chunks_fts(rowid, content) VALUES (new.id, new.content);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS document_chunks_ad AFTER DELETE ON document_chunks BEGIN
        INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS document_chunks_au AFTER UPDATE ON document_chunks BEGIN
        INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
        INSERT INTO document_chunks_fts(rowid, content) VALUES (new.id, new.content);
    END
    """,
]


def _set_sqlite_pragmas(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        settings.ensure_directories()
        _engine = create_engine(
            settings.db_url,
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
        )
        if _engine.dialect.name == "sqlite":
            event.listen(_engine, "connect", _set_sqlite_pragmas)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def reset_engine() -> None:
    """Dispose the engine (used by tests after changing settings)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for code running outside a request (e.g. streaming)."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def fts_available(engine: Engine | None = None) -> bool:
    engine = engine or get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='document_chunks_fts'")
        ).first()
        return row is not None


def run_migrations() -> None:
    """Apply Alembic migrations up to ``head`` using the configured database URL."""
    from alembic import command
    from alembic.config import Config

    backend_dir = PROJECT_ROOT / "backend"
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().db_url)
    cfg.attributes["skip_logging"] = True  # keep the application's logging configuration
    command.upgrade(cfg, "head")


def check_database() -> dict[str, object]:
    """Lightweight health check that does not leak filesystem paths."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            integrity = conn.execute(text("PRAGMA quick_check")).scalar()
        url = get_settings().db_url
        size = None
        if url.startswith("sqlite:///"):
            path = Path(url.removeprefix("sqlite:///"))
            size = path.stat().st_size if path.exists() else 0
        return {
            "ok": integrity == "ok",
            "engine": "SQLite",
            "file": Path(url.removeprefix("sqlite:///")).name if size is not None else None,
            "size_bytes": size,
            "fts5": fts_available(engine),
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Database health check failed: %s", type(exc).__name__)
        return {"ok": False, "engine": "SQLite", "error": type(exc).__name__}
