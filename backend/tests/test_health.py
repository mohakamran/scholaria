"""Health endpoint, database connectivity and Ollama status."""

from sqlalchemy import text

from app.database.connection import check_database, get_engine


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_database_connection_and_fts(client):
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    info = check_database()
    assert info["ok"] is True and info["fts5"] is True


def test_system_status_reports_ollama_connected(client):
    data = client.get("/api/system/status").json()
    assert data["ollama"]["connected"] is True
    assert data["ollama"]["default_model_available"] is True
    assert data["database"]["ok"] is True
    # No absolute filesystem paths are exposed.
    assert "/" not in str(data["database"].get("file"))


def test_ollama_down_is_reported_not_crashing(client, fake_ollama):
    fake_ollama.mode = "down"
    data = client.get("/api/system/status").json()
    assert data["ollama"]["connected"] is False
    assert "Cannot connect to Ollama" in data["ollama"]["error"]
    response = client.get("/api/models")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ollama_unavailable"


def test_default_assistants_seeded(client):
    assistants = client.get("/api/assistants").json()
    assert len(assistants) == 4
    assert sum(a["is_default"] for a in assistants) == 1


def test_settings_update_and_local_only_ollama(client):
    response = client.put("/api/settings", json={"temperature": 0.2, "context_length": 2048})
    assert response.status_code == 200
    assert response.json()["temperature"] == 0.2
    bad = client.put("/api/settings", json={"ollama_url": "http://example.com:11434"})
    assert bad.status_code == 422


def test_model_select(client, fake_ollama):
    assert client.post("/api/models/select", json={"model": "qwen3:1.7b"}).status_code == 200
    assert client.get("/api/settings").json()["default_model"] == "qwen3:1.7b"
    assert client.post("/api/models/select", json={"model": "missing:7b"}).status_code == 404
