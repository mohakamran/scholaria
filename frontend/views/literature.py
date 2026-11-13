"""Module B — Literature & document assistant."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from components import api_client as api
from components.ui import call, fmt_size, fmt_time, project_selector, render_sources

ss = st.session_state
ss.setdefault("selected_document_id", None)
ss.setdefault("doc_qa", {})       # document_id -> list of Q&A results (this session)
ss.setdefault("doc_results", {})  # (document_id, task) -> summary result

st.title("Literature assistant")
st.caption("Upload papers (PDF, DOCX, TXT), ask questions with page-referenced answers, and extract key "
           "elements. Answers are based on retrieved passages, not a full verification of the paper.")

# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
with st.expander("Upload documents", expanded=not api.safe_get("/documents", default=[]),
                 icon=":material/upload_file:"):
    settings = api.safe_get("/settings", default={}) or {}
    files = st.file_uploader("Choose files", type=["pdf", "docx", "txt"], accept_multiple_files=True,
                             help=f"Maximum {settings.get('max_upload_mb', 25)} MB per file.")
    project_id = project_selector("Add to project (optional)", key="lit_upload_project")
    if st.button("Upload and process", type="primary", disabled=not files):
        for f in files or []:
            with st.spinner(f"Extracting text from {f.name}…"):
                doc = call(api.post, "/documents/upload", long=True,
                           files={"file": (f.name, f.getvalue())},
                           data={"project_id": project_id} if project_id else {},
                           error_prefix=f"{f.name}: ")
            if doc:
                if doc["processing_status"] == "ready":
                    st.success(f"{f.name}: {doc['page_count'] or '–'} pages, {doc['chunk_count']} chunks indexed.")
                else:
                    st.warning(f"{f.name}: {doc.get('error_message') or doc['processing_status']}")
                ss["selected_document_id"] = doc["id"]

# --------------------------------------------------------------------------- #
# Document list
# --------------------------------------------------------------------------- #
documents: list[dict[str, Any]] = call(api.get, "/documents") or []
if not documents:
    st.info("No documents yet. Upload a research paper to begin.")
    st.stop()

table = pd.DataFrame([{
    "ID": d["id"], "Title": d["title"] or d["filename"], "File": d["filename"], "Type": d["file_type"].upper(),
    "Pages": d["page_count"], "Chunks": d["chunk_count"], "Size": fmt_size(d["file_size"]),
    "Status": d["processing_status"], "Uploaded": fmt_time(d["created_at"]),
} for d in documents])
st.dataframe(table, hide_index=True, width="stretch")

labels = {f"{d['title'] or d['filename']} (#{d['id']})": d["id"] for d in documents}
ids = list(labels.values())
default_index = ids.index(ss["selected_document_id"]) if ss["selected_document_id"] in ids else 0
choice = st.selectbox("Selected document", list(labels), index=default_index)
doc_id = labels[choice]
ss["selected_document_id"] = doc_id
doc = next(d for d in documents if d["id"] == doc_id)

if doc["processing_status"] != "ready":
    st.warning(f"Status: {doc['processing_status']}. {doc.get('error_message') or ''}")

tab_ask, tab_extract, tab_details = st.tabs(["Ask questions", "Summarise & extract", "Details"])

# --------------------------------------------------------------------------- #
# Q&A
# --------------------------------------------------------------------------- #
with tab_ask:
    others = {k: v for k, v in labels.items() if v != doc_id}
    with st.form("ask_form", clear_on_submit=False):
        question = st.text_area("Question", placeholder="e.g. What sample size and sampling method were used?",
                                height=90)
        c1, c2 = st.columns([3, 1])
        extra = c1.multiselect("Also search these documents", list(others)) if others else []
        top_k = c2.number_input("Passages", 1, 12, int(settings.get("retrieval_top_k", 5)))
        submitted = st.form_submit_button("Ask", type="primary", disabled=doc["processing_status"] != "ready")
    if submitted and question.strip():
        with st.spinner("Retrieving passages and generating a grounded answer…"):
            result = call(api.post, f"/documents/{doc_id}/ask", long=True, json={
                "question": question.strip(), "top_k": int(top_k),
                "additional_document_ids": [others[e] for e in extra] or None})
        if result:
            ss["doc_qa"].setdefault(doc_id, []).insert(0, {"question": question.strip(), **result})

    for item in ss["doc_qa"].get(doc_id, []):
        with st.container(border=True):
            st.markdown(f"**Q: {item['question']}**")
            st.markdown(item["answer"])
            badge = ":green[Grounded in cited passages]" if item["grounded"] else ":orange[Not grounded]"
            st.caption(f"{badge} · {item['note']} · model: {item.get('model') or '—'}")
            render_sources(item["sources"])

# --------------------------------------------------------------------------- #
# Summaries & extraction
# --------------------------------------------------------------------------- #
with tab_extract:
    tasks = api.safe_get("/documents/tasks", default=[]) or []
    task_labels = {t["label"]: t["id"] for t in tasks}
    c1, c2 = st.columns([3, 2])
    task_label = c1.selectbox("Task", list(task_labels) or ["Summary"])
    task = task_labels.get(task_label, "summary")
    full_scan = c2.toggle("Process the whole document", value=task == "summary",
                          help="Whole-document processing reads every chunk (slower, complete). Otherwise only "
                               "the most relevant passages are analysed (faster, may miss details).")
    chunks = doc.get("chunk_count") or 0
    if full_scan and chunks > 40:
        st.caption(f"This document has {chunks} chunks; whole-document processing may take several minutes.")
    if st.button("Run", type="primary", disabled=doc["processing_status"] != "ready"):
        with st.spinner(f"Running '{task_label}' with the local model… this can take a while on long papers."):
            result = call(api.post, f"/documents/{doc_id}/summarize", long=True,
                          json={"task": task, "full_scan": full_scan})
        if result:
            ss["doc_results"][f"{doc_id}:{task}"] = result

    result = ss["doc_results"].get(f"{doc_id}:{task}")
    if result:
        with st.container(border=True):
            st.markdown(result["result"])
            coverage = ":green[Full document]" if result["coverage"] == "full" else ":orange[Partial coverage]"
            st.caption(f"{coverage} · {result['note']} · {result['chunks_processed']}/{result['total_chunks']} "
                       f"chunks · model: {result['model']}")
            if result["removed_invalid_page_refs"]:
                st.caption(f":orange[{result['removed_invalid_page_refs']} unverifiable page reference(s) removed.]")
        d1, d2 = st.columns([1, 3])
        d1.download_button("Download .md", result["result"], file_name=f"{task}_{doc_id}.md", mime="text/markdown")
        if doc.get("project_id"):
            if d2.button("Save to project notes", icon=":material/note_add:"):
                if call(api.post, f"/projects/{doc['project_id']}/notes",
                        json={"title": f"{task_label}: {doc['title'] or doc['filename']}"[:300],
                              "content": result["result"] + f"\n\n_Source: document #{doc_id}; {result['note']}_"}):
                    st.success("Saved to project notes.")

# --------------------------------------------------------------------------- #
# Details
# --------------------------------------------------------------------------- #
with tab_details:
    c1, c2, c3 = st.columns(3)
    c1.metric("Pages", doc["page_count"] or "–")
    c2.metric("Chunks", doc["chunk_count"] or 0)
    c3.metric("Characters", f"{doc['char_count'] or 0:,}")
    meta = doc.get("metadata") or {}
    if meta:
        st.markdown("**Metadata**")
        st.json({k: v for k, v in meta.items() if k not in {"encrypted"}}, expanded=False)
    st.caption(f"SHA-256: `{doc['file_hash'][:16]}…` · uploaded {fmt_time(doc['created_at'])}")

    new_project = project_selector("Project", key="lit_doc_project", default_id=doc.get("project_id"))
    if new_project != doc.get("project_id") and st.button("Update project"):
        call(api.patch, f"/documents/{doc_id}", json={"project_id": new_project})
        st.rerun()

    with st.expander("Preview extracted text"):
        page = st.number_input("Page (0 = all)", 0, doc["page_count"] or 0, 0) if doc["page_count"] else 0
        chunks_list = call(api.get, f"/documents/{doc_id}/chunks",
                           params={"limit": 10, **({"page": page} if page else {})}) or []
        for chunk in chunks_list:
            st.caption(f"Chunk {chunk['chunk_index']} · page {chunk['page_number'] or '–'}")
            st.text(chunk["content"][:1500])

    b1, b2, _ = st.columns([1, 1, 4])
    if b1.button("Re-process", icon=":material/sync:"):
        with st.spinner("Re-processing…"):
            call(api.post, f"/documents/{doc_id}/process", long=True)
        st.rerun()
    with b2.popover("Delete", icon=":material/delete:"):
        st.write("Delete this document, its stored file and index?")
        if st.button("Yes, delete", type="primary", key="confirm_doc_delete"):
            call(api.delete, f"/documents/{doc_id}")
            ss["selected_document_id"] = None
            st.rerun()
