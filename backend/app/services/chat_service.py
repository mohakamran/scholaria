"""Conversation business logic: context building, streaming and persistence.

The assistant reply is always persisted, including when generation is stopped
by the user, the client disconnects, or Ollama fails mid-stream (partial text
is saved with a non-``complete`` status).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError, OllamaError, ValidationFailed
from app.core.logging import get_logger
from app.core.prompts import GROUNDED_QA_PROMPT
from app.database.connection import session_scope
from app.database.models import Conversation, Message
from app.database.repositories import (
    AssistantRepository,
    ConversationRepository,
    DocumentRepository,
    MessageRepository,
    ProjectRepository,
)
from app.schemas.chat import ChatRequest, RegenerateRequest
from app.services.ollama_service import OllamaService, estimate_tokens
from app.services.retrieval_service import cited_sources, format_passages, get_retriever
from app.services.settings_service import RuntimeSettings, get_runtime_settings

logger = get_logger("chat")

DEFAULT_TITLE = "New conversation"
PROMPT_MARGIN_TOKENS = 64

# conversation_id -> stop flag for in-flight generations.
_stop_flags: dict[int, asyncio.Event] = {}


@dataclass
class PreparedChat:
    conversation_id: int
    model: str
    messages: list[dict[str, str]]
    temperature: float
    num_ctx: int
    num_predict: int
    keep_alive: str
    user_message_id: int | None
    sources: list[dict[str, Any]] = field(default_factory=list)
    history_used: int = 0
    history_dropped: int = 0


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def request_stop(conversation_id: int) -> bool:
    flag = _stop_flags.get(conversation_id)
    if flag is None:
        return False
    flag.set()
    return True


def build_history(history: list[Message], budget_tokens: int, max_messages: int) -> tuple[list[dict[str, str]], int]:
    """Keep the most recent messages that fit the token budget (oldest dropped first)."""
    usable = [m for m in history if m.role in {"user", "assistant"} and m.content.strip()][-max_messages:]
    selected: list[dict[str, str]] = []
    used = 0
    for msg in reversed(usable):
        cost = estimate_tokens(msg.content) + 4
        if used + cost > budget_tokens:
            break
        selected.append({"role": msg.role, "content": msg.content})
        used += cost
    selected.reverse()
    # A context must not start with an assistant turn.
    while selected and selected[0]["role"] == "assistant":
        selected.pop(0)
    return selected, len(usable) - len(selected)


def make_title(message: str, limit: int = 48) -> str:
    """A short conversation title from the first line, cut at a word boundary."""
    first = message.strip().splitlines()[0].strip() if message.strip() else ""
    if not first:
        return DEFAULT_TITLE
    if len(first) <= limit:
        return first
    cut = first[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return (cut or first[:limit]) + "…"


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max(200, max_tokens * 4)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[... message truncated to fit the context window ...]"


class ChatService:
    def __init__(self, db: Session, user_id: int | None = None, ollama: OllamaService | None = None) -> None:
        self.db = db
        self.runtime: RuntimeSettings = get_runtime_settings(db)
        self.ollama = ollama or OllamaService(self.runtime.ollama_url)
        self.user_id = user_id
        self.conversations = ConversationRepository(db, user_id)
        self.messages = MessageRepository(db, user_id)
        self.assistants = AssistantRepository(db)

    # --- Preparation -------------------------------------------------------------
    def _system_prompt(self, assistant_id: int | None, grounded: bool) -> str:
        assistant = None
        if assistant_id is not None:
            assistant = self.assistants.get(assistant_id)
            if assistant is None:
                raise NotFoundError(f"Assistant {assistant_id} not found")
        if assistant is None and self.runtime.default_assistant_id:
            assistant = self.assistants.get(self.runtime.default_assistant_id)
        assistant = assistant or self.assistants.get_default()
        prompt = assistant.system_prompt if assistant else "You are a helpful academic research assistant."
        return f"{prompt}\n\n{GROUNDED_QA_PROMPT}" if grounded else prompt

    def _get_or_create_conversation(self, req: ChatRequest, model: str) -> Conversation:
        if req.conversation_id is not None:
            return self.conversations.get_or_404(req.conversation_id)
        title = make_title(req.message)
        if req.project_id is not None:
            ProjectRepository(self.db, self.user_id).get_or_404(req.project_id)
        return self.conversations.create(
            title=title, project_id=req.project_id, assistant_type=req.assistant_type,
            assistant_id=req.assistant_id, model_name=model,
        )

    async def _resolve_model(self, requested: str | None) -> str:
        return await self.ollama.resolve_model(requested, self.runtime.default_model, self.runtime.fallback_model)

    def _assemble(self, conversation: Conversation, latest_user_text: str, *, assistant_id: int | None,
                  document_ids: list[int] | None, num_ctx: int, num_predict: int,
                  exclude_message_id: int | None) -> tuple[list[dict[str, str]], list[dict[str, Any]], int, int]:
        """Build the model message list within the token budget."""
        grounded = bool(document_ids)
        system_prompt = self._system_prompt(assistant_id or conversation.assistant_id, grounded)
        budget = num_ctx - num_predict - estimate_tokens(system_prompt) - PROMPT_MARGIN_TOKENS
        if budget < 256:
            raise ValidationFailed(
                "Context length is too small for the requested response length. Increase the context "
                "length or reduce the maximum response tokens."
            )

        sources: list[dict[str, Any]] = []
        user_content = truncate_to_tokens(latest_user_text, int(budget * 0.35))
        if grounded:
            docs = DocumentRepository(self.db, self.user_id).get_many(document_ids or [])
            ready_ids = [d.id for d in docs if d.processing_status == "ready"]
            # Never search without an explicit, owned document list (an empty list would mean "all").
            chunks = (get_retriever().search(self.db, latest_user_text, ready_ids, self.runtime.retrieval_top_k)
                      if ready_ids else [])
            passage_text, sources = format_passages(chunks, max_chars=int(budget * 0.45) * 4)
            if sources:
                user_content = f"Source passages:\n{passage_text}\n\nQuestion: {user_content}"
            else:
                user_content = (
                    "No relevant passages were found in the selected documents. Say so explicitly "
                    f"and do not invent document content.\n\nQuestion: {user_content}"
                )
        remaining = budget - estimate_tokens(user_content)

        prior = self.messages.recent_for_conversation(conversation.id, self.runtime.history_max_messages + 2)
        prior = [m for m in prior if m.id != exclude_message_id]
        history, dropped = build_history(prior, max(0, remaining), self.runtime.history_max_messages)
        messages = [{"role": "system", "content": system_prompt}, *history,
                    {"role": "user", "content": user_content}]
        return messages, sources, len(history), dropped

    def _params(self, temperature: float | None, context_length: int | None,
                max_tokens: int | None) -> tuple[float, int, int]:
        rt = self.runtime
        return (
            rt.temperature if temperature is None else temperature,
            context_length or rt.context_length,
            max_tokens or rt.max_tokens,
        )

    async def prepare(self, req: ChatRequest) -> PreparedChat:
        model = await self._resolve_model(req.model)
        temperature, num_ctx, num_predict = self._params(req.temperature, req.context_length, req.max_tokens)
        conversation = self._get_or_create_conversation(req, model)
        user_msg = self.messages.add_message(
            conversation.id, "user", req.message, token_count=estimate_tokens(req.message)
        )
        messages, sources, used, dropped = self._assemble(
            conversation, req.message, assistant_id=req.assistant_id, document_ids=req.document_ids,
            num_ctx=num_ctx, num_predict=num_predict, exclude_message_id=user_msg.id,
        )
        if req.assistant_id and conversation.assistant_id != req.assistant_id:
            conversation.assistant_id = req.assistant_id
        conversation.model_name = model
        self.conversations.touch(conversation)
        return PreparedChat(conversation.id, model, messages, temperature, num_ctx, num_predict,
                            self.runtime.keep_alive, user_msg.id, sources, used, dropped)

    async def prepare_regenerate(self, conversation_id: int, req: RegenerateRequest) -> PreparedChat:
        conversation = self.conversations.get_or_404(conversation_id)
        history = self.messages.list_for_conversation(conversation_id)
        last_user = next((m for m in reversed(history) if m.role == "user"), None)
        if last_user is None:
            raise ValidationFailed("There is no user message to regenerate a response for.")
        model = await self._resolve_model(req.model or conversation.model_name)
        # Remove assistant replies that follow the last user message.
        for msg in history:
            if msg.id > last_user.id and msg.role == "assistant":
                self.db.delete(msg)
        self.db.commit()
        temperature, num_ctx, num_predict = self._params(req.temperature, req.context_length, req.max_tokens)
        messages, sources, used, dropped = self._assemble(
            conversation, last_user.content, assistant_id=req.assistant_id, document_ids=req.document_ids,
            num_ctx=num_ctx, num_predict=num_predict, exclude_message_id=last_user.id,
        )
        conversation.model_name = model
        self.conversations.touch(conversation)
        return PreparedChat(conversation.id, model, messages, temperature, num_ctx, num_predict,
                            self.runtime.keep_alive, None, sources, used, dropped)

    # --- Generation ----------------------------------------------------------------
    @staticmethod
    def _persist_reply(prepared: PreparedChat, content: str, status: str, stats: dict[str, Any]) -> int | None:
        """Save the assistant reply in a fresh session (safe after client disconnect)."""
        content = content.strip()
        if not content:
            return None
        sources: list[dict[str, Any]] = []
        if prepared.sources:
            content, sources = cited_sources(content, prepared.sources)
        with session_scope() as db:
            msg = MessageRepository(db).add_message(
                prepared.conversation_id, "assistant", content, model_name=prepared.model,
                token_count=stats.get("eval_count") or estimate_tokens(content), status=status,
                sources=sources or None,
            )
            conv = db.get(Conversation, prepared.conversation_id)
            if conv is not None:
                ConversationRepository(db).touch(conv)
            return msg.id

    async def stream(self, prepared: PreparedChat) -> AsyncIterator[str]:
        """Yield SSE events: meta, token*, then done or error."""
        stop_flag = asyncio.Event()
        _stop_flags[prepared.conversation_id] = stop_flag
        parts: list[str] = []
        stats: dict[str, Any] = {}
        status = "interrupted"  # if the client disconnects we never reach the end
        error: dict[str, Any] | None = None
        message_id: int | None = None

        yield sse("meta", {
            "conversation_id": prepared.conversation_id, "model": prepared.model,
            "user_message_id": prepared.user_message_id, "history_messages_used": prepared.history_used,
            "history_messages_dropped": prepared.history_dropped,
        })
        agen = self.ollama.chat_stream(
            prepared.model, prepared.messages, temperature=prepared.temperature, num_ctx=prepared.num_ctx,
            num_predict=prepared.num_predict, keep_alive=prepared.keep_alive,
        )
        try:
            async for event in agen:
                if stop_flag.is_set():
                    status = "stopped"
                    break
                if event.done:
                    stats = event.stats
                    continue
                parts.append(event.content)
                yield sse("token", {"content": event.content})
            else:
                status = "complete" if stats.get("done_reason") != "incomplete" else "interrupted"
        except OllamaError as exc:
            status = "error"
            error = exc.to_dict()["error"]
            logger.warning("Generation failed for conversation %s: %s", prepared.conversation_id, exc.code)
        finally:
            _stop_flags.pop(prepared.conversation_id, None)
            # Persist synchronously first so nothing is lost if we are being cancelled.
            try:
                message_id = self._persist_reply(prepared, "".join(parts), status, stats)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Failed to persist assistant reply")
            try:
                await agen.aclose()
            except BaseException:  # noqa: BLE001 - cancellation during cleanup is expected
                pass
            logger.info("Conversation %s: generation %s (%d chars)", prepared.conversation_id, status,
                        sum(len(p) for p in parts))

        if error is not None:
            error["partial_message_id"] = message_id
            yield sse("error", error)
            return
        if status == "complete" and stats.get("done_reason") == "length":
            status_note = "Response reached the maximum length setting."
        else:
            status_note = None
        persisted_sources: list[dict[str, Any]] = []
        if message_id is not None and prepared.sources:
            with session_scope() as db:
                msg = db.get(Message, message_id)
                persisted_sources = json.loads(msg.sources) if msg and msg.sources else []
        yield sse("done", {
            "message_id": message_id, "status": status, "conversation_id": prepared.conversation_id,
            "completion_tokens": stats.get("eval_count"), "prompt_tokens": stats.get("prompt_eval_count"),
            "note": status_note, "sources": persisted_sources,
        })

    async def complete(self, prepared: PreparedChat) -> Message:
        """Non-streaming generation (consumes the same stream internally)."""
        error: dict[str, Any] | None = None
        message_id: int | None = None
        async for chunk in self.stream(prepared):
            event, _, payload = chunk.partition("\ndata: ")
            data = json.loads(payload)
            if event.endswith("error"):
                error = data
            elif event.endswith("done"):
                message_id = data.get("message_id")
        if error is not None:
            status_by_code = {"ollama_unavailable": 503, "model_not_found": 404, "insufficient_memory": 507}
            raise AppError(error["message"], status_code=status_by_code.get(error["code"], 502), code=error["code"])
        if message_id is None:
            raise AppError("The model returned an empty response.", status_code=502, code="empty_response")
        msg = self.db.get(Message, message_id)
        self.db.refresh(msg)  # type: ignore[arg-type]
        return msg  # type: ignore[return-value]
