"""Conversations, message persistence, history and streaming."""

from app.database.models import Message
from app.services.chat_service import build_history
from app.services.ollama_service import ThinkFilter, classify_error
from tests.conftest import parse_sse


def test_conversation_crud(client):
    created = client.post("/api/conversations", json={"title": "My chat"})
    assert created.status_code == 201
    conv_id = created.json()["id"]
    assert client.patch(f"/api/conversations/{conv_id}", json={"title": "Renamed"}).json()["title"] == "Renamed"
    assert [c["id"] for c in client.get("/api/conversations").json()] == [conv_id]
    assert client.delete(f"/api/conversations/{conv_id}").status_code == 200
    assert client.get(f"/api/conversations/{conv_id}").status_code == 404


def test_chat_persists_messages(client, fake_ollama):
    fake_ollama.reply = "Hello researcher"
    response = client.post("/api/chat", json={"message": "Hi there"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assistant_message"]["content"] == "Hello researcher"
    detail = client.get(f"/api/conversations/{body['conversation_id']}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["title"] == "Hi there"
    # Options always carry the memory-safe limits.
    options = fake_ollama.requests[-1]["options"]
    assert options["num_ctx"] == 4096 and options["num_predict"] == 1024
    assert fake_ollama.requests[-1]["keep_alive"] == "2m"


def test_history_is_used_for_follow_up(client, fake_ollama):
    first = client.post("/api/chat", json={"message": "My topic is sleep research"}).json()
    client.post("/api/chat", json={"message": "What was my topic?", "conversation_id": first["conversation_id"]})
    sent = fake_ollama.requests[-1]["messages"]
    contents = [m["content"] for m in sent]
    assert "My topic is sleep research" in contents
    assert sent[0]["role"] == "system" and sent[-1]["content"] == "What was my topic?"


def test_streaming_response_and_persistence(client, fake_ollama):
    fake_ollama.reply = "Streaming works fine"
    response = client.post("/api/chat/stream", json={"message": "Stream please"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    names = [e for e, _ in events]
    assert names[0] == "meta" and names[-1] == "done" and "token" in names
    text = "".join(d["content"] for e, d in events if e == "token")
    assert text.strip() == "Streaming works fine"
    conv_id = events[0][1]["conversation_id"]
    messages = client.get(f"/api/conversations/{conv_id}").json()["messages"]
    assert messages[-1]["content"] == "Streaming works fine" and messages[-1]["status"] == "complete"


def test_stream_midstream_failure_persists_partial(client, fake_ollama):
    fake_ollama.mode = "midstream_error"
    fake_ollama.chunks = ["Partial ", "answer"]
    events = parse_sse(client.post("/api/chat/stream", json={"message": "Go"}).text)
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "insufficient_memory"
    conv_id = events[0][1]["conversation_id"]
    last = client.get(f"/api/conversations/{conv_id}").json()["messages"][-1]
    assert last["role"] == "assistant" and last["status"] == "error" and last["content"] == "Partial answer"


def test_ollama_connection_failure(client, fake_ollama):
    fake_ollama.mode = "down"
    response = client.post("/api/chat", json={"message": "Hello"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ollama_unavailable"
    stream = client.post("/api/chat/stream", json={"message": "Hello"})
    assert stream.status_code == 503


def test_model_memory_error_is_explicit(client, fake_ollama):
    fake_ollama.mode = "memory"
    response = client.post("/api/chat", json={"message": "Hello"})
    assert response.status_code == 507
    assert "memory" in response.json()["error"]["message"].lower()


def test_fallback_model_used_when_default_missing(client, fake_ollama):
    fake_ollama.models = ["qwen3:1.7b"]
    assert client.post("/api/chat", json={"message": "Hi"}).json()["model"] == "qwen3:1.7b"
    fake_ollama.models = []
    response = client.post("/api/chat", json={"message": "Hi"})
    assert response.status_code == 404 and "ollama pull" in response.json()["error"]["message"]


def test_regenerate_replaces_last_reply(client, fake_ollama):
    fake_ollama.reply = "First"
    conv_id = client.post("/api/chat", json={"message": "Question"}).json()["conversation_id"]
    fake_ollama.reply = "Second"
    events = parse_sse(client.post(f"/api/chat/{conv_id}/regenerate", json={}).text)
    assert events[-1][0] == "done"
    messages = client.get(f"/api/conversations/{conv_id}").json()["messages"]
    assert [m["content"].strip() for m in messages] == ["Question", "Second"]


def test_clear_conversation(client, fake_ollama):
    conv_id = client.post("/api/chat", json={"message": "Q"}).json()["conversation_id"]
    assert client.delete(f"/api/conversations/{conv_id}/messages").json()["deleted_messages"] == 2
    assert client.get(f"/api/conversations/{conv_id}").json()["messages"] == []


def test_blank_message_rejected(client):
    response = client.post("/api/chat", json={"message": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_history_budget_trims_oldest():
    history = [Message(id=i, role="user" if i % 2 == 0 else "assistant", content="x" * 400, conversation_id=1)
               for i in range(20)]
    selected, dropped = build_history(history, budget_tokens=500, max_messages=12)
    assert len(selected) < 12 and dropped > 0
    assert selected[0]["role"] == "user"
    assert sum(len(m["content"]) for m in selected) // 4 <= 500


def test_think_filter_strips_reasoning():
    f = ThinkFilter()
    out = "".join(f.feed(p) for p in ["<thi", "nk>secret reasoning</th", "ink>\nVisible ", "answer"]) + f.flush()
    assert out == "Visible answer"


def test_error_classification():
    assert classify_error("model 'x' not found", 404).code == "model_not_found"
    assert classify_error("requires more system memory").code == "insufficient_memory"


def test_auto_title_cuts_at_word_boundary():
    from app.services.chat_service import make_title

    assert make_title("Short question?") == "Short question?"
    title = make_title("What are the main threats to validity in a cross-sectional survey design?")
    assert title.endswith("…") and len(title) <= 49 and "cross-sectional s…" not in title
    assert make_title("   ") == "New conversation"
