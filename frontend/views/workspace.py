"""Module E — Research workspace: projects, notes, bibliography and linked material."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from components import api_client as api
from components.ui import call, fmt_time

ss = st.session_state
ss.setdefault("workspace_project_id", None)
pages = ss["_pages"]

st.title("Research workspace")

projects: list[dict[str, Any]] = call(api.get, "/projects") or []

with st.expander("Create a research project", expanded=not projects or ss.pop("workspace_create", False),
                 icon=":material/create_new_folder:"):
    with st.form("create_project", clear_on_submit=True):
        title = st.text_input("Project title *")
        description = st.text_area("Description", height=80)
        objectives = st.text_area("Research objectives", height=80)
        questions = st.text_area("Research questions", height=80)
        hypotheses = st.text_area("Hypotheses", height=80)
        if st.form_submit_button("Create project", type="primary"):
            if not title.strip():
                st.error("A title is required.")
            else:
                created = call(api.post, "/projects", json={
                    "title": title.strip(), "description": description or None, "objectives": objectives or None,
                    "research_questions": questions or None, "hypotheses": hypotheses or None})
                if created:
                    ss["workspace_project_id"] = created["id"]
                    st.rerun()

if not projects:
    st.info("Create your first research project to organise papers, data, notes and conversations.")
    st.stop()

labels = {f"{p['title']} (#{p['id']})": p["id"] for p in projects}
ids = list(labels.values())
index = ids.index(ss["workspace_project_id"]) if ss["workspace_project_id"] in ids else 0
project_id = labels[st.selectbox("Project", list(labels), index=index)]
ss["workspace_project_id"] = project_id
project = next(p for p in projects if p["id"] == project_id)
counts = project.get("counts") or {}

m = st.columns(5)
for col, key in zip(m, ["documents", "datasets", "conversations", "notes", "citations"]):
    col.metric(key.capitalize(), counts.get(key, 0))

tabs = st.tabs(["Overview", "Notes", "Bibliography", "Documents", "Datasets", "Conversations"])

# --- Overview -------------------------------------------------------------------
with tabs[0]:
    with st.form("edit_project"):
        title = st.text_input("Title", value=project["title"])
        description = st.text_area("Description", value=project.get("description") or "", height=90)
        objectives = st.text_area("Research objectives", value=project.get("objectives") or "", height=110)
        questions = st.text_area("Research questions", value=project.get("research_questions") or "", height=110)
        hypotheses = st.text_area("Hypotheses", value=project.get("hypotheses") or "", height=110)
        if st.form_submit_button("Save changes", type="primary"):
            if call(api.patch, f"/projects/{project_id}", json={
                    "title": title.strip() or project["title"], "description": description, "objectives": objectives,
                    "research_questions": questions, "hypotheses": hypotheses}):
                st.success("Project saved.")
                st.rerun()
    st.caption(f"Created {fmt_time(project['created_at'])} · updated {fmt_time(project['updated_at'])}")
    with st.popover("Delete project", icon=":material/delete:"):
        st.write("Deletes the project with its **notes and bibliography**. Documents, datasets and "
                 "conversations are kept and simply unlinked.")
        if st.button("Yes, delete project", type="primary"):
            call(api.delete, f"/projects/{project_id}")
            ss["workspace_project_id"] = None
            st.rerun()

# --- Notes ------------------------------------------------------------------------
with tabs[1]:
    with st.form("new_note", clear_on_submit=True):
        n_title = st.text_input("Note title")
        n_content = st.text_area("Content (Markdown supported)", height=140)
        if st.form_submit_button("Save note", type="primary"):
            if n_title.strip():
                call(api.post, f"/projects/{project_id}/notes", json={"title": n_title.strip(), "content": n_content})
                st.rerun()
            else:
                st.error("A note title is required.")
    for note in call(api.get, f"/projects/{project_id}/notes") or []:
        with st.expander(f"{note['title']} · {fmt_time(note['updated_at'])}"):
            edit_key = f"edit_note_{note['id']}"
            if ss.get(edit_key):
                new_title = st.text_input("Title", note["title"], key=f"nt_{note['id']}")
                new_content = st.text_area("Content", note["content"], height=200, key=f"nc_{note['id']}")
                c1, c2 = st.columns([1, 5])
                if c1.button("Save", key=f"ns_{note['id']}", type="primary"):
                    call(api.patch, f"/notes/{note['id']}", json={"title": new_title, "content": new_content})
                    ss[edit_key] = False
                    st.rerun()
                if c2.button("Cancel", key=f"ncl_{note['id']}"):
                    ss[edit_key] = False
                    st.rerun()
            else:
                st.markdown(note["content"] or "_Empty note_")
                c1, c2, _ = st.columns([1, 1, 5])
                if c1.button("Edit", key=f"ne_{note['id']}"):
                    ss[edit_key] = True
                    st.rerun()
                if c2.button("Delete", key=f"nd_{note['id']}"):
                    call(api.delete, f"/notes/{note['id']}")
                    st.rerun()

# --- Bibliography -------------------------------------------------------------------
with tabs[2]:
    st.caption("Enter references you have verified. The writing assistant may cite only these entries.")
    with st.form("new_citation", clear_on_submit=True):
        c_title = st.text_input("Title *")
        c1, c2 = st.columns([3, 1])
        authors = c1.text_input("Authors", placeholder="Smith, J. and Doe, A.")
        year = c2.number_input("Year", min_value=0, max_value=2100, value=0, help="0 = unknown")
        c3, c4 = st.columns(2)
        journal = c3.text_input("Journal / venue")
        doi = c4.text_input("DOI", placeholder="10.xxxx/xxxxx")
        url = st.text_input("URL")
        c_notes = st.text_area("Notes", height=70)
        if st.form_submit_button("Add reference", type="primary"):
            if c_title.strip():
                call(api.post, f"/projects/{project_id}/citations", json={
                    "title": c_title.strip(), "authors": authors or None, "publication_year": int(year) or None,
                    "journal": journal or None, "doi": doi or None, "url": url or None, "notes": c_notes or None})
                st.rerun()
            else:
                st.error("A title is required.")
    citations = call(api.get, f"/projects/{project_id}/citations") or []
    if citations:
        st.dataframe(pd.DataFrame([{
            "ID": c["id"], "Authors": c["authors"], "Year": c["publication_year"], "Title": c["title"],
            "Journal": c["journal"], "DOI": c["doi"]} for c in citations]), hide_index=True, width="stretch")
        b1, b2 = st.columns([1, 3])
        bib = api.safe_get(f"/projects/{project_id}/citations/export")
        if bib:
            b1.download_button("Export BibTeX", bib, file_name=f"project_{project_id}.bib", mime="text/plain")
        with b2:
            delete_id = st.selectbox("Delete reference", [None] + [c["id"] for c in citations],
                                     format_func=lambda i: "—" if i is None else f"#{i}")
            if delete_id and st.button("Delete selected reference"):
                call(api.delete, f"/citations/{delete_id}")
                st.rerun()

# --- Documents / datasets / conversations -------------------------------------------
def link_section(kind: str, items: list[dict[str, Any]], label_fn, patch_path: str) -> None:
    linked = [i for i in items if i.get("project_id") == project_id]
    unlinked = [i for i in items if i.get("project_id") != project_id]
    if linked:
        for item in linked:
            c1, c2 = st.columns([6, 1])
            c1.markdown(f"- {label_fn(item)}")
            if c2.button("Unlink", key=f"unlink_{kind}_{item['id']}"):
                call(api.patch, patch_path.format(id=item["id"]), json={"project_id": None})
                st.rerun()
    else:
        st.caption(f"No {kind} linked to this project.")
    if unlinked:
        options = {label_fn(i): i["id"] for i in unlinked}
        choice = st.selectbox(f"Add existing {kind[:-1]}", [None, *options], key=f"add_{kind}",
                              format_func=lambda x: "—" if x is None else x)
        if choice and st.button(f"Link {kind[:-1]}", key=f"link_{kind}"):
            call(api.patch, patch_path.format(id=options[choice]), json={"project_id": project_id})
            st.rerun()


with tabs[3]:
    docs = call(api.get, "/documents") or []
    link_section("documents", docs, lambda d: f"{d['title'] or d['filename']} ({d['file_type'].upper()}, "
                 f"{d['processing_status']})", "/documents/{id}")
    if st.button("Open literature assistant", icon=":material/menu_book:"):
        st.switch_page(pages["literature"])

with tabs[4]:
    datasets = call(api.get, "/datasets") or []
    link_section("datasets", datasets, lambda d: f"{d['filename']} ({d['row_count']:,}×{d['column_count']})",
                 "/datasets/{id}")
    if st.button("Open data analysis", icon=":material/monitoring:"):
        st.switch_page(pages["data"])

with tabs[5]:
    conversations = call(api.get, "/conversations", params={"limit": 200}) or []
    link_section("conversations", conversations, lambda c: f"{c['title']} · {fmt_time(c['updated_at'])}",
                 "/conversations/{id}")
    if st.button("Start a project conversation", icon=":material/add_comment:", type="primary"):
        conv = call(api.post, "/conversations", json={"title": f"{project['title']} discussion",
                                                      "project_id": project_id})
        if conv:
            ss["conversation_id"] = conv["id"]
            st.switch_page(pages["chat"])
