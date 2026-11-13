"""Communication with the local Ollama HTTP API.

This module is the only place that talks to Ollama. It knows nothing about
conversations, documents or the database.

Memory safety on 8 GB machines:
* A process-wide semaphore allows only ``max_concurrent_requests`` (default 1)
  inference calls at a time.
* ``num_ctx`` and ``num_predict`` are always sent explicitly, so the model's
  (much larger) advertised context is never used by accident.
* ``keep_alive`` defaults to two minutes so the model is unloaded when idle.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import ModelMemoryError, ModelNotFoundError, OllamaError, OllamaUnavailableError
from app.core.logging import get_logger

logger = get_logger("ollama")

# Tests may install an ``httpx.MockTransport`` here so no real server is needed.
_transport: httpx.AsyncBaseTransport | None = None

_lock_state: dict[str, Any] = {"loop": None, "sem": None}

_MEMORY_PATTERNS = re.compile(
    r"(out of memory|requires more system memory|insufficient memory|not enough memory|"
    r"failed to allocate|cudaMalloc|memory)", re.IGNORECASE
)


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def _semaphore() -> asyncio.Semaphore:
    """Return a semaphore bound to the running event loop (safe across test loops)."""
    loop = asyncio.get_running_loop()
    if _lock_state["loop"] is not loop:
        _lock_state["loop"] = loop
        _lock_state["sem"] = asyncio.Semaphore(get_settings().max_concurrent_requests)
    return _lock_state["sem"]


def normalize_model_name(name: str) -> str:
    name = name.strip()
    return name if ":" in name else f"{name}:latest"


def classify_error(message: str, status_code: int | None = None) -> OllamaError:
    lowered = message.lower()
    if status_code == 404 or ("model" in lowered and "not found" in lowered):
        return ModelNotFoundError(
            f"Model not available in Ollama: {message}. Install it with 'ollama pull <model>' "
            "or choose another model in Settings."
        )
    if _MEMORY_PATTERNS.search(message):
        return ModelMemoryError(
            "The model could not run because there is not enough free memory. Close other "
            "applications, reduce the context length, or switch to the smaller fallback model "
            f"(e.g. qwen3:1.7b). Details: {message}"
        )
    return OllamaError(f"Ollama error: {message}")


class ThinkFilter:
    """Remove ``<think>...</think>`` reasoning blocks from streamed text."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False

    def feed(self, text: str) -> str:
        self._buffer += text
        out: list[str] = []
        while self._buffer:
            if self._inside:
                end = self._buffer.find(self.CLOSE)
                if end == -1:
                    # Keep only a possible partial closing tag.
                    self._buffer = self._buffer[-(len(self.CLOSE) - 1):]
                    break
                self._buffer = self._buffer[end + len(self.CLOSE):].lstrip("\n")
                self._inside = False
            else:
                start = self._buffer.find(self.OPEN)
                if start == -1:
                    # Emit everything except a trailing partial "<think" prefix.
                    keep = 0
                    for i in range(1, len(self.OPEN)):
                        if self._buffer.endswith(self.OPEN[:i]):
                            keep = i
                    out.append(self._buffer[: len(self._buffer) - keep])
                    self._buffer = self._buffer[len(self._buffer) - keep:]
                    break
                out.append(self._buffer[:start])
                self._buffer = self._buffer[start + len(self.OPEN):]
                self._inside = True
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self._inside else self._buffer
        self._buffer = ""
        return rest


@dataclass
class ChatResult:
    content: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    done_reason: str | None = None


@dataclass
class StreamEvent:
    """A piece of streamed output; ``done`` is True on the final event."""

    content: str = ""
    done: bool = False
    stats: dict[str, Any] = field(default_factory=dict)


