"""Module A — ChatGPT-style research chat with streaming, history and grounding."""

from __future__ import annotations

import time
from typing import Any

import streamlit as st

from components import api_client as api
from components.ui import call, copy_box, fmt_time, render_sources, show_error

ss = st.session_state
ss.setdefault("conversation_id", None)

ss.setdefault("chat_settings", {})


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=30, show_spinner=False)
def load_models() -> tuple[list[str], str | None]:
    data = api.safe_get("/models")
    if not data:
        return [], None
    return [m["name"] for m in data["models"]], data.get("default_model")


@st.cache_data(ttl=30, show_spinner=False)
def load_assistants() -> list[dict[str, Any]]:
    return api.safe_get("/assistants", default=[]) or []


@st.cache_data(ttl=30, show_spinner=False)
def load_runtime_settings() -> dict[str, Any]:
    return api.safe_get("/settings", default={}) or {}


def ready_documents() -> list[dict[str, Any]]:
    docs = api.safe_get("/documents", default=[]) or []
    return [d for d in docs if d["processing_status"] == "ready"]


def stop_generation() -> None:
    conv_id = ss.get("streaming_conversation_id")
    if conv_id:
        try:
            api.post(f"/chat/{conv_id}/stop")
            time.sleep(0.4)  # give the backend a moment to persist the partial reply
        except api.ApiError:
            pass
    ss["streaming_conversation_id"] = None


# --------------------------------------------------------------------------- #
# Header: model, assistant and generation settings
# --------------------------------------------------------------------------- #
models, default_model = load_models()
assistants = load_assistants()
runtime = load_runtime_settings()

conversation: dict[str, Any] | None = None
if ss["conversation_id"]:
    conversation = api.safe_get(f"/conversations/{ss['conversation_id']}")
    if conversation is None:
        ss["conversation_id"] = None

st.title(conversation["title"] if conversation else "New chat")
if not conversation:
    st.markdown("<div class='sch-subtitle'>Ask anything about research design, methods, statistics or your field. "
                "Attach your papers below to get answers with page references.</div>", unsafe_allow_html=True)

col_model, col_assistant, col_settings = st.columns([3, 3, 1.4], vertical_alignment="bottom")
with col_model:
    if models:
        preferred = (conversation or {}).get("model_name") or default_model
        index = models.index(preferred) if preferred in models else 0
        model = st.selectbox("Model", models, index=index, key="chat_model")
    else:
        model = None
        st.selectbox("Model", ["No model available"], disabled=True)
with col_assistant:
    names = [a["name"] for a in assistants] or ["Research Assistant"]
    default_idx = 0
    conv_assistant = (conversation or {}).get("assistant_id")
    for i, a in enumerate(assistants):
        if (conv_assistant and a["id"] == conv_assistant) or (not conv_assistant and a["is_default"]):
            default_idx = i
    assistant_name = st.selectbox("Assistant", names, index=default_idx, key="chat_assistant",
                                  help="Each assistant uses a different system prompt (manage them in Settings).")
    assistant_id = next((a["id"] for a in assistants if a["name"] == assistant_name), None)
with col_settings:
    with st.popover("Settings", icon=":material/tune:", width="stretch"):
        temperature = st.slider("Temperature", 0.0, 1.5, float(runtime.get("temperature", 0.6)), 0.05,
                                help="Lower = more focused and deterministic.")
        ctx_options = [2048, 4096, 6144, 8192]
        ctx_default = int(runtime.get("context_length", 4096))
        context_length = st.select_slider("Context length (tokens)", ctx_options,
                                          value=ctx_default if ctx_default in ctx_options else 4096,
                                          help="Larger contexts need more memory. 4096 is recommended on 8 GB.")
        max_tokens = st.select_slider("Max response tokens", [256, 512, 768, 1024, 1536, 2048],
                                      value=min(int(runtime.get("max_tokens", 1024)), 2048))

if not models:
    status = api.safe_get("/models/status")
    if status is None:
        st.error("The Scholaria service is not responding. Restart it with `./scripts/start.sh`.")
    elif not status.get("connected"):
        st.warning("The local AI engine (Ollama) is not running. Start it with `ollama serve`, then reload.")
    else:
        st.warning("No AI model is installed. Run `ollama pull qwen3:4b-instruct-2507-q4_K_M` in Terminal.")

# Grounding in uploaded documents (optional)
docs = ready_documents()
doc_labels = {f"{d['title'] or d['filename']} (#{d['id']})": d["id"] for d in docs}
selected_docs: list[int] = []
if doc_labels:
    chosen = st.multiselect("Ground answers in documents (optional)", list(doc_labels), key="chat_docs",
                            placeholder="Answer from general knowledge",
                            help="Relevant passages are retrieved and cited with page numbers.")
    selected_docs = [doc_labels[c] for c in chosen]

