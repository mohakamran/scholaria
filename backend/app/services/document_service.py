"""Document ingestion (PDF/DOCX/TXT), chunking, grounded Q&A and summaries.

Memory notes: PDFs are read page by page, DOCX/TXT in ~20k character segments,
and chunks are written to SQLite per page/segment, so a whole document is never
held in memory as one string.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, ValidationFailed
from app.core.logging import get_logger
from app.core.prompts import (
    DOCUMENT_MAP_PROMPT,
    DOCUMENT_REDUCE_PROMPT,
    GROUNDED_QA_PROMPT,
    LITERATURE_PROMPT,
)
from app.database.models import Document, DocumentChunk
from app.database.repositories import ChunkRepository, DocumentRepository
from app.schemas.documents import AskRequest, AskResponse, SummarizeRequest, SummarizeResponse
from app.services.ollama_service import OllamaService, estimate_tokens
from app.services.retrieval_service import cited_sources, format_passages, get_retriever
from app.services.settings_service import get_runtime_settings
from app.services.storage_service import StoredFile, delete_stored, resolve_inside

logger = get_logger("documents")

ALLOWED_DOCUMENT_TYPES = {"pdf", "docx", "txt"}
SEGMENT_CHARS = 20000
MAP_MAX_TOKENS = 400


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
@dataclass
class Segment:
    page_number: int | None
    text: str


def clean_text(text: str) -> str:
    text = text.replace("­", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate line breaks
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf_metadata(path: Path) -> dict[str, Any]:
    import pymupdf as fitz  # PyMuPDF

    with fitz.open(path) as pdf:
        meta = {k: v for k, v in (pdf.metadata or {}).items() if v}
        meta["page_count"] = pdf.page_count
        meta["encrypted"] = bool(pdf.needs_pass)
    return meta


def _iter_pdf(path: Path) -> Iterator[Segment]:
    import pymupdf as fitz  # PyMuPDF

    with fitz.open(path) as pdf:
        if pdf.needs_pass:
            raise ValidationFailed("The PDF is password-protected and cannot be read")
        for index in range(pdf.page_count):
            page = pdf.load_page(index)
            # "blocks" keeps paragraphs together; join wrapped lines inside a block.
            blocks = page.get_text("blocks", sort=True)
            paragraphs = [re.sub(r"\s*\n\s*", " ", b[4]).strip() for b in blocks if b[6] == 0 and b[4].strip()]
            yield Segment(index + 1, clean_text("\n\n".join(paragraphs)))


def _docx_metadata(path: Path) -> dict[str, Any]:
    import docx

    props = docx.Document(str(path)).core_properties
    meta = {
        "title": props.title, "author": props.author, "subject": props.subject,
        "keywords": props.keywords,
        "created": props.created.isoformat() if props.created else None,
    }
    return {k: v for k, v in meta.items() if v}


def _iter_docx(path: Path) -> Iterator[Segment]:
    import docx

    document = docx.Document(str(path))
    buffer: list[str] = []
    size = 0

    def texts() -> Iterator[str]:
        for para in document.paragraphs:
            if para.text.strip():
                yield para.text.strip()
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    yield " | ".join(dict.fromkeys(cells))

    for text in texts():
        buffer.append(text)
        size += len(text)
        if size >= SEGMENT_CHARS:
            yield Segment(None, clean_text("\n\n".join(buffer)))
            buffer, size = [], 0
    if buffer:
        yield Segment(None, clean_text("\n\n".join(buffer)))


def _iter_txt(path: Path) -> Iterator[Segment]:
    raw = path.read_bytes()[:4]
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    buffer: list[str] = []
    size = 0
    with path.open("r", encoding=encoding, errors="replace") as fh:
        for line in fh:
            buffer.append(line)
            size += len(line)
            if size >= SEGMENT_CHARS and not line.strip():
                yield Segment(None, clean_text("".join(buffer)))
                buffer, size = [], 0
    if buffer:
        yield Segment(None, clean_text("".join(buffer)))


def iter_segments(path: Path, file_type: str) -> Iterator[Segment]:
    if file_type == "pdf":
        return _iter_pdf(path)
    if file_type == "docx":
        return _iter_docx(path)
    if file_type == "txt":
        return _iter_txt(path)
    raise ValidationFailed(f"Unsupported document type: {file_type}")


def extract_metadata(path: Path, file_type: str) -> dict[str, Any]:
    try:
        if file_type == "pdf":
            return _pdf_metadata(path)
        if file_type == "docx":
            return _docx_metadata(path)
    except ValidationFailed:
        raise
    except Exception as exc:  # metadata is optional
        logger.warning("Metadata extraction failed: %s", type(exc).__name__)
    return {}


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")


def _split_long(paragraph: str, size: int) -> list[str]:
    if len(paragraph) <= size:
        return [paragraph]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE.split(paragraph):
        while len(sentence) > size:  # a single huge "sentence" (tables, references)
            cut = sentence.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + len(sentence) + 1 > size:
            pieces.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current.strip():
        pieces.append(current.strip())
    return pieces


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    """Split text into ~``size`` character chunks on paragraph/sentence boundaries.

    Each chunk after the first starts with the last ``overlap`` characters of the
    previous one (aligned to a word boundary) to preserve context.
    """
    text = text.strip()
    if not text:
        return []
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if para:
            units.extend(_split_long(para, size))
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 2 > size:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            if tail and " " in tail:
                tail = tail[tail.index(" ") + 1:]
            current = f"{tail}\n\n{unit}" if tail else unit
        else:
            current = f"{current}\n\n{unit}" if current else unit
    if current.strip():
        chunks.append(current)
    return chunks


# --------------------------------------------------------------------------- #
# Literature tasks
# --------------------------------------------------------------------------- #
TASKS: dict[str, dict[str, str]] = {
    "summary": {
        "label": "Summary",
        "instruction": "Summarise the document: purpose, theoretical background, method, key findings and conclusions.",
        "query": "aim purpose objective method results findings conclusion contribution",
    },
    "objectives": {
        "label": "Research objectives",
        "instruction": "Extract the stated research objectives, aims or purposes of the study.",
        "query": "aim aims objective objectives purpose goal this study seeks investigate examine",
    },
    "research_questions": {
        "label": "Research questions",
        "instruction": "Extract the explicit research questions (RQ) and, if stated, the hypotheses.",
        "query": "research question questions RQ RQ1 hypothesis hypotheses H1 address",
    },
    "theoretical_framework": {
        "label": "Theoretical framework",
        "instruction": "Identify the theories, models or conceptual frameworks the study uses and how they are applied.",
        "query": "theory theoretical framework model conceptual lens grounded based perspective",
    },
    "methodology": {
        "label": "Methodology",
        "instruction": "Describe the methodology: research design, approach, data collection, instruments, procedures and analysis techniques.",
        "query": "method methodology design approach data collection procedure analysis survey interview experiment qualitative quantitative",
    },
    "sample": {
        "label": "Sample & participants",
        "instruction": "Identify the sample size, participants, sampling method, setting and demographics. Report numbers exactly as written.",
        "query": "sample participants respondents n= recruited sampling population demographic age gender",
    },
    "variables": {
        "label": "Variables & measurements",
        "instruction": "List the variables (independent, dependent, mediating, moderating, control) and how each was measured (scales, items, instruments).",
        "query": "variable variables measure measured scale items independent dependent mediator moderator control construct",
    },
    "findings": {
        "label": "Findings",
        "instruction": "Summarise the main findings and results, keeping any reported statistics exactly as written.",
        "query": "results findings found significant effect showed indicate support supported",
    },
    "limitations": {
        "label": "Limitations",
        "instruction": "Identify the limitations acknowledged by the authors.",
        "query": "limitation limitations limited caution generalizability constraint weakness",
    },
    "future_research": {
        "label": "Future research",
        "instruction": "Identify recommendations for future research stated by the authors.",
        "query": "future research recommend recommendations further studies should explore direction",
    },
}

_PAGE_REF = re.compile(r"\((?:p|pp|page|pages)\.?\s*(\d{1,4})(?:\s*[-–,]\s*(\d{1,4}))?\)", re.IGNORECASE)


def validate_page_refs(text: str, valid_pages: set[int]) -> tuple[str, list[int], int]:
    """Remove page references that do not exist in the processed text."""
    removed = 0
    seen: set[int] = set()

    def repl(match: re.Match[str]) -> str:
        nonlocal removed
        pages = [int(g) for g in match.groups() if g]
        if all(p in valid_pages for p in pages):
            seen.update(pages)
            return match.group(0)
        removed += 1
        return "(page reference removed: not verifiable)"

    return _PAGE_REF.sub(repl, text), sorted(seen), removed


class DocumentService:
    def __init__(self, db: Session, user_id: int | None = None, ollama: OllamaService | None = None) -> None:
        self.db = db
        self.settings = get_settings()
        self.runtime = get_runtime_settings(db)
        self.ollama = ollama or OllamaService(self.runtime.ollama_url)
        self.user_id = user_id
        self.docs = DocumentRepository(db, user_id)
        self.chunks = ChunkRepository(db)

    # --- Ingestion -----------------------------------------------------------
    def register_upload(self, stored: StoredFile, project_id: int | None) -> Document:
        existing = self.docs.get_by_hash(stored.sha256)
        if existing is not None:
            delete_stored(self.settings.uploads_dir, stored.stored_name)
            raise ConflictError(
                f"This file was already uploaded as document {existing.id} ('{existing.filename}')",
                details={"document_id": existing.id},
            )
        return self.docs.create(
            project_id=project_id, filename=stored.original_name, stored_filename=stored.stored_name,
            file_type=stored.extension, file_size=stored.size, file_hash=stored.sha256,
            processing_status="uploaded", title=Path(stored.original_name).stem[:500],
        )

    def path_for(self, doc: Document) -> Path:
        return resolve_inside(self.settings.uploads_dir, doc.stored_filename)

    def process(self, doc: Document) -> Document:
        """Extract text, chunk it and index it. Safe to call again (re-processing)."""
        path = self.path_for(doc)
        if not path.exists():
            return self.docs.update(doc, processing_status="failed", error_message="Stored file is missing")
        self.docs.update(doc, processing_status="processing", error_message=None)
        self.chunks.delete_for_document(doc.id)
        try:
            metadata = extract_metadata(path, doc.file_type)
            index = 0
            chars = 0
            pages = 0
            for segment in iter_segments(path, doc.file_type):
                pages = max(pages, segment.page_number or 0)
                rows = []
                for piece in chunk_text(segment.text, self.settings.chunk_chars, self.settings.chunk_overlap):
                    rows.append({"document_id": doc.id, "chunk_index": index,
                                 "page_number": segment.page_number, "content": piece})
                    index += 1
                    chars += len(piece)
                self.chunks.bulk_insert(rows)
            title = str(metadata.get("title") or "").strip()
            if len(title) < 4 or title.lower().startswith(("untitled", "microsoft word")):
                title = doc.title or Path(doc.filename).stem
            if index == 0:
                hint = " It may be a scanned PDF (OCR is not supported in this version)." if doc.file_type == "pdf" else ""
                return self.docs.update(doc, processing_status="failed", chunk_count=0, char_count=0,
                                        page_count=pages or None, metadata_json=json.dumps(metadata, default=str),
                                        error_message=f"No extractable text found.{hint}")
            logger.info("Document %s processed: %d chunks, %d pages", doc.id, index, pages)
            return self.docs.update(
                doc, processing_status="ready", chunk_count=index, char_count=chars,
                page_count=pages or None, title=title[:500], metadata_json=json.dumps(metadata, default=str),
            )
        except ValidationFailed as exc:
            self.db.rollback()
            return self.docs.update(doc, processing_status="failed", error_message=exc.message[:500])
        except Exception as exc:
            self.db.rollback()
            logger.exception("Processing failed for document %s", doc.id)
            return self.docs.update(doc, processing_status="failed",
                                    error_message=f"Text extraction failed ({type(exc).__name__})")

    def delete(self, doc: Document) -> None:
        stored = doc.stored_filename
        self.docs.delete(doc)
        delete_stored(self.settings.uploads_dir, stored)

    def _require_ready(self, doc: Document) -> None:
        if doc.processing_status != "ready":
            raise ValidationFailed(
                f"Document is not ready for questions (status: {doc.processing_status})."
                + (f" {doc.error_message}" if doc.error_message else "")
            )

    # --- Question answering ------------------------------------------------------
    async def ask(self, doc: Document, req: AskRequest) -> AskResponse:
        self._require_ready(doc)
        ids = [doc.id]
        for extra in self.docs.get_many(req.additional_document_ids or []):
            if extra.processing_status == "ready" and extra.id not in ids:
                ids.append(extra.id)
        top_k = req.top_k or self.runtime.retrieval_top_k
        chunks = get_retriever().search(self.db, req.question, ids, top_k)
        if not chunks:
            return AskResponse(
                answer="No passages in the selected document(s) matched this question, so it cannot be "
                       "answered from the document. Try rephrasing with terms used in the paper.",
                sources=[], retrieved_passages=0, model=None, grounded=False,
                note="No evidence retrieved; the language model was not called.",
            )
        model = await self.ollama.resolve_model(req.model, self.runtime.default_model, self.runtime.fallback_model)
        num_ctx, num_predict = self.runtime.context_length, self.runtime.max_tokens
        system = f"{LITERATURE_PROMPT}\n\n{GROUNDED_QA_PROMPT}"
        budget_tokens = num_ctx - num_predict - estimate_tokens(system) - estimate_tokens(req.question) - 96
        passages, sources = format_passages(chunks, max_chars=max(800, budget_tokens * 4))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Source passages:\n{passages}\n\nQuestion: {req.question}"},
        ]
        result = await self.ollama.chat(
            model, messages, temperature=req.temperature if req.temperature is not None else min(self.runtime.temperature, 0.3),
            num_ctx=num_ctx, num_predict=num_predict, keep_alive=self.runtime.keep_alive,
        )
        answer, used = cited_sources(result.content, sources)
        grounded = bool(used)
        note = (f"Answer based on {len(sources)} retrieved passage(s), not a verification against the full "
                "document.")
        if not grounded:
            note += " The model did not cite any passage, so treat this answer with caution."
        return AskResponse(answer=answer, sources=used or sources, retrieved_passages=len(sources),  # type: ignore[arg-type]
                           model=model, grounded=grounded, note=note)

    # --- Summaries & extraction ------------------------------------------------------
    def _windows(self, doc: Document, chunks: list[DocumentChunk] | None, max_chars: int) -> Iterator[tuple[str, set[int], int]]:
        """Group chunks into windows of text with [Page N] markers."""
        source = chunks if chunks is not None else self.chunks.iter_for_document(doc.id)
        parts: list[str] = []
        pages: set[int] = set()
        size = 0
        last_page: int | None = None
        for chunk in source:
            marker = ""
            if chunk.page_number is not None and chunk.page_number != last_page:
                marker = f"[Page {chunk.page_number}]\n"
                last_page = chunk.page_number
            piece = marker + chunk.content
            if parts and size + len(piece) > max_chars:
                yield "\n\n".join(parts), pages, len(parts)
                parts, pages, size = [], set(), 0
                if chunk.page_number is not None and not marker:
                    piece = f"[Page {chunk.page_number}]\n" + piece
            parts.append(piece)
            size += len(piece)
            if chunk.page_number is not None:
                pages.add(chunk.page_number)
        if parts:
            yield "\n\n".join(parts), pages, len(parts)

    async def _llm(self, model: str, system: str, user: str, num_predict: int) -> str:
        result = await self.ollama.chat(
            model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2, num_ctx=self.runtime.context_length, num_predict=num_predict,
            keep_alive=self.runtime.keep_alive,
        )
        return result.content

    async def summarize(self, doc: Document, req: SummarizeRequest) -> SummarizeResponse:
        self._require_ready(doc)
        task = TASKS[req.task]
        full_scan = req.full_scan if req.full_scan is not None else req.task == "summary"
        model = await self.ollama.resolve_model(req.model, self.runtime.default_model, self.runtime.fallback_model)
        has_pages = doc.file_type == "pdf"
        page_rule = "" if has_pages else " This document has no page numbers: do not cite pages."
        num_ctx, max_tokens = self.runtime.context_length, self.runtime.max_tokens
        # Leave room for instructions and the map output inside the context window.
        window_chars = min(self.settings.summary_window_chars, max(1500, (num_ctx - MAP_MAX_TOKENS - 400) * 3))
        total = self.chunks.count_for_document(doc.id)

        selected: list[DocumentChunk] | None = None
        if not full_scan:
            hits = get_retriever().search(self.db, task["query"], [doc.id], top_k=8)
            selected = sorted(self.chunks.get_by_ids([h.chunk_id for h in hits]), key=lambda c: c.chunk_index)
            if not selected:
                selected = list(self.chunks.iter_for_document(doc.id, batch_size=8))[:4]

        notes: list[str] = []
        processed = 0
        valid_pages: set[int] = set()
        windows_done = 0
        for window_text, pages, n_chunks in self._windows(doc, selected, window_chars):
            if windows_done >= self.settings.summary_max_windows:
                break
            windows_done += 1
            valid_pages |= pages
            processed += n_chunks
            user = f"Task: {task['instruction']}{page_rule}\n\n<section>\n{window_text}\n</section>"
            output = await self._llm(model, DOCUMENT_MAP_PROMPT, user, MAP_MAX_TOKENS)
            if output.strip() and output.strip().upper().rstrip(".") != "NONE":
                notes.append(output.strip())

        processed_chunks = processed
        coverage = "full" if (selected is None and processed_chunks >= total) else "partial"

        if not notes:
            result = f"The analysed text does not appear to report: {task['label'].lower()}."
        else:
            result = await self._reduce(model, task, notes, page_rule, max_tokens)

        if has_pages:
            result, pages_cited, removed = validate_page_refs(result, valid_pages)
        else:
            result, pages_cited, removed = validate_page_refs(result, set())
        note = ("Generated from all document chunks." if coverage == "full" else
                f"Generated from {processed_chunks} of {total} chunks selected by keyword retrieval; "
                "information elsewhere in the document may be missing.")
        return SummarizeResponse(task=req.task, result=result, model=model, chunks_processed=processed_chunks,
                                 total_chunks=total, coverage=coverage, pages_referenced=pages_cited,
                                 removed_invalid_page_refs=removed, note=note)

    async def _reduce(self, model: str, task: dict[str, str], notes: list[str], page_rule: str,
                      max_tokens: int) -> str:
        budget_chars = max(2000, (self.runtime.context_length - max_tokens - 300) * 3)
        while True:
            joined = "\n\n---\n\n".join(notes)
            if len(joined) <= budget_chars or len(notes) == 1:
                user = (f"Task: {task['instruction']}{page_rule}\n\nPartial notes from document sections:\n"
                        f"{joined[:budget_chars]}\n\nWrite the final {task['label'].lower()} with clear headings or bullets.")
                return await self._llm(model, DOCUMENT_REDUCE_PROMPT, user, max_tokens)
            # Too many notes for one context: reduce them in groups first.
            groups: list[list[str]] = [[]]
            size = 0
            for note in notes:
                if groups[-1] and size + len(note) > budget_chars:
                    groups.append([])
                    size = 0
                groups[-1].append(note)
                size += len(note)
            merged = []
            for group in groups:
                user = (f"Task: {task['instruction']}{page_rule}\n\nPartial notes:\n" + "\n\n---\n\n".join(group)
                        )[: budget_chars + 500]
                merged.append(await self._llm(model, DOCUMENT_REDUCE_PROMPT, user, MAP_MAX_TOKENS * 2))
            if len(merged) >= len(notes):  # safety: guarantee progress
                notes = ["\n".join(merged)[:budget_chars]]
            else:
                notes = merged
