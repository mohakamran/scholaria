"""Local lexical retrieval over document chunks.

Version 1 uses SQLite FTS5 (BM25 ranking) with a pure-Python fallback when FTS5
is unavailable. ``Retriever`` is a small protocol so that vector or hybrid
retrieval (ChromaDB, FAISS, local embeddings) can be added later without
changing callers.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Protocol

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.database.connection import fts_available
from app.database.models import Document, DocumentChunk

logger = get_logger("retrieval")

STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be because been before being below
    between both but by can could did do does doing down during each few for from further had has have
    having he her here hers herself him himself his how i if in into is it its itself just me more most my
    myself no nor not now of off on once only or other our ours ourselves out over own same she should so
    some such than that the their theirs them themselves then there these they this those through to too
    under until up very was we were what when where which while who whom why will with would you your
    yours yourself yourselves paper study article document author authors please tell explain describe
    give list show find according""".split()
)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,}|\d{2,}")


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    document_title: str
    chunk_index: int
    page_number: int | None
    content: str
    score: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class Retriever(Protocol):
    def search(self, db: Session, query: str, document_ids: list[int] | None, top_k: int) -> list[RetrievedChunk]:
        ...


def query_terms(query: str) -> list[str]:
    terms = [t.lower().strip("-") for t in _WORD.findall(query)]
    seen: list[str] = []
    for term in terms:
        if term and term not in STOPWORDS and term not in seen:
            seen.append(term)
    return seen[:24]


def _fts_query(terms: list[str]) -> str:
    # Quote every term so user input can never inject FTS5 syntax.
    return " OR ".join('"' + t.replace('"', "") + '"' for t in terms)


class LexicalRetriever:
    """BM25 retrieval via FTS5 with a term-frequency fallback."""

    def search(self, db: Session, query: str, document_ids: list[int] | None = None,
               top_k: int = 5) -> list[RetrievedChunk]:
        terms = query_terms(query)
        if not terms:
            return []
        if fts_available(db.get_bind()):  # type: ignore[arg-type]
            try:
                return self._search_fts(db, terms, document_ids, top_k)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("FTS search failed (%s); using fallback", type(exc).__name__)
        return self._search_fallback(db, terms, document_ids, top_k)

    def _search_fts(self, db: Session, terms: list[str], document_ids: list[int] | None,
                    top_k: int) -> list[RetrievedChunk]:
        sql = """
            SELECT c.id, c.document_id, c.chunk_index, c.page_number, c.content,
                   COALESCE(d.title, d.filename) AS title, bm25(document_chunks_fts) AS rank
            FROM document_chunks_fts
            JOIN document_chunks c ON c.id = document_chunks_fts.rowid
            JOIN documents d ON d.id = c.document_id
            WHERE document_chunks_fts MATCH :q
        """
        params: dict[str, object] = {"q": _fts_query(terms), "k": top_k}
        if document_ids:
            sql += " AND c.document_id IN :ids"
        sql += " ORDER BY rank LIMIT :k"
        stmt = text(sql)
        if document_ids:
            stmt = stmt.bindparams(bindparam("ids", expanding=True))
            params["ids"] = list(document_ids)
        rows = db.execute(stmt, params).all()
        return [
            RetrievedChunk(chunk_id=r[0], document_id=r[1], chunk_index=r[2], page_number=r[3], content=r[4],
                           document_title=r[5], score=round(-float(r[6]), 4))
            for r in rows
        ]

    def _search_fallback(self, db: Session, terms: list[str], document_ids: list[int] | None,
                         top_k: int) -> list[RetrievedChunk]:
        """Scan chunks in batches and score by term frequency (no FTS5)."""
        scored: list[tuple[float, DocumentChunk, str]] = []
        stmt = select(DocumentChunk, Document.title, Document.filename).join(Document)
        if document_ids:
            stmt = stmt.where(DocumentChunk.document_id.in_(document_ids))
        for chunk, title, filename in db.execute(stmt.execution_options(yield_per=200)):
            words = Counter(w.lower() for w in _WORD.findall(chunk.content))
            score = sum(words[t] for t in terms) + 2 * sum(1 for t in terms if words[t])
            if score:
                scored.append((score, chunk, title or filename))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            RetrievedChunk(chunk_id=c.id, document_id=c.document_id, chunk_index=c.chunk_index,
                           page_number=c.page_number, content=c.content, document_title=t, score=float(s))
            for s, c, t in scored[:top_k]
        ]


def get_retriever() -> Retriever:
    return LexicalRetriever()


def format_passages(chunks: list[RetrievedChunk], max_chars: int) -> tuple[str, list[dict[str, object]]]:
    """Render chunks as labelled passages and return source metadata for citation.

    Only the labels ([S1], [S2] ...) are given to the model; document titles and
    page numbers in the returned ``sources`` come from the database, never from
    the model.
    """
    blocks: list[str] = []
    sources: list[dict[str, object]] = []
    used = 0
    for chunk in chunks:
        label = f"S{len(sources) + 1}"
        content = chunk.content.replace("</passage>", "")
        location = f"page {chunk.page_number}" if chunk.page_number else "no page numbers"
        block = f'<passage id="{label}" document="{chunk.document_title}" location="{location}">\n{content}\n</passage>'
        if used + len(block) > max_chars and sources:
            break
        if used + len(block) > max_chars:
            block = block[: max_chars - 20] + "\n</passage>"
        blocks.append(block)
        used += len(block)
        sources.append({
            "label": label,
            "document_id": chunk.document_id,
            "document_title": chunk.document_title,
            "page_number": chunk.page_number,
            "chunk_index": chunk.chunk_index,
            "excerpt": chunk.content[:300],
            "score": chunk.score,
        })
    return "\n\n".join(blocks), sources


_CITE = re.compile(r"\[(S\d+)\]")


def cited_sources(answer: str, sources: list[dict[str, object]]) -> tuple[str, list[dict[str, object]]]:
    """Remove labels the model invented and return only the sources it actually cited."""
    valid = {str(s["label"]) for s in sources}
    cleaned = _CITE.sub(lambda m: m.group(0) if m.group(1) in valid else "", answer)
    used = {m for m in _CITE.findall(cleaned)}
    return cleaned, [s for s in sources if s["label"] in used]
