"""Sign-up, login, sessions and per-account data isolation."""

from datetime import timedelta

from sqlalchemy import update

from app.database.connection import get_sessionmaker
from app.database.models import AuthSession, utcnow
from app.services.auth_service import hash_password, verify_password
from tests.conftest import TEST_PASSWORD, login_as


def test_password_hashing():
    stored = hash_password("Secret123")
    assert stored.startswith("scrypt$") and "Secret123" not in stored
    assert verify_password("Secret123", stored) and not verify_password("secret123", stored)
    assert hash_password("Secret123") != stored  # random salt


def test_signup_validation(anon_client):
    ok = anon_client.post("/api/auth/signup", json={"email": "A@Example.com", "full_name": "Ana", "password": "Passw0rd!"})
    assert ok.status_code == 201 and ok.json()["email"] == "a@example.com"
    assert "password" not in str(ok.json()).lower()
    dup = anon_client.post("/api/auth/signup", json={"email": "a@example.com", "full_name": "Ana", "password": "Passw0rd!"})
    assert dup.status_code == 409
    for body, msg in [({"email": "bad", "full_name": "x", "password": "Passw0rd1"}, "valid email"),
                      ({"email": "b@example.com", "full_name": "x", "password": "short1"}, "at least 8"),
                      ({"email": "b@example.com", "full_name": "x", "password": "onlyletters"}, "letter and one number"),
                      ({"email": "b@example.com", "full_name": "   ", "password": "Passw0rd1"}, "name")]:
        r = anon_client.post("/api/auth/signup", json=body)
        assert r.status_code == 422 and msg in r.json()["error"]["message"]


def test_login_logout_and_me(anon_client):
    anon_client.post("/api/auth/signup", json={"email": "u@example.com", "full_name": "U", "password": TEST_PASSWORD})
    wrong = anon_client.post("/api/auth/login", json={"email": "u@example.com", "password": "Wrong1234"})
    assert wrong.status_code == 401 and wrong.json()["error"]["message"] == "Incorrect email or password."
    unknown = anon_client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "Wrong1234"})
    assert unknown.json()["error"]["message"] == "Incorrect email or password."  # no account enumeration
    token = anon_client.post("/api/auth/login", json={"email": "U@example.com", "password": TEST_PASSWORD}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert anon_client.get("/api/auth/me", headers=headers).json()["email"] == "u@example.com"
    assert anon_client.post("/api/auth/logout", headers=headers).status_code == 200
    assert anon_client.get("/api/auth/me", headers=headers).status_code == 401


def test_protected_endpoints_require_login(anon_client):
    assert anon_client.get("/api/health").status_code == 200  # public
    for path in ["/api/conversations", "/api/projects", "/api/documents", "/api/datasets", "/api/settings",
                 "/api/system/status", "/api/models", "/api/writing/tasks", "/api/activity"]:
        r = anon_client.get(path)
        assert r.status_code == 401, path
        assert r.json()["error"]["code"] == "unauthorized"
    assert anon_client.get("/api/projects", headers={"Authorization": "Bearer forged"}).status_code == 401


def test_login_throttling(anon_client):
    anon_client.post("/api/auth/signup", json={"email": "t@example.com", "full_name": "T", "password": TEST_PASSWORD})
    for _ in range(5):
        anon_client.post("/api/auth/login", json={"email": "t@example.com", "password": "Wrong1234"})
    blocked = anon_client.post("/api/auth/login", json={"email": "t@example.com", "password": TEST_PASSWORD})
    assert blocked.status_code == 429


def test_expired_session_rejected(anon_client):
    token = login_as(anon_client, "e@example.com")
    with get_sessionmaker()() as db:
        db.execute(update(AuthSession).values(expires_at=utcnow() - timedelta(minutes=1)))
        db.commit()
    r = anon_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and "expired" in r.json()["error"]["message"]


def test_profile_theme_and_password_change(client):
    assert client.patch("/api/auth/me", json={"theme": "dark", "full_name": "New Name"}).json()["theme"] == "dark"
    assert client.patch("/api/auth/me", json={"theme": "purple"}).status_code == 422
    bad = client.post("/api/auth/change-password", json={"current_password": "nope", "new_password": "Another123"})
    assert bad.status_code == 422
    ok = client.post("/api/auth/change-password", json={"current_password": TEST_PASSWORD, "new_password": "Another123"})
    assert ok.status_code == 200
    assert client.post("/api/auth/login", json={"email": "researcher@example.com", "password": "Another123"}).status_code == 200


def test_accounts_cannot_see_each_others_data(anon_client, sample_pdf, fake_ollama):
    alice = {"Authorization": f"Bearer {login_as(anon_client, 'alice@example.com')}"}
    bob = {"Authorization": f"Bearer {login_as(anon_client, 'bob@example.com')}"}
    project = anon_client.post("/api/projects", json={"title": "Alice secret"}, headers=alice).json()
    note = anon_client.post(f"/api/projects/{project['id']}/notes", json={"title": "n"}, headers=alice).json()
    chat = anon_client.post("/api/chat", json={"message": "hello"}, headers=alice).json()
    with sample_pdf.open("rb") as fh:
        doc = anon_client.post("/api/documents/upload", files={"file": ("p.pdf", fh)}, headers=alice).json()

    assert anon_client.get("/api/projects", headers=bob).json() == []
    assert anon_client.get("/api/conversations", headers=bob).json() == []
    assert anon_client.get("/api/documents", headers=bob).json() == []
    assert anon_client.get("/api/activity", headers=bob).json() == []
    for path in [f"/api/projects/{project['id']}", f"/api/notes/{note['id']}",
                 f"/api/conversations/{chat['conversation_id']}", f"/api/documents/{doc['id']}"]:
        assert anon_client.get(path, headers=bob).status_code == 404, path
    assert anon_client.delete(f"/api/conversations/{chat['conversation_id']}", headers=bob).status_code == 404
    assert anon_client.patch(f"/api/projects/{project['id']}", json={"title": "x"}, headers=bob).status_code == 404
    assert anon_client.post(f"/api/documents/{doc['id']}/ask", json={"question": "students surveyed"},
                            headers=bob).status_code == 404
    # Bob cannot pull Alice's document into his chat via document_ids.
    fake_ollama.reply = "No evidence."
    anon_client.post("/api/chat", json={"message": "How many students were surveyed?", "document_ids": [doc["id"]]},
                     headers=bob)
    assert "250 graduate students" not in fake_ollama.requests[-1]["messages"][-1]["content"]
    # Same file can be uploaded by another account (duplicates are per account).
    with sample_pdf.open("rb") as fh:
        assert anon_client.post("/api/documents/upload", files={"file": ("p.pdf", fh)}, headers=bob).status_code == 201


def test_first_account_claims_existing_data(anon_client):
    from app.database.models import Project

    with get_sessionmaker()() as db:
        db.add(Project(title="Created before accounts"))
        db.commit()
    headers = {"Authorization": f"Bearer {login_as(anon_client, 'first@example.com')}"}
    assert [p["title"] for p in anon_client.get("/api/projects", headers=headers).json()] == ["Created before accounts"]
    other = {"Authorization": f"Bearer {login_as(anon_client, 'second@example.com')}"}
    assert anon_client.get("/api/projects", headers=other).json() == []
