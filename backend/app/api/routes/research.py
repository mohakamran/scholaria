"""Research workspace: projects, notes, citations (bibliography)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.database.connection import get_db
from app.database.models import Citation, ResearchNote, User
from app.database.repositories import CitationRepository, NoteRepository, ProjectRepository
from app.schemas.common import Deleted
from app.schemas.research import (
    CitationCreate,
    CitationOut,
    CitationUpdate,
    NoteCreate,
    NoteOut,
    NoteUpdate,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
)

router = APIRouter(tags=["research"], dependencies=[Depends(current_user)])


def _project_out(repo: ProjectRepository, project) -> ProjectOut:  # type: ignore[no-untyped-def]
    out = ProjectOut.model_validate(project)
    out.counts = repo.counts(project.id)
    return out


# --- Projects ---------------------------------------------------------------
@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> ProjectOut:
    repo = ProjectRepository(db, user.id)
    return _project_out(repo, repo.create(**body.model_dump()))


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> list[ProjectOut]:
    repo = ProjectRepository(db, user.id)
    return [_project_out(repo, p) for p in repo.list()]


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> ProjectOut:
    repo = ProjectRepository(db, user.id)
    return _project_out(repo, repo.get_or_404(project_id))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, body: ProjectUpdate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> ProjectOut:
    repo = ProjectRepository(db, user.id)
    project = repo.update(repo.get_or_404(project_id), **body.model_dump(exclude_unset=True))
    return _project_out(repo, project)


@router.delete("/projects/{project_id}", response_model=Deleted)
def delete_project(project_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Deleted:
    """Deletes the project, its notes and citations. Documents, datasets and
    conversations are kept and simply unlinked from the project."""
    repo = ProjectRepository(db, user.id)
    repo.delete_project(repo.get_or_404(project_id))
    return Deleted(id=project_id)


# --- Notes --------------------------------------------------------------------
@router.get("/projects/{project_id}/notes", response_model=list[NoteOut])
def list_notes(project_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> list[ResearchNote]:
    ProjectRepository(db, user.id).get_or_404(project_id)
    return list(NoteRepository(db, user.id).list_for_project(project_id))


@router.post("/projects/{project_id}/notes", response_model=NoteOut, status_code=201)
def create_note(project_id: int, body: NoteCreate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> ResearchNote:
    ProjectRepository(db, user.id).get_or_404(project_id)
    return NoteRepository(db, user.id).create(project_id=project_id, **body.model_dump())


@router.get("/notes/{note_id}", response_model=NoteOut)
def get_note(note_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> ResearchNote:
    return NoteRepository(db, user.id).get_or_404(note_id)


@router.patch("/notes/{note_id}", response_model=NoteOut)
def update_note(note_id: int, body: NoteUpdate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> ResearchNote:
    repo = NoteRepository(db, user.id)
    return repo.update(repo.get_or_404(note_id), **body.model_dump(exclude_unset=True))


@router.delete("/notes/{note_id}", response_model=Deleted)
def delete_note(note_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Deleted:
    repo = NoteRepository(db, user.id)
    repo.delete(repo.get_or_404(note_id))
    return Deleted(id=note_id)


# --- Citations ------------------------------------------------------------------
@router.get("/projects/{project_id}/citations", response_model=list[CitationOut])
def list_citations(project_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> list[Citation]:
    ProjectRepository(db, user.id).get_or_404(project_id)
    return list(CitationRepository(db, user.id).list_for_project(project_id))


@router.post("/projects/{project_id}/citations", response_model=CitationOut, status_code=201)
def create_citation(project_id: int, body: CitationCreate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Citation:
    ProjectRepository(db, user.id).get_or_404(project_id)
    return CitationRepository(db, user.id).create(project_id=project_id, **body.model_dump())


@router.get("/citations/{citation_id}", response_model=CitationOut)
def get_citation(citation_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Citation:
    return CitationRepository(db, user.id).get_or_404(citation_id)


@router.patch("/citations/{citation_id}", response_model=CitationOut)
def update_citation(citation_id: int, body: CitationUpdate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Citation:
    repo = CitationRepository(db, user.id)
    return repo.update(repo.get_or_404(citation_id), **body.model_dump(exclude_unset=True))


@router.delete("/citations/{citation_id}", response_model=Deleted)
def delete_citation(citation_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Deleted:
    repo = CitationRepository(db, user.id)
    repo.delete(repo.get_or_404(citation_id))
    return Deleted(id=citation_id)


def _bibtex_escape(value: str) -> str:
    return re.sub(r"([{}%&$#_])", r"\\\1", value)


@router.get("/projects/{project_id}/citations/export", response_class=PlainTextResponse)
def export_bibtex(project_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> str:
    """Export the bibliography as BibTeX using only the stored fields."""
    ProjectRepository(db, user.id).get_or_404(project_id)
    entries = []
    for c in CitationRepository(db, user.id).list_for_project(project_id):
        first_author = re.sub(r"[^A-Za-z]", "", (c.authors or "anon").split(",")[0].split(" and ")[0])[:20] or "anon"
        key = f"{first_author.lower()}{c.publication_year or 'nd'}_{c.id}"
        fields = {"title": c.title, "author": c.authors, "year": c.publication_year, "journal": c.journal,
                  "doi": c.doi, "url": c.url}
        body = ",\n".join(f"  {k} = {{{_bibtex_escape(str(v))}}}" for k, v in fields.items() if v)
        entries.append(f"@article{{{key},\n{body}\n}}")
    return "\n\n".join(entries) + ("\n" if entries else "")
