"""Data-access layer. Route handlers and services never build queries directly."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.database.models import (
    AnalysisRun,
    Assistant,
    Base,
    Citation,
    Conversation,
    Dataset,
    Document,
    DocumentChunk,
    Message,
    Project,
    ResearchNote,
    Setting,
    utcnow,
)

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]
    label: str = "Record"

    def __init__(self, db: Session, user_id: int | None = None) -> None:
        """``user_id`` scopes every read and write to one account (None = unscoped, internal use)."""
        self.db = db
        self.user_id = user_id

    # --- Ownership --------------------------------------------------------------
    def owner_of(self, obj: ModelT) -> int | None:
        """Return the account that owns ``obj`` (overridden for child records)."""
        return getattr(obj, "user_id", None)

    def _owned(self, obj: ModelT | None) -> bool:
        return obj is not None and (self.user_id is None or self.owner_of(obj) == self.user_id)

    def scope(self, stmt: Any) -> Any:
        if self.user_id is not None and hasattr(self.model, "user_id"):
            stmt = stmt.where(self.model.user_id == self.user_id)  # type: ignore[attr-defined]
        return stmt

    def get(self, obj_id: int) -> ModelT | None:
        obj = self.db.get(self.model, obj_id)
        return obj if self._owned(obj) else None

    def get_or_404(self, obj_id: int) -> ModelT:
        obj = self.get(obj_id)
        if obj is None:
            # Records of other accounts are reported as missing, never as forbidden.
            raise NotFoundError(f"{self.label} {obj_id} not found")
        return obj

    def add(self, obj: ModelT) -> ModelT:
        self.db.add(obj)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def create(self, **fields: Any) -> ModelT:
        if self.user_id is not None and hasattr(self.model, "user_id"):
            fields.setdefault("user_id", self.user_id)
        return self.add(self.model(**fields))

    def update(self, obj: ModelT, **fields: Any) -> ModelT:
        for key, value in fields.items():
            setattr(obj, key, value)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def delete(self, obj: ModelT) -> None:
        self.db.delete(obj)
        self.db.commit()


# --- Projects ---------------------------------------------------------------
class ProjectRepository(BaseRepository[Project]):
    model = Project
    label = "Project"

    def list(self) -> Sequence[Project]:
        return self.db.scalars(self.scope(select(Project)).order_by(Project.updated_at.desc())).all()

    def counts(self, project_id: int) -> dict[str, int]:
        def count(model: type[Base]) -> int:
            return self.db.scalar(select(func.count()).select_from(model).where(model.project_id == project_id)) or 0  # type: ignore[attr-defined]

        return {
            "documents": count(Document),
            "datasets": count(Dataset),
            "conversations": count(Conversation),
            "notes": count(ResearchNote),
            "citations": count(Citation),
        }

    def delete_project(self, project: Project) -> None:
        """Delete a project; unlink (not delete) documents, datasets and conversations."""
        for model in (Document, Dataset, Conversation):
            self.db.execute(update(model).where(model.project_id == project.id).values(project_id=None))  # type: ignore[attr-defined]
        self.db.delete(project)
        self.db.commit()


class NoteRepository(BaseRepository[ResearchNote]):
    model = ResearchNote
    label = "Note"

    def owner_of(self, obj: ResearchNote) -> int | None:
        return obj.project.user_id

    def list_for_project(self, project_id: int) -> Sequence[ResearchNote]:
        return self.db.scalars(
            select(ResearchNote).where(ResearchNote.project_id == project_id).order_by(ResearchNote.updated_at.desc())
        ).all()


class CitationRepository(BaseRepository[Citation]):
    model = Citation
    label = "Citation"

    def owner_of(self, obj: Citation) -> int | None:
        return obj.project.user_id

    def list_for_project(self, project_id: int) -> Sequence[Citation]:
        return self.db.scalars(
            select(Citation).where(Citation.project_id == project_id).order_by(Citation.created_at.desc())
        ).all()


# --- Assistants -------------------------------------------------------------
class AssistantRepository(BaseRepository[Assistant]):
    model = Assistant
    label = "Assistant"

    def list(self) -> Sequence[Assistant]:
        return self.db.scalars(select(Assistant).order_by(Assistant.is_default.desc(), Assistant.name)).all()

    def get_default(self) -> Assistant | None:
        return self.db.scalars(select(Assistant).where(Assistant.is_default.is_(True))).first() or \
            self.db.scalars(select(Assistant).order_by(Assistant.id)).first()

    def get_by_name(self, name: str) -> Assistant | None:
        return self.db.scalars(select(Assistant).where(Assistant.name == name)).first()

    def set_default(self, assistant: Assistant) -> None:
        self.db.execute(update(Assistant).values(is_default=False))
        assistant.is_default = True
        self.db.commit()


# --- Conversations & messages ----------------------------------------------
class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation
    label = "Conversation"

    def list(self, *, project_id: int | None = None, assistant_type: str | None = None,
             limit: int = 100, offset: int = 0) -> Sequence[Conversation]:
        stmt = self.scope(select(Conversation))
        if project_id is not None:
            stmt = stmt.where(Conversation.project_id == project_id)
        if assistant_type is not None:
            stmt = stmt.where(Conversation.assistant_type == assistant_type)
        stmt = stmt.order_by(Conversation.updated_at.desc()).limit(limit).offset(offset)
        return self.db.scalars(stmt).all()

    def message_count(self, conversation_id: int) -> int:
        return self.db.scalar(
            select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
        ) or 0

    def touch(self, conversation: Conversation) -> None:
        conversation.updated_at = utcnow()
        self.db.commit()


class MessageRepository(BaseRepository[Message]):
    model = Message
    label = "Message"

    def owner_of(self, obj: Message) -> int | None:
        return obj.conversation.user_id

    def list_for_conversation(self, conversation_id: int) -> Sequence[Message]:
        return self.db.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        ).all()

    def recent_for_conversation(self, conversation_id: int, limit: int) -> list[Message]:
        """Most recent ``limit`` messages, returned oldest-first."""
        rows = self.db.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.desc()).limit(limit)
        ).all()
        return list(reversed(rows))

    def last_message(self, conversation_id: int) -> Message | None:
        return self.db.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.desc()).limit(1)
        ).first()

    def clear(self, conversation_id: int) -> int:
        result = self.db.execute(delete(Message).where(Message.conversation_id == conversation_id))
        self.db.commit()
        return result.rowcount or 0

    def add_message(self, conversation_id: int, role: str, content: str, *, model_name: str | None = None,
                    token_count: int | None = None, status: str = "complete",
                    sources: list[dict[str, Any]] | None = None) -> Message:
        return self.create(
            conversation_id=conversation_id, role=role, content=content, model_name=model_name,
            token_count=token_count, status=status, sources=json.dumps(sources) if sources else None,
        )


# --- Documents --------------------------------------------------------------
class DocumentRepository(BaseRepository[Document]):
    model = Document
    label = "Document"

    def list(self, project_id: int | None = None) -> Sequence[Document]:
        stmt = self.scope(select(Document))
        if project_id is not None:
            stmt = stmt.where(Document.project_id == project_id)
        return self.db.scalars(stmt.order_by(Document.created_at.desc())).all()

    def get_by_hash(self, file_hash: str) -> Document | None:
        return self.db.scalars(self.scope(select(Document)).where(Document.file_hash == file_hash)).first()

    def get_many(self, ids: Iterable[int]) -> Sequence[Document]:
        ids = list(ids)
        if not ids:
            return []
        return self.db.scalars(self.scope(select(Document)).where(Document.id.in_(ids))).all()


class ChunkRepository(BaseRepository[DocumentChunk]):
    model = DocumentChunk
    label = "Chunk"

    def delete_for_document(self, document_id: int) -> None:
        self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        self.db.commit()

    def bulk_insert(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            self.db.add_all([DocumentChunk(**row) for row in rows])
            self.db.commit()

    def count_for_document(self, document_id: int) -> int:
        return self.db.scalar(
            select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == document_id)
        ) or 0

    def iter_for_document(self, document_id: int, batch_size: int = 50) -> Iterable[DocumentChunk]:
        """Yield chunks in order without loading the whole document at once."""
        last_index = -1
        while True:
            batch = self.db.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id, DocumentChunk.chunk_index > last_index)
                .order_by(DocumentChunk.chunk_index)
                .limit(batch_size)
            ).all()
            if not batch:
                return
            yield from batch
            last_index = batch[-1].chunk_index

    def get_by_ids(self, ids: Sequence[int]) -> list[DocumentChunk]:
        if not ids:
            return []
        rows = {c.id: c for c in self.db.scalars(select(DocumentChunk).where(DocumentChunk.id.in_(ids))).all()}
        return [rows[i] for i in ids if i in rows]


# --- Datasets ---------------------------------------------------------------
class DatasetRepository(BaseRepository[Dataset]):
    model = Dataset
    label = "Dataset"

    def list(self, project_id: int | None = None) -> Sequence[Dataset]:
        stmt = self.scope(select(Dataset))
        if project_id is not None:
            stmt = stmt.where(Dataset.project_id == project_id)
        return self.db.scalars(stmt.order_by(Dataset.created_at.desc())).all()


class AnalysisRunRepository(BaseRepository[AnalysisRun]):
    model = AnalysisRun
    label = "Analysis"

    def owner_of(self, obj: AnalysisRun) -> int | None:
        return obj.dataset.user_id

    def list_for_dataset(self, dataset_id: int) -> Sequence[AnalysisRun]:
        return self.db.scalars(
            select(AnalysisRun).where(AnalysisRun.dataset_id == dataset_id).order_by(AnalysisRun.created_at.desc())
        ).all()


# --- Settings ---------------------------------------------------------------
class SettingRepository(BaseRepository[Setting]):
    model = Setting
    label = "Setting"

    def all_values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for row in self.db.scalars(select(Setting)).all():
            try:
                out[row.key] = json.loads(row.value)
            except json.JSONDecodeError:
                out[row.key] = row.value
        return out

    def set_value(self, key: str, value: Any) -> None:
        row = self.db.scalars(select(Setting).where(Setting.key == key)).first()
        encoded = json.dumps(value)
        if row is None:
            self.db.add(Setting(key=key, value=encoded))
        else:
            row.value = encoded
            row.updated_at = utcnow()
        self.db.commit()


# --- Activity ---------------------------------------------------------------
def recent_activity(db: Session, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """Merge one account's recent conversations, documents, datasets and notes into one timeline."""
    items: list[dict[str, Any]] = []
    for c in db.scalars(select(Conversation).where(Conversation.user_id == user_id)
                        .order_by(Conversation.updated_at.desc()).limit(limit)):
        items.append({"type": "conversation", "id": c.id, "title": c.title, "at": c.updated_at})
    for d in db.scalars(select(Document).where(Document.user_id == user_id)
                        .order_by(Document.created_at.desc()).limit(limit)):
        items.append({"type": "document", "id": d.id, "title": d.title or d.filename, "at": d.created_at})
    for ds in db.scalars(select(Dataset).where(Dataset.user_id == user_id)
                         .order_by(Dataset.created_at.desc()).limit(limit)):
        items.append({"type": "dataset", "id": ds.id, "title": ds.filename, "at": ds.created_at})
    for n in db.scalars(select(ResearchNote).join(Project).where(Project.user_id == user_id)
                        .order_by(ResearchNote.updated_at.desc()).limit(limit)):
        items.append({"type": "note", "id": n.id, "title": n.title, "at": n.updated_at})
    items.sort(key=lambda item: item["at"], reverse=True)
    return items[:limit]