class OllamaService:
    def __init__(self, base_url: str | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        self.timeout = httpx.Timeout(settings.ollama_read_timeout, connect=settings.ollama_connect_timeout)
        self.disable_thinking = settings.disable_thinking

    def _client(self, timeout: httpx.Timeout | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout or self.timeout, transport=_transport)

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            async with self._client(httpx.Timeout(10.0, connect=3.0)) as client:
                response = await client.get(path)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise OllamaUnavailableError(
                "Cannot connect to Ollama. Start it with 'ollama serve' (or open the Ollama app)."
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(f"Ollama request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise classify_error(response.text, response.status_code)
        return response.json()

    # --- Discovery ------------------------------------------------------------
    async def version(self) -> str:
        return str((await self._get("/api/version")).get("version", "unknown"))

    async def list_models(self) -> list[dict[str, Any]]:
        data = await self._get("/api/tags")
        models = []
        for m in data.get("models", []):
            details = m.get("details") or {}
            models.append({
                "name": m.get("name") or m.get("model"),
                "size_bytes": m.get("size"),
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
                "family": details.get("family"),
                "modified_at": m.get("modified_at"),
            })
        return models

    async def running_models(self) -> list[dict[str, Any]]:
        data = await self._get("/api/ps")
        return [
            {"name": m.get("name"), "size_bytes": m.get("size"), "expires_at": m.get("expires_at")}
            for m in data.get("models", [])
        ]

    async def resolve_model(self, requested: str | None, default: str, fallback: str) -> str:
        """Pick an installed model: requested -> default -> fallback."""
        installed = {normalize_model_name(m["name"]) for m in await self.list_models() if m.get("name")}
        for candidate in (requested, default, fallback):
            if candidate and normalize_model_name(candidate) in installed:
                return candidate
        if not installed:
            raise ModelNotFoundError(
                f"No models are installed in Ollama. Run: ollama pull {default}"
            )
        raise ModelNotFoundError(
            f"Model '{requested or default}' is not installed. Run 'ollama pull {requested or default}' "
            "or select an installed model."
        )

    # --- Inference --------------------------------------------------------------
    def _payload(self, model: str, messages: list[dict[str, str]], *, temperature: float, num_ctx: int,
                 num_predict: int, keep_alive: str, stream: bool) -> dict[str, Any]:
        messages = list(messages)
        if self.disable_thinking and "qwen3" in model and "instruct" not in model and messages:
            # Qwen3 hybrid models honour the "/no_think" soft switch in the system prompt.
            if messages[0]["role"] == "system":
                messages[0] = {**messages[0], "content": messages[0]["content"] + " /no_think"}
            else:
                messages.insert(0, {"role": "system", "content": "/no_think"})
        return {
            "model": model,
            "messages": messages,
            "stream": stream,
            "keep_alive": keep_alive,
            "options": {"temperature": temperature, "num_ctx": num_ctx, "num_predict": num_predict},
        }

    async def chat(self, model: str, messages: list[dict[str, str]], *, temperature: float, num_ctx: int,
                   num_predict: int, keep_alive: str) -> ChatResult:
        parts: list[str] = []
        stats: dict[str, Any] = {}
        async for event in self.chat_stream(model, messages, temperature=temperature, num_ctx=num_ctx,
                                            num_predict=num_predict, keep_alive=keep_alive):
            if event.done:
                stats = event.stats
            else:
                parts.append(event.content)
        return ChatResult(
            content="".join(parts).strip(), model=model,
            prompt_tokens=stats.get("prompt_eval_count"), completion_tokens=stats.get("eval_count"),
            done_reason=stats.get("done_reason"),
        )

    async def chat_stream(self, model: str, messages: list[dict[str, str]], *, temperature: float,
                          num_ctx: int, num_predict: int, keep_alive: str) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion. Holds the inference slot for the whole stream."""
        payload = self._payload(model, messages, temperature=temperature, num_ctx=num_ctx,
                                num_predict=num_predict, keep_alive=keep_alive, stream=True)
        think = ThinkFilter()
        # Acquire the inference slot manually and release it in ``finally`` so the
        # slot is freed even when the consumer disconnects or is cancelled.
        sem = _semaphore()
        await sem.acquire()
        try:
            async with self._client() as client:
                async with client.stream("POST", "/api/chat", json=payload) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")
                        try:
                            body = json.loads(body).get("error", body)
                        except (json.JSONDecodeError, AttributeError):
                            pass
                        raise classify_error(str(body), response.status_code)
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning("Skipping malformed stream line from Ollama")
                            continue
                        if "error" in data:
                            raise classify_error(str(data["error"]))
                        piece = (data.get("message") or {}).get("content") or ""
                        if piece:
                            visible = think.feed(piece)
                            if visible:
                                yield StreamEvent(content=visible)
                        if data.get("done"):
                            tail = think.flush()
                            if tail:
                                yield StreamEvent(content=tail)
                            yield StreamEvent(done=True, stats={
                                k: data.get(k) for k in
                                ("eval_count", "prompt_eval_count", "total_duration", "done_reason")
                            })
                            return
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise OllamaUnavailableError(
                "Cannot connect to Ollama. Start it with 'ollama serve' (or open the Ollama app)."
            ) from exc
        except httpx.ReadTimeout as exc:
            raise OllamaError("Ollama timed out while generating a response.") from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(f"Connection to Ollama failed: {type(exc).__name__}") from exc
        finally:
            sem.release()
        # Stream ended without a "done" message (e.g. server stopped).
        tail = think.flush()
        if tail:
            yield StreamEvent(content=tail)
        yield StreamEvent(done=True, stats={"done_reason": "incomplete"})


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 characters per token for English)."""
    return max(1, len(text) // 4) if text else 0
