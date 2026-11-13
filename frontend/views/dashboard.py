"""Home: greeting, at-a-glance counts, quick actions and recent activity."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from components import api_client as api
from components.auth import current_user
from components.ui import call, fmt_time

ss = st.session_state
pages = ss["_pages"]
user = current_user() or {}

hour = datetime.now().hour
greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
first_name = (user.get("full_name") or "").split()[0] if user.get("full_name") else ""
st.title(f"{greeting}, {first_name}" if first_name else greeting)
st.markdown("<div class='sch-subtitle'>What would you like to work on today? Everything stays on this computer.</div>",
            unsafe_allow_html=True)

# Only speak up about the AI engine when something needs attention.
status = api.safe_get("/models/status")
if status is None:
    st.error("The Scholaria service is not responding. Restart it with `./scripts/start.sh`.", icon=":material/error:")
elif not status.get("connected"):
    st.warning("The local AI engine (Ollama) is not running, so chat and writing are unavailable. "
               "Start it with `ollama serve` or open the Ollama app.", icon=":material/power_off:")
elif not status.get("default_model_available") and not status.get("fallback_model_available"):
    st.warning("No AI model is installed yet. Run `ollama pull qwen3:4b-instruct-2507-q4_K_M` in Terminal.",
               icon=":material/download:")

# --- At a glance -------------------------------------------------------------------
projects = api.safe_get("/projects", default=[]) or []
documents = api.safe_get("/documents", default=[]) or []
datasets = api.safe_get("/datasets", default=[]) or []
chats = api.safe_get("/conversations", default=[], params={"limit": 500}) or []
cols = st.columns(4)
for col, (label, value, icon) in zip(cols, [("Chats", len(chats), ":material/forum:"),
                                            ("Papers", len(documents), ":material/description:"),
                                            ("Datasets", len(datasets), ":material/table_chart:"),
                                            ("Projects", len(projects), ":material/folder_open:")]):
    with col.container(border=True):
        st.markdown(f"{icon} <span class='sch-stat-label'>{label}</span><div class='sch-stat'>{value}</div>",
                    unsafe_allow_html=True)

# --- Quick actions ---------------------------------------------------------------------
st.subheader("Start something")
actions = [
    ("Ask a question", "Chat with your private AI research assistant.", ":material/forum:", "chat"),
    ("Understand a paper", "Upload a PDF and get page-cited answers.", ":material/menu_book:", "literature"),
    ("Analyse a dataset", "Descriptives, correlations and regression.", ":material/monitoring:", "data"),
    ("Draft academic text", "Abstracts, hypotheses, methods and more.", ":material/edit_note:", "writing"),
]
cols = st.columns(4)
for col, (title, text, icon, target) in zip(cols, actions):
    with col.container(border=True):
        st.markdown(f"**{icon} {title}**")
        st.caption(text)
        if st.button("Open", key=f"go_{target}", icon=":material/arrow_forward:", width="stretch"):
            if target == "chat":
                ss["conversation_id"] = None
            st.switch_page(pages[target])

# --- Recent activity ---------------------------------------------------------------------
st.subheader("Recent activity")
activity = call(api.get, "/activity", params={"limit": 8}) or []
if not activity:
    st.info("Nothing here yet. Start a chat or upload a paper and it will show up here.", icon=":material/history:")
icons = {"conversation": ":material/forum:", "document": ":material/description:",
         "dataset": ":material/table_chart:", "note": ":material/sticky_note_2:"}
labels = {"conversation": "Chat", "document": "Paper", "dataset": "Dataset", "note": "Note"}
targets = {"conversation": "chat", "document": "literature", "dataset": "data", "note": "workspace"}
with st.container(border=bool(activity)):
    for item in activity:
        c1, c2 = st.columns([7, 1], vertical_alignment="center")
        c1.markdown(f"{icons.get(item['type'], '')} **{item['title']}**  \n"
                    f"<span class='sch-muted'>{labels.get(item['type'], '')} · {fmt_time(item['at'])}</span>",
                    unsafe_allow_html=True)
        if c2.button("Open", key=f"act_{item['type']}_{item['id']}", type="tertiary"):
            if item["type"] == "conversation":
                ss["conversation_id"] = item["id"]
            elif item["type"] == "document":
                ss["selected_document_id"] = item["id"]
            elif item["type"] == "dataset":
                ss["selected_dataset_id"] = item["id"]
            st.switch_page(pages[targets[item["type"]]])
