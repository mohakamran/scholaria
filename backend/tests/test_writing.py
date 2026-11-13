"""Academic writing workflows."""

from tests.conftest import parse_sse


def test_writing_tasks_listed(client):
    ids = {t["id"] for t in client.get("/api/writing/tasks").json()}
    assert {"abstract", "hypotheses", "methodology", "latex", "paraphrase"} <= ids


def test_generate_streams_and_uses_bibliography(client, fake_ollama):
    pid = client.post("/api/projects", json={"title": "Remote work", "research_questions": "RQ1"}).json()["id"]
    client.post(f"/api/projects/{pid}/citations", json={"title": "Remote work review", "authors": "Lee, K.",
                                                        "publication_year": 2021})
    fake_ollama.reply = "Draft abstract text"
    response = client.post("/api/writing/generate", json={
        "task": "abstract", "text": "We studied remote workers.", "project_id": pid,
        "include_project_context": True, "include_bibliography": True})
    events = parse_sse(response.text)
    assert events[-1][0] == "done"
    assert "".join(d["content"] for e, d in events if e == "token").strip() == "Draft abstract text"
    sent = fake_ollama.requests[-1]["messages"]
    assert "Never invent citations" in sent[0]["content"]
    assert "Lee, K. (2021) Remote work review" in sent[1]["content"]


def test_generate_validation(client):
    assert client.post("/api/writing/generate", json={"task": "unknown_task", "text": "x"}).status_code == 422
    assert client.post("/api/writing/generate", json={"task": "abstract", "text": ""}).status_code == 422
    too_long = client.post("/api/writing/generate", json={"task": "grammar", "text": "word " * 5000})
    assert too_long.status_code == 422


def test_unsupported_citation_is_flagged(client, fake_ollama):
    pid = client.post("/api/projects", json={"title": "P"}).json()["id"]
    client.post(f"/api/projects/{pid}/citations", json={"title": "No notes", "authors": "Smith, J.", "publication_year": 2020})
    client.post(f"/api/projects/{pid}/citations", json={"title": "With notes", "authors": "Lee, K.", "publication_year": 2021,
                                                        "notes": "Found a positive effect."})
    fake_ollama.reply = "Smith (2020) demonstrates X. Lee (2021) found a positive effect."
    events = parse_sse(client.post("/api/writing/generate", json={
        "task": "discussion", "text": "Discuss.", "project_id": pid, "include_bibliography": True}).text)
    warnings = events[-1][1]["warnings"]
    assert len(warnings) == 1 and "Smith (2020)" in warnings[0]
    sent = fake_ollama.requests[-1]["messages"][1]["content"]
    assert "User's notes on what this source reports: Found a positive effect." in sent
