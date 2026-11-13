"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import User
from app.services.auth_service import user_for_token


def bearer_token(authorization: str | None = Header(default=None)) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def current_user(token: str | None = Depends(bearer_token), db: Session = Depends(get_db)) -> User:
    """Resolve the logged-in user or raise 401."""
    return user_for_token(db, token)
