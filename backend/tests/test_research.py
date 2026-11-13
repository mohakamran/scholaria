"""Projects, notes, citations and safe project deletion."""


def test_project_notes_citations(client, tmp_path):
    project = client.post("/api/projects", json={"title": "Sleep study", "objectives": "Measure sleep"}).json()
    pid = project["id"]
    note = client.post(f"/api/projects/{pid}/notes", json={"title": "Idea", "content": "Use actigraphy"})
    assert note.status_code == 201
    nid = note.json()["id"]
    assert client.patch(f"/api/notes/{nid}", json={"content": "Updated"}).json()["content"] == "Updated"
    assert len(client.get(f"/api/projects/{pid}/notes").json()) == 1

    ok = client.post(f"/api/projects/{pid}/citations", json={
        "title": "Sleep and memory", "authors": "Smith, J. and Doe, A.", "publication_year": 2020,
        "doi": "https://doi.org/10.1000/xyz123"})
    assert ok.status_code == 201 and ok.json()["doi"] == "10.1000/xyz123"
    bad = client.post(f"/api/projects/{pid}/citations", json={"title": "X", "doi": "not-a-doi"})
    assert bad.status_code == 422
    bib = client.get(f"/api/projects/{pid}/citations/export").text
    assert "@article{smith2020" in bib and "10.1000/xyz123" in bib
    assert client.get(f"/api/projects/{pid}").json()["counts"]["citations"] == 1


def test_project_delete_keeps_documents_and_conversations(client, tmp_path):
    pid = client.post("/api/projects", json={"title": "Temp"}).json()["id"]
    txt = tmp_path / "notes.txt"
    txt.write_text("Research notes about qualitative interviews.")
    with txt.open("rb") as fh:
        doc = client.post("/api/documents/upload", files={"file": ("notes.txt", fh)}, data={"project_id": pid}).json()
    conv = client.post("/api/conversations", json={"title": "c", "project_id": pid}).json()
    client.post(f"/api/projects/{pid}/notes", json={"title": "n"})
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/documents/{doc['id']}").json()["project_id"] is None
    assert client.get(f"/api/conversations/{conv['id']}").json()["project_id"] is None
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_project_validation(client):
    assert client.post("/api/projects", json={"title": ""}).status_code == 422
    assert client.get("/api/projects/999").status_code == 404
    assert client.get("/api/projects/999").json()["error"]["code"] == "not_found"


def test_activity_feed(client):
    pid = client.post("/api/projects", json={"title": "A"}).json()["id"]
    client.post(f"/api/projects/{pid}/notes", json={"title": "First note"})
    items = client.get("/api/activity").json()
    assert items[0]["type"] == "note"
