"""Local accounts: password hashing, sign-up, login sessions and logout.

* Passwords are hashed with scrypt (memory-hard KDF from the standard library)
  using a random 16-byte salt per password.
* Login returns a random bearer token; the database stores only its SHA-256
  hash, so a copied database cannot be used to log in.
* Repeated failed logins for an email are throttled in memory.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, ValidationFailed
from app.core.logging import get_logger
from app.database.models import AuthSession, Conversation, Dataset, Document, Project, User, utcnow

logger = get_logger("auth")

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SESSION_HOURS = 12
REMEMBER_DAYS = 30
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")

# email -> (failure count, locked-until monotonic time)
_failures: dict[str, tuple[int, float]] = {}


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class TooManyAttempts(AppError):
    status_code = 429
    code = "too_many_attempts"


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r),
                                p=int(p), dklen=len(expected))
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValidationFailed("Password must be at least 8 characters long.")
    if len(password) > 128:
        raise ValidationFailed("Password must be at most 128 characters long.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValidationFailed("Password must contain at least one letter and one number.")


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValidationFailed("Please enter a valid email address.")
    return email


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Accounts and sessions
# --------------------------------------------------------------------------- #
def signup(db: Session, email: str, full_name: str, password: str) -> User:
    email = normalize_email(email)
    full_name = full_name.strip()
    if not full_name:
        raise ValidationFailed("Please enter your name.")
    validate_password(password)
    if db.scalars(select(User).where(User.email == email)).first() is not None:
        raise ConflictError("An account with this email already exists. Please log in.")
    is_first_user = (db.scalar(select(func.count()).select_from(User)) or 0) == 0
    user = User(email=email, full_name=full_name[:120], password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    if is_first_user:
        # Data created before accounts existed belongs to the first account.
        for model in (Project, Conversation, Document, Dataset):
            db.execute(update(model).where(model.user_id.is_(None)).values(user_id=user.id))
        db.commit()
    logger.info("Account %s created", user.id)
    return user


def _check_throttle(email: str) -> None:
    count, until = _failures.get(email, (0, 0.0))
    if until and time.monotonic() < until:
        wait = int(until - time.monotonic()) + 1
        raise TooManyAttempts(f"Too many failed attempts. Try again in {wait} seconds.")


def _record_failure(email: str) -> None:
    count, _ = _failures.get(email, (0, 0.0))
    count += 1
    until = time.monotonic() + LOCKOUT_SECONDS if count >= MAX_FAILURES else 0.0
    _failures[email] = (0 if until else count, until)


def reset_throttle() -> None:
    _failures.clear()


def login(db: Session, email: str, password: str, remember: bool = False) -> tuple[User, str, datetime]:
    try:
        email = normalize_email(email)
    except ValidationFailed:
        raise AuthError("Incorrect email or password.") from None
    _check_throttle(email)
    user = db.scalars(select(User).where(User.email == email)).first()
    # Verify against a dummy hash when the user does not exist to keep timing similar.
    stored = user.password_hash if user else hash_password(secrets.token_hex(8))
    if not verify_password(password, stored) or user is None:
        _record_failure(email)
        raise AuthError("Incorrect email or password.")
    _failures.pop(email, None)
    token = secrets.token_urlsafe(32)
    expires = utcnow() + (timedelta(days=REMEMBER_DAYS) if remember else timedelta(hours=SESSION_HOURS))
    db.add(AuthSession(user_id=user.id, token_hash=_token_hash(token), expires_at=expires))
    user.last_login_at = utcnow()
    # Housekeeping: drop this user's expired sessions.
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.expires_at < utcnow()))
    db.commit()
    logger.info("User %s logged in", user.id)
    return user, token, expires


def user_for_token(db: Session, token: str | None) -> User:
    if not token:
        raise AuthError("Please log in to continue.")
    session = db.scalars(select(AuthSession).where(AuthSession.token_hash == _token_hash(token))).first()
    if session is None or session.expires_at < utcnow():
        raise AuthError("Your session has expired. Please log in again.")
    now = utcnow()
    if session.last_used_at is None or (now - session.last_used_at).total_seconds() > 300:
        session.last_used_at = now
        db.commit()
    return session.user


def logout(db: Session, token: str) -> None:
    db.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    db.commit()


def change_password(db: Session, user: User, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        raise ValidationFailed("Current password is incorrect.")
    validate_password(new)
    user.password_hash = hash_password(new)
    db.commit()
