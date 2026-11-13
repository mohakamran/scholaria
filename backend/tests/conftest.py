"""Shared fixtures. Tests never need a running Ollama or a downloaded model:
Ollama is replaced with an ``httpx.MockTransport`` (see ``FakeOllama``)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.database.connection import reset_engine
from app.services import analysis_service, ollama_service


class FakeOllama:
    """Simulates the Ollama HTTP API."""

    def __init__(self) -> None:
        self.models = ["qwen3:4b-instruct-2507-q4_K_M", "qwen3:1.7b"]
        self.mode = "ok"  # ok | down | memory | midstream_error | not_found
        self.reply = "This is a mocked answer."
        self.chunks: list[str] | None = None
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.mode == "down":
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.test"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": m, "size": 1_000_000, "details": {"parameter_size": "4B", "quantization_level": "Q4_K_M"}}
                for m in self.models
            ]})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        if path == "/api/chat":
            body = json.loads(request.content)
            self.requests.append(body)
            if self.mode == "not_found":
                return httpx.Response(404, json={"error": f"model '{body['model']}' not found"})
            if self.mode == "memory":
                return httpx.Response(500, json={"error": "model requires more system memory (5.6 GiB) than is available (3.1 GiB)"})
            pieces = self.chunks if self.chunks is not None else [w + " " for w in self.reply.split(" ")]
            lines = [json.dumps({"message": {"role": "assistant", "content": p}, "done": False}) for p in pieces]
            if self.mode == "midstream_error":
                lines.append(json.dumps({"error": "llama runner process has terminated: out of memory"}))
            else:
                lines.append(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                                         "done_reason": "stop", "eval_count": len(pieces), "prompt_eval_count": 42}))
            return httpx.Response(200, content=("\n".join(lines) + "\n").encode(),
                                  headers={"content-type": "application/x-ndjson"})
        return httpx.Response(404, json={"error": "unknown endpoint"})


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "storage"
    monkeypatch.setenv("RA_STORAGE_DIR", str(root))
    monkeypatch.setenv("RA_DATABASE_URL", f"sqlite:///{root / 'test.db'}")
    get_settings.cache_clear()
    reset_engine()
    analysis_service.clear_cache()
    yield root
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def fake_ollama() -> Iterator[FakeOllama]:
    fake = FakeOllama()
    ollama_service.set_transport(httpx.MockTransport(fake.handler))
    yield fake
    ollama_service.set_transport(None)


TEST_PASSWORD = "Research123"


def login_as(test_client: TestClient, email: str, name: str = "Test Researcher") -> str:
    """Create (if needed) and log in an account; return its bearer token."""
    test_client.post("/api/auth/signup", json={"email": email, "full_name": name, "password": TEST_PASSWORD})
    response = test_client.post("/api/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["token"]


@pytest.fixture
def anon_client(storage: Path, fake_ollama: FakeOllama) -> Iterator[TestClient]:
    """A client that is not logged in."""
    from app.main import create_app
    from app.services.auth_service import reset_throttle

    reset_throttle()
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def client(anon_client: TestClient) -> TestClient:
    """A client logged in as researcher@example.com."""
    token = login_as(anon_client, "researcher@example.com")
    anon_client.headers["Authorization"] = f"Bearer {token}"
    return anon_client


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        event, data = "message", "{}"
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        events.append((event, json.loads(data)))
    return events


def make_pdf(path: Path, pages: list[str]) -> Path:
    import pymupdf as fitz

    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 550, 800), text, fontsize=11)
    doc.set_metadata({"title": "A Study of Coffee and Productivity", "author": "Test Author"})
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "paper.pdf", [
        "Introduction. This paper examines how coffee consumption affects academic productivity among "
        "graduate students. The research objective is to quantify the relationship.",
        "Methodology. We surveyed 250 graduate students using a cross-sectional design. Participants "
        "completed a productivity questionnaire and a caffeine intake diary.",
        "Findings. Moderate caffeine intake was associated with higher self-reported productivity. "
        "Limitations include self-report bias and a single university sample.",
    ])
