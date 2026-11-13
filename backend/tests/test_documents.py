"""Document upload validation, extraction, chunking and grounded Q&A."""

import io
from pathlib import Path

import docx

from app.services.document_service import chunk_text, iter_segments, validate_page_refs
from app.services.retrieval_service import cited_sources, query_terms


def upload(client, path: Path, name: str | None = None, **form):
    with path.open("rb") as fh:
        return client.post("/api/documents/upload", files={"file": (name or path.name, fh)}, data=form)


def test_pdf_text_extraction_preserves_pages(sample_pdf):
    segments = list(iter_segments(sample_pdf, "pdf"))
    assert [s.page_number for s in segments] == [1, 2, 3]
    assert "250 graduate students" in segments[1].text


def test_chunking_respects_size_and_overlap():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(10))
    chunks = chunk_text(text, size=500, overlap=80)
    assert len(chunks) > 3
    assert all(len(c) <= 600 for c in chunks)
    # Overlap: each chunk after the first starts with text from the previous chunk.
    assert chunks[1].split("\n\n")[0] in chunks[0]
    assert chunk_text("   ") == []


def test_chunking_splits_very_long_sentence():
    chunks = chunk_text("a" * 50 + " " + "b" * 3000, size=1000, overlap=0)
    assert all(len(c) <= 1000 for c in chunks)


def test_upload_and_process_pdf(client, sample_pdf, storage):
    response = upload(client, sample_pdf)
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["processing_status"] == "ready"
    assert doc["page_count"] == 3 and doc["chunk_count"] >= 3
    assert doc["title"] == "A Study of Coffee and Productivity"
    assert doc["metadata"]["author"] == "Test Author"
    # Stored under a random internal name, never the user's filename.
    stored = list((storage / "uploads").iterdir())
    assert len(stored) == 1 and stored[0].name != "paper.pdf" and stored[0].suffix == ".pdf"
    chunks = client.get(f"/api/documents/{doc['id']}/chunks", params={"page": 2}).json()
    assert chunks and all(c["page_number"] == 2 for c in chunks)


def test_upload_validation(client, tmp_path, sample_pdf):
    exe = tmp_path / "evil.exe"
    exe.write_bytes(b"MZ....")
    assert upload(client, exe).status_code == 422
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_text("not a pdf")
    r = upload(client, fake_pdf)
    assert r.status_code == 422 and "not a valid PDF" in r.json()["error"]["message"]
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    assert upload(client, empty).status_code == 422
    # Size limit is enforced while streaming.
    client.put("/api/settings", json={"max_upload_mb": 1})
    big = tmp_path / "big.txt"
    big.write_text("x" * (1024 * 1024 + 10))
    assert upload(client, big).status_code == 413


def test_path_traversal_filename_is_neutralised(client, tmp_path, storage):
    note = tmp_path / "note.txt"
    note.write_text("Plain text research notes about methodology and sampling.")
    response = upload(client, note, name="../../../etc/passwd.txt")
    assert response.status_code == 201
    assert response.json()["filename"] == "passwd.txt"
    assert not (storage.parent / "etc").exists()


def test_duplicate_upload_conflict(client, sample_pdf):
    first = upload(client, sample_pdf).json()
    second = upload(client, sample_pdf)
    assert second.status_code == 409
    assert second.json()["error"]["details"]["document_id"] == first["id"]


def test_docx_upload(client, tmp_path):
    path = tmp_path / "draft.docx"
    document = docx.Document()
    document.add_paragraph("The theoretical framework is the Technology Acceptance Model.")
    document.add_paragraph("Participants were 120 nurses.")
    document.save(path)
    doc = upload(client, path).json()
    assert doc["processing_status"] == "ready" and doc["page_count"] is None


def test_ask_returns_grounded_page_references(client, sample_pdf, fake_ollama):
    doc_id = upload(client, sample_pdf).json()["id"]
    fake_ollama.reply = "The study surveyed 250 graduate students [S1]. Also see [S9]."
    response = client.post(f"/api/documents/{doc_id}/ask", json={"question": "How many students were surveyed?"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["grounded"] is True
    assert "[S9]" not in data["answer"]  # invented label removed
    assert data["sources"][0]["label"] == "S1"
    assert data["sources"][0]["page_number"] == 2  # page comes from the database, not the model
    assert data["sources"][0]["document_title"] == "A Study of Coffee and Productivity"
    assert "not a verification against the full document" in data["note"]
    prompt = fake_ollama.requests[-1]["messages"][1]["content"]
    assert "<passage id=\"S1\"" in prompt and "250 graduate students" in prompt


def test_ask_without_evidence_does_not_call_model(client, sample_pdf, fake_ollama):
    doc_id = upload(client, sample_pdf).json()["id"]
    calls = len(fake_ollama.requests)
    data = client.post(f"/api/documents/{doc_id}/ask", json={"question": "quantum chromodynamics lattice"}).json()
    assert data["grounded"] is False and data["sources"] == []
    assert len(fake_ollama.requests) == calls


def test_summarize_removes_invented_page_refs(client, sample_pdf, fake_ollama):
    doc_id = upload(client, sample_pdf).json()["id"]
    fake_ollama.reply = "Survey of students (p. 2). Invented claim (p. 99)."
    data = client.post(f"/api/documents/{doc_id}/summarize", json={"task": "summary"}).json()
    assert data["coverage"] == "full"
    assert "(p. 2)" in data["result"] and "(p. 99)" not in data["result"]
    assert data["removed_invalid_page_refs"] >= 1 and data["pages_referenced"] == [2]


def test_targeted_extraction_is_partial(client, sample_pdf, fake_ollama):
    doc_id = upload(client, sample_pdf).json()["id"]
    data = client.post(f"/api/documents/{doc_id}/summarize", json={"task": "methodology"}).json()
    assert data["coverage"] == "partial" or data["chunks_processed"] == data["total_chunks"]


def test_chat_with_documents_stores_sources(client, sample_pdf, fake_ollama):
    doc_id = upload(client, sample_pdf).json()["id"]
    fake_ollama.reply = "Moderate caffeine helped [S1]."
    body = client.post("/api/chat", json={"message": "What did caffeine intake do?", "document_ids": [doc_id]}).json()
    sources = body["assistant_message"]["sources"]
    assert sources and sources[0]["page_number"] in (2, 3) and sources[0]["label"] == "S1"


def test_delete_document_removes_file_and_chunks(client, sample_pdf, storage):
    doc_id = upload(client, sample_pdf).json()["id"]
    assert client.delete(f"/api/documents/{doc_id}").status_code == 200
    assert list((storage / "uploads").iterdir()) == []
    assert client.get(f"/api/documents/{doc_id}").status_code == 404


def test_retrieval_helpers():
    assert query_terms("What is the sample size of the study?") == ["sample", "size"]
    answer, used = cited_sources("A [S1] B [S3]", [{"label": "S1"}, {"label": "S2"}])
    assert answer == "A [S1] B " and [u["label"] for u in used] == ["S1"]
    text, pages, removed = validate_page_refs("x (p. 1) y (pp. 2-5)", {1, 2})
    assert pages == [1] and removed == 1
