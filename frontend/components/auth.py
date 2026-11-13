"""Login state, the session cookie and the light/dark theme.

The bearer token lives in ``st.session_state`` and in a ``SameSite=Strict``
cookie on 127.0.0.1, so a page reload keeps you logged in. "Remember me" keeps
the cookie for 30 days; otherwise it is removed when the browser closes.
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from components import api_client as api

COOKIE = "scholaria_session"
REMEMBER_SECONDS = 30 * 24 * 3600

ss = st.session_state


def current_user() -> dict[str, Any] | None:
    return ss.get("auth_user")


def _queue_cookie(value: str | None, max_age: int | None) -> None:
    ss["_cookie_op"] = {"value": value, "max_age": max_age}


def flush_cookie_ops() -> None:
    """Write or clear the session cookie in the browser (runs a tiny script once)."""
    op = ss.pop("_cookie_op", None)
    if op is None:
        return
    if op["value"] is None:
        cookie = f"{COOKIE}=; path=/; max-age=0; SameSite=Strict"
    else:
        age = f"; max-age={op['max_age']}" if op["max_age"] else ""
        cookie = f"{COOKIE}={op['value']}; path=/; SameSite=Strict{age}"
    st.html(f"<script>document.cookie = {json.dumps(cookie)};</script>", unsafe_allow_javascript=True)


def restore_session() -> None:
    """Re-establish a login from the cookie after a page reload."""
    if current_user() or ss.get("_logged_out"):
        return
    token = st.context.cookies.get(COOKIE)
    if not token:
        return
    ss["auth_token"] = token
    try:
        ss["auth_user"] = api.get("/auth/me")
    except api.ApiError:
        ss.pop("auth_token", None)
        _queue_cookie(None, None)


def complete_login(token: str, user: dict[str, Any], remember: bool) -> None:
    ss["auth_token"] = token
    ss["auth_user"] = user
    ss.pop("_logged_out", None)
    _queue_cookie(token, REMEMBER_SECONDS if remember else None)


def logout() -> None:
    try:
        api.post("/auth/logout")
    except api.ApiError:
        pass
    end_session()


def end_session() -> None:
    """Forget the login locally (also used when the server reports an expired session)."""
    for key in list(ss.keys()):
        if not key.startswith("_pages"):
            del ss[key]
    ss["_logged_out"] = True
    _queue_cookie(None, None)


# --------------------------------------------------------------------------- #
# Theme
# --------------------------------------------------------------------------- #
def active_theme() -> str:
    user = current_user()
    if user and user.get("theme") in {"light", "dark"}:
        return user["theme"]
    return "dark" if st._config.get_option("theme.base") == "dark" else "light"


def apply_theme() -> None:
    """Switch Streamlit's theme to the user's preference (reruns once if it changed)."""
    if not current_user():
        return
    theme = active_theme()
    if st._config.get_option("theme.base") != theme:
        st._config.set_option("theme.base", theme)
        st.rerun()


def set_theme(theme: str) -> None:
    user = current_user()
    if not user or user.get("theme") == theme:
        return
    try:
        ss["auth_user"] = api.patch("/auth/me", json={"theme": theme})
    except api.ApiError:
        ss["auth_user"] = {**user, "theme": theme}
