"""Reusable UI helpers shared by all pages."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

import streamlit as st

from components import api_client as api

T = TypeVar("T")

BASE_CSS = """
<style>
.block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1180px;}
h1 {font-weight: 700; letter-spacing: -0.02em;}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {padding-top: 0.5rem;}
[data-testid="stSidebar"] .stButton button {justify-content: flex-start; text-align: left;}
[data-testid="stSidebar"] .stButton button > div {justify-content: flex-start; width: 100%;}
[data-testid="stSidebar"] .stButton button p {white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: left;}
[data-testid="stSidebar"] [data-testid="stBaseButton-tertiary"] {padding: 0.3rem 0.55rem; border-radius: 0.5rem;}
[data-testid="stSidebar"] [data-testid="stBaseButton-tertiary"]:hover {background: rgba(99, 102, 241, 0.10);}
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {font-weight: 600;}
[data-testid="stSidebar"] [data-testid="stPopoverButton"] div[aria-hidden="true"] {display: none;}
[data-testid="stSidebar"] [data-testid="stPopoverButton"] {padding: 0.3rem; min-height: 0;}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] [data-testid="stVerticalBlock"] {gap: 0.35rem;}
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {gap: 0.25rem;}
.sch-subtitle {opacity: 0.72; margin-top: -0.6rem; margin-bottom: 1.2rem; font-size: 1.02rem;}
.sch-muted {opacity: 0.65; font-size: 0.85rem;}
.ra-muted {opacity: 0.65; font-size: 0.85rem;}
.sch-user {display: flex; gap: 0.6rem; align-items: center; margin: 0.2rem 0 0.6rem 0; line-height: 1.2;}
.sch-user small {opacity: 0.65;}
.sch-avatar {display: inline-flex; align-items: center; justify-content: center; width: 2.1rem; height: 2.1rem;
             border-radius: 50%; background: linear-gradient(135deg, #4F46E5, #0E7490); color: #fff;
             font-weight: 700; font-size: 0.85rem; flex-shrink: 0;}
.sch-stat {font-size: 1.9rem; font-weight: 700; line-height: 1.1;}
.sch-stat-label {opacity: 0.65; font-size: 0.85rem;}
</style>
"""


def apply_base_style() -> None:
    st.markdown(BASE_CSS, unsafe_allow_html=True)


def call(fn: Callable[..., T], *args: Any, error_prefix: str = "", **kwargs: Any) -> T | None:
    """Run an API call and show a friendly error instead of a traceback."""
    try:
        return fn(*args, **kwargs)
    except api.ApiError as exc:
        if exc.code == "unauthorized":
            raise  # handled by app.py: returns to the sign-in page
        show_error(exc, error_prefix)
        return None


def show_error(exc: api.ApiError, prefix: str = "") -> None:
    hints = {
        "ollama_unavailable": "Start Ollama with `ollama serve` (or open the Ollama app), then retry.",
        "model_not_found": "Install a model, e.g. `ollama pull qwen3:4b-instruct-2507-q4_K_M`, or pick another model in Settings.",
        "insufficient_memory": "Close memory-heavy apps, lower the context length, or switch to `qwen3:1.7b`.",
        "api_unavailable": "Run `./scripts/start.sh` from the project folder.",
    }
    message = f"{prefix}{exc.message}"
    st.error(message, icon=":material/error:")
    if exc.code in hints:
        st.caption(hints[exc.code])


def fmt_time(value: str | None) -> str:
    """Format a UTC ISO timestamp from the API in local time."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", ""))
        dt = dt.replace(tzinfo=timezone.utc).astimezone()
        return dt.strftime("%d %b %Y, %H:%M")
    except ValueError:
        return value


def fmt_size(num: int | None) -> str:
    if not num:
        return "0 B"
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def project_options(include_none: bool = True) -> dict[str, int | None]:
    projects = api.safe_get("/projects", default=[]) or []
    options: dict[str, int | None] = {"— No project —": None} if include_none else {}
    for p in projects:
        options[f"{p['title']} (#{p['id']})"] = p["id"]
    return options


def project_selector(label: str = "Project", key: str = "project_select", include_none: bool = True,
                     default_id: int | None = None) -> int | None:
    options = project_options(include_none)
    labels = list(options)
    index = 0
    if default_id is not None:
        for i, name in enumerate(labels):
            if options[name] == default_id:
                index = i
    if not labels:
        return None
    choice = st.selectbox(label, labels, index=index, key=key)
    return options[choice]


def render_sources(sources: list[dict[str, Any]] | None, expanded: bool = False) -> None:
    """Show source references. Page numbers come from the database, never the model."""
    if not sources:
        return
    with st.expander(f"Sources ({len(sources)})", expanded=expanded, icon=":material/menu_book:"):
        for src in sources:
            page = f"p. {src['page_number']}" if src.get("page_number") else "no page numbers"
            st.markdown(f"**[{src['label']}] {src['document_title']}** — {page}, chunk {src['chunk_index']}")
            st.caption(src.get("excerpt", "")[:300] + ("…" if len(src.get("excerpt", "")) >= 300 else ""))


def copy_box(text: str, key: str) -> None:
    """A popover whose code block has Streamlit's built-in copy button."""
    with st.popover("Copy", icon=":material/content_copy:"):
        st.caption("Use the copy icon in the top-right of the box.")
        st.code(text, language="markdown", wrap_lines=True)


def page_header(title: str, subtitle: str) -> None:
    """Consistent page title with a short explanatory subtitle."""
    st.title(title)
    st.markdown(f"<div class='sch-subtitle'>{subtitle}</div>", unsafe_allow_html=True)