# Conversation actions
if conversation:
    actions = st.container(horizontal=True, vertical_alignment="center")
    with actions:
        with st.popover("Rename", icon=":material/edit:"):
            new_title = st.text_input("Title", value=conversation["title"], key="rename_title")
            if st.button("Save", key="rename_save"):
                if call(api.patch, f"/conversations/{conversation['id']}", json={"title": new_title.strip()}):
                    st.cache_data.clear()
                    st.rerun()
    with actions:
        if st.button("Clear", icon=":material/mop:", help="Delete all messages but keep the conversation"):
            call(api.delete, f"/conversations/{conversation['id']}/messages")
            st.rerun()
    with actions:
        with st.popover("Delete", icon=":material/delete:"):
            st.write("Delete this conversation permanently?")
            if st.button("Yes, delete", type="primary", key="confirm_delete_conv"):
                call(api.delete, f"/conversations/{conversation['id']}")
                ss["conversation_id"] = None
                st.cache_data.clear()
                st.rerun()
    actions.caption(f"{len(conversation['messages'])} messages · model: {conversation.get('model_name') or '—'}")

st.divider()

# --------------------------------------------------------------------------- #
# Message history
# --------------------------------------------------------------------------- #
messages = conversation["messages"] if conversation else []
SUGGESTIONS = [
    (":material/schema:", "Mediation vs. moderation, with an example",
     "Explain the difference between mediation and moderation, with a short example for each."),
    (":material/biotech:", "Qualitative or quantitative design?",
     "How do I decide between a qualitative and a quantitative research design for my study?"),
    (":material/calculate:", "Sample size for a regression with 5 predictors",
     "How large should my sample be for a multiple regression with 5 predictors, and why?"),
    (":material/rate_review:", "Writing a strong research gap statement",
     "What makes a strong research gap statement in a literature review? Give a template."),
]
# Read the input first so the starter suggestions disappear as soon as a question is sent.
prompt = st.chat_input("Ask a research question…", disabled=not models) or ss.pop("pending_prompt", None)
if not messages and not prompt:
    st.caption("Try one of these to get started:")
    grid = st.columns(2)
    for i, (icon, label, text) in enumerate(SUGGESTIONS):
        if grid[i % 2].button(label, key=f"suggest_{i}", icon=icon, width="stretch", help=text):
            ss["pending_prompt"] = text
            st.rerun()
    st.caption("Answers are generated locally. Verify important facts, and never rely on references you have not checked.")

STATUS_NOTE = {"stopped": "Generation stopped", "error": "Generation failed (partial response)",
               "interrupted": "Interrupted (partial response)"}

for i, msg in enumerate(messages):
    with st.chat_message(msg["role"], avatar=":material/person:" if msg["role"] == "user" else ":material/smart_toy:"):
        st.markdown(msg["content"])
        meta = [fmt_time(msg["created_at"])]
        if msg["role"] == "assistant":
            if msg.get("model_name"):
                meta.append(msg["model_name"])
            if msg.get("status") in STATUS_NOTE:
                meta.append(f":orange[{STATUS_NOTE[msg['status']]}]")
        st.caption(" · ".join(meta))
        if msg["role"] == "assistant":
            render_sources(msg.get("sources"))
            row = st.container(horizontal=True)
            with row:
                copy_box(msg["content"], key=f"copy_{msg['id']}")
            if i == len(messages) - 1:
                with row:
                    if st.button("Regenerate", key=f"regen_{msg['id']}", icon=":material/refresh:"):
                        ss["pending_regenerate"] = True
                        st.rerun()

# A trailing user message without a reply (e.g. after an error) can be retried.
if messages and messages[-1]["role"] == "user" and not ss.get("pending_regenerate"):
    if st.button("Retry response", icon=":material/refresh:"):
        ss["pending_regenerate"] = True
        st.rerun()


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #
def run_stream(path: str, payload: dict[str, Any]) -> None:
    state: dict[str, Any] = {}

    def tokens():
        for event, data in api.stream_sse(path, payload):
            if event == "meta":
                state["meta"] = data
                ss["streaming_conversation_id"] = data["conversation_id"]
                ss["conversation_id"] = data["conversation_id"]
            elif event == "token":
                yield data["content"]
            elif event == "done":
                state["done"] = data
            elif event == "error":
                state["error"] = data

    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        st.button("Stop generating", icon=":material/stop_circle:", on_click=stop_generation, key="stop_btn")
        try:
            st.write_stream(tokens())
        except api.ApiError as exc:
            show_error(exc)
            ss["streaming_conversation_id"] = None
            return
    ss["streaming_conversation_id"] = None
    if "error" in state:
        err = state["error"]
        show_error(api.ApiError(err.get("message", "Generation failed"), code=err.get("code")))
        st.button("Dismiss", key="dismiss_err")
        return
    st.cache_data.clear()
    st.rerun()


generation_payload = {
    "model": model, "temperature": temperature, "context_length": context_length, "max_tokens": max_tokens,
    "assistant_id": assistant_id, "document_ids": selected_docs or None,
}

if ss.pop("pending_regenerate", False) and conversation:
    run_stream(f"/chat/{conversation['id']}/regenerate", generation_payload)

if prompt and models:
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(prompt)
    run_stream("/chat/stream", {**generation_payload, "message": prompt,
                                "conversation_id": ss.get("conversation_id"), "assistant_type": "research"})
