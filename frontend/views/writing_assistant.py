"""Module D — Academic writing assistant."""

from __future__ import annotations

import streamlit as st

from components import api_client as api
from components.ui import call, copy_box, project_selector, show_error

ss = st.session_state
ss.setdefault("writing_output", "")

st.title("Academic writing")
st.caption("Drafts are evidence-conscious: the assistant never invents references, DOIs, authors or data and "
           "marks gaps with [citation needed] / [evidence needed]. Always review drafts before use.")

tasks = api.safe_get("/writing/tasks", default=[]) or []
if not tasks:
    st.error("Cannot load writing tasks — is the backend running?")
    st.stop()
task_by_label = {t["label"]: t for t in tasks}

c1, c2 = st.columns([3, 1])
task_label = c1.selectbox("Writing task", list(task_by_label))
task = task_by_label[task_label]
output_format = c2.selectbox("Output format", ["markdown", "latex", "plain"],
                             index=1 if task["id"] == "latex" else 0, disabled=task["id"] == "latex")
st.caption(task["instruction"])

text = st.text_area("Your text / research details", height=220, key="writing_text",
                    placeholder="Paste a draft, findings, statistical output, or describe your study…")
context = st.text_area("Additional context (optional)", height=80, key="writing_context",
                       placeholder="Discipline, target journal, word limit, key constructs…")

with st.expander("Use project information", icon=":material/folder_open:"):
    project_id = project_selector("Project", key="writing_project")
    pc1, pc2 = st.columns(2)
    include_context = pc1.checkbox("Include objectives, questions & hypotheses", disabled=project_id is None)
    include_bib = pc2.checkbox("Allow citing the project bibliography", disabled=project_id is None,
                               help="Only references you entered in the workspace may be cited.")
    temperature = st.slider("Temperature", 0.0, 1.2, 0.5, 0.05)

chars = len(text) + len(context)
st.caption(f"{chars:,} characters (~{chars // 4:,} tokens). Long inputs may exceed the 4096-token context — "
           "split long drafts into sections.")

if st.button("Generate", type="primary", icon=":material/edit:"):
    payload = {"task": task["id"], "text": text, "context": context or None, "project_id": project_id,
               "include_project_context": include_context, "include_bibliography": include_bib,
               "output_format": output_format, "temperature": temperature}
    state: dict = {}

    def tokens():
        for event, data in api.stream_sse("/writing/generate", payload):
            if event == "token":
                yield data["content"]
            elif event == "error":
                state["error"] = data
            elif event == "done":
                state["done"] = data

    with st.container(border=True):
        try:
            output = st.write_stream(tokens())
            ss["writing_output"] = output if isinstance(output, str) else "".join(map(str, output))
        except api.ApiError as exc:
            show_error(exc)
    if "error" in state:
        show_error(api.ApiError(state["error"]["message"], code=state["error"].get("code")))
    elif "done" in state:
        ss["writing_note"] = state["done"].get("note")
        ss["writing_warnings"] = state["done"].get("warnings") or []
        st.rerun()

if ss["writing_output"]:
    st.subheader("Draft")
    if ss.get("writing_note"):
        st.caption(ss["writing_note"])
    for warning in ss.get("writing_warnings", []):
        st.warning(warning, icon=":material/fact_check:")
    with st.container(border=True):
        if output_format == "latex" or task["id"] == "latex":
            st.code(ss["writing_output"], language="latex", wrap_lines=True)
        else:
            st.markdown(ss["writing_output"])
    row = st.container(horizontal=True, vertical_alignment="center")
    b1 = b2 = b3 = row
    with b1:
        copy_box(ss["writing_output"], key="copy_writing")
    ext = "tex" if output_format == "latex" or task["id"] == "latex" else ("md" if output_format == "markdown" else "txt")
    b2.download_button(f"Download .{ext}", ss["writing_output"], file_name=f"{task['id']}.{ext}")
    if project_id:
        if b3.button("Save as project note", icon=":material/note_add:"):
            if call(api.post, f"/projects/{project_id}/notes",
                    json={"title": f"Draft: {task_label}", "content": ss["writing_output"]}):
                st.success("Saved to project notes.")
    else:
        b3.caption("Select a project to save this draft as a note.")
