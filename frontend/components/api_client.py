"""Thin synchronous client for the local FastAPI backend.

The frontend never talks to Ollama or the database directly.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx
import streamlit as st

API_URL = os.environ.get("RA_API_URL", "http://127.0.0.1:8000").rstrip("/")
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
LONG_TIMEOUT = httpx.Timeout(1800.0, connect=5.0)  # summaries of long papers on a small model


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None,
                 details: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details


def _headers() -> dict[str, str]:
    """Attach the logged-in user's bearer token (kept only in this browser session)."""
    token = st.session_state.get("auth_token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _raise_for(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    try:
        err = response.json().get("error", {})
        raise ApiError(err.get("message") or response.text, response.status_code, err.get("code"), err.get("details"))
    except (json.JSONDecodeError, AttributeError):
        raise ApiError(response.text or f"HTTP {response.status_code}", response.status_code) from None


def request(method: str, path: str, *, long: bool = False, **kwargs: Any) -> Any:
    try:
        with httpx.Client(base_url=API_URL, timeout=LONG_TIMEOUT if long else DEFAULT_TIMEOUT) as client:
            response = client.request(method, f"/api{path}", headers=_headers(), **kwargs)
    except httpx.ConnectError as exc:
        raise ApiError("Cannot reach the backend API at 127.0.0.1:8000. Is it running? (./scripts/start.sh)",
                       code="api_unavailable") from exc
    except httpx.TimeoutException as exc:
        raise ApiError("The request timed out. The model may still be busy; try again shortly.",
                       code="timeout") from exc
    _raise_for(response)
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    return response.content


def get(path: str, **kwargs: Any) -> Any:
    return request("GET", path, **kwargs)


def post(path: str, **kwargs: Any) -> Any:
    return request("POST", path, **kwargs)


def patch(path: str, **kwargs: Any) -> Any:
    return request("PATCH", path, **kwargs)


def put(path: str, **kwargs: Any) -> Any:
    return request("PUT", path, **kwargs)


def delete(path: str, **kwargs: Any) -> Any:
    return request("DELETE", path, **kwargs)


def stream_sse(path: str, payload: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """POST and yield (event, data) tuples from a server-sent-event response."""
    try:
        with httpx.Client(base_url=API_URL, timeout=httpx.Timeout(600.0, connect=5.0)) as client:
            with client.stream("POST", f"/api{path}", json=payload, headers=_headers()) as response:
                if response.status_code >= 400:
                    response.read()
                    _raise_for(response)
                event = "message"
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        event = line[7:].strip()
                    elif line.startswith("data: "):
                        yield event, json.loads(line[6:])
                    elif not line:
                        event = "message"
    except httpx.ConnectError as exc:
        raise ApiError("Cannot reach the backend API. Is it running? (./scripts/start.sh)",
                       code="api_unavailable") from exc
    except httpx.TimeoutException as exc:
        raise ApiError("The model stopped responding (timeout).", code="timeout") from exc


def safe_get(path: str, default: Any = None, **kwargs: Any) -> Any:
    """GET that returns ``default`` instead of raising (for optional UI data).

    An expired login is still surfaced so the app can return to the sign-in page.
    """
    try:
        return get(path, **kwargs)
    except ApiError as exc:
        if exc.code == "unauthorized":
            raise
        return default
