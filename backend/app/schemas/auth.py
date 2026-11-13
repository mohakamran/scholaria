"""Schemas for accounts and login."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class SignupRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    full_name: str = Field(..., min_length=1, max_length=120)
    password: str = Field(..., min_length=1, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)
    remember: bool = False


class UserOut(ORMModel):
    id: int
    email: str
    full_name: str
    theme: str
    created_at: datetime
    last_login_at: datetime | None


class LoginResponse(BaseModel):
    token: str
    expires_at: datetime
    user: UserOut


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=120)
    theme: Literal["light", "dark"] | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=1, max_length=128)
