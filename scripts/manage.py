"""Scholaria administration commands (run from the project root).

    .venv/bin/python scripts/manage.py list-users
    .venv/bin/python scripts/manage.py reset-password you@example.com

Accounts live only on this computer, so there is no email-based password reset;
use this command instead. It also signs the account out of every browser.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import delete, select  # noqa: E402

from app.core.errors import ValidationFailed  # noqa: E402
from app.database.connection import get_sessionmaker, run_migrations  # noqa: E402
from app.database.models import AuthSession, User  # noqa: E402
from app.services.auth_service import hash_password, normalize_email, validate_password  # noqa: E402


def list_users() -> int:
    with get_sessionmaker()() as db:
        users = db.scalars(select(User).order_by(User.id)).all()
        if not users:
            print("No accounts yet. Create one on the sign-in page.")
        for u in users:
            last = u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "never"
            print(f"{u.id:>3}  {u.email:<35} {u.full_name:<25} last login: {last}")
    return 0


def reset_password(email: str) -> int:
    try:
        email = normalize_email(email)
    except ValidationFailed as exc:
        print(exc.message)
        return 1
    with get_sessionmaker()() as db:
        user = db.scalars(select(User).where(User.email == email)).first()
        if user is None:
            print(f"No account found for {email}.")
            return 1
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat new password: "):
            print("The passwords do not match.")
            return 1
        try:
            validate_password(password)
        except ValidationFailed as exc:
            print(exc.message)
            return 1
        user.password_hash = hash_password(password)
        db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
        db.commit()
    print(f"Password updated for {email}. All existing sessions were signed out.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scholaria administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-users", help="List local accounts")
    reset = sub.add_parser("reset-password", help="Set a new password for an account")
    reset.add_argument("email")
    args = parser.parse_args()
    run_migrations()
    if args.command == "list-users":
        return list_users()
    return reset_password(args.email)


if __name__ == "__main__":
    raise SystemExit(main())
