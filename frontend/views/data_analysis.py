"""Module C — Research data assistant (validated, predefined analyses only)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from components import api_client as api
from components.analysis_view import LABELS, render_results
from components.ui import call, fmt_size, fmt_time, project_selector

ss = st.session_state
ss.setdefault("selected_dataset_id", None)
ss.setdefault("interpretations", {})

st.title("Data analysis")
st.caption("All statistics are computed by Python (pandas, SciPy, statsmodels). The language model only "
           "explains stored results on request — it never produces numbers.")

with st.expander("Upload dataset", expanded=not api.safe_get("/datasets", default=[]), icon=":material/upload:"):
    upload = st.file_uploader("CSV or Excel (.xlsx)", type=["csv", "xlsx"])
    project_id = project_selector("Add to project (optional)", key="data_upload_project")
    if st.button("Upload", type="primary", disabled=upload is None):
        with st.spinner("Reading dataset…"):
            ds = call(api.post, "/datasets/upload", long=True, files={"file": (upload.name, upload.getvalue())},
                      data={"project_id": project_id} if project_id else {})
        if ds:
            st.success(f"Loaded {ds['row_count']:,} rows × {ds['column_count']} columns.")
            ss["selected_dataset_id"] = ds["id"]

datasets: list[dict[str, Any]] = call(api.get, "/datasets") or []
if not datasets:
    st.info("No datasets yet. Upload a CSV or Excel file to begin.")
    st.stop()

labels = {f"{d['filename']} (#{d['id']}) — {d['row_count']:,}×{d['column_count']}": d["id"] for d in datasets}
ids = list(labels.values())
index = ids.index(ss["selected_dataset_id"]) if ss["selected_dataset_id"] in ids else 0
dataset_id = labels[st.selectbox("Dataset", list(labels), index=index)]
ss["selected_dataset_id"] = dataset_id
dataset = next(d for d in datasets if d["id"] == dataset_id)

preview = call(api.get, f"/datasets/{dataset_id}/preview", params={"rows": 100})
if not preview:
    st.stop()
columns = [c["name"] for c in preview["column_info"]]
numeric = [c["name"] for c in preview["column_info"] if c["numeric"]]
categorical = [c for c in columns if c not in numeric]

tab_preview, tab_analyze, tab_history = st.tabs(["Preview", "Analyse", "Saved analyses & export"])

with tab_preview:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows", f"{preview['rows']:,}")
    m2.metric("Columns", preview["columns"])
    m3.metric("Missing cells", f"{sum(c['missing'] for c in preview['column_info']):,}")
    m4.metric("Duplicate rows", preview["duplicate_rows"])
    st.markdown("**Columns and data types**")
    st.dataframe(pd.DataFrame(preview["column_info"]).rename(columns={
        "name": "Column", "dtype": "Type", "missing": "Missing", "unique": "Unique", "numeric": "Numeric"}),
        hide_index=True, width="stretch")
    st.markdown(f"**First {len(preview['head']['rows'])} rows**")
    st.dataframe(pd.DataFrame(preview["head"]["rows"], columns=preview["head"]["columns"]), width="stretch")
    st.caption(f"{fmt_size(dataset['file_size'])} · uploaded {fmt_time(dataset['created_at'])}")
    with st.popover("Delete dataset", icon=":material/delete:"):
        st.write("Delete this dataset file and all saved analyses?")
        if st.button("Yes, delete", type="primary", key="confirm_ds_delete"):
            call(api.delete, f"/datasets/{dataset_id}")
            ss["selected_dataset_id"] = None
            st.rerun()

with tab_analyze:
    analysis = st.selectbox("Analysis", list(LABELS), format_func=LABELS.get, key="analysis_type")
    body: dict[str, Any] = {"analysis_type": analysis}
    if analysis == "descriptive":
        body["columns"] = st.multiselect("Numeric variables", numeric, default=numeric[:8]) or None
    elif analysis in {"missing", "duplicates"}:
        body["columns"] = st.multiselect("Variables (empty = all)", columns) or None
    elif analysis == "frequencies":
        body["columns"] = st.multiselect("Variables (max 10)", columns, default=categorical[:2], max_selections=10)
        body["top_n"] = st.slider("Show top values", 5, 100, 30)
    elif analysis == "correlation":
        body["columns"] = st.multiselect("Numeric variables (2–30)", numeric, default=numeric[:5], max_selections=30)
        body["method"] = st.radio("Method", ["pearson", "spearman", "kendall"], horizontal=True)
    elif analysis == "regression":
        dep = st.selectbox("Dependent variable (numeric)", numeric) if numeric else None
        body["dependent"] = dep
        body["independents"] = st.multiselect("Independent variables", [c for c in columns if c != dep],
                                              help="Categorical predictors are dummy-coded automatically.")
    elif analysis == "histogram":
        body["x"] = st.selectbox("Numeric variable", numeric) if numeric else None
        body["bins"] = st.slider("Bins", 5, 60, 20)
    elif analysis == "scatter":
        c1, c2 = st.columns(2)
        body["x"] = c1.selectbox("X (numeric)", numeric, key="sc_x") if numeric else None
        body["y"] = c2.selectbox("Y (numeric)", numeric, index=min(1, max(0, len(numeric) - 1)), key="sc_y") if numeric else None

    if st.button("Run analysis", type="primary"):
        with st.spinner("Computing…"):
            run = call(api.post, f"/datasets/{dataset_id}/analyze", long=True, json=body)
        if run:
            ss["last_run"] = run

    run = ss.get("last_run")
    if run and run["dataset_id"] == dataset_id:
        with st.container(border=True):
            render_results(run["results"])
            if st.button("Explain these results with AI", icon=":material/auto_awesome:", key="interp_last"):
                with st.spinner("The local model is writing an explanation of the computed results…"):
                    out = call(api.post, f"/datasets/{dataset_id}/analyses/{run['id']}/interpret", long=True, json={})
                if out:
                    ss["interpretations"][run["id"]] = out
            if run["id"] in ss["interpretations"]:
                interp = ss["interpretations"][run["id"]]
                st.info(interp["explanation"])
                st.caption(interp["note"])

with tab_history:
    runs = call(api.get, f"/datasets/{dataset_id}/analyses") or []
    if runs:
        e1, e2, _ = st.columns([1, 1, 3])
        xlsx = api.safe_get(f"/datasets/{dataset_id}/export", params={"format": "xlsx"})
        if xlsx:
            e1.download_button("Export all (.xlsx)", xlsx, file_name=f"analyses_{dataset_id}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        js = api.safe_get(f"/datasets/{dataset_id}/export", params={"format": "json"})
        if js:
            import json as _json
            e2.download_button("Export all (.json)", _json.dumps(js, indent=2), file_name=f"analyses_{dataset_id}.json",
                               mime="application/json")
    else:
        st.caption("No saved analyses yet.")
    for run in runs:
        with st.expander(f"#{run['id']} · {LABELS.get(run['analysis_type'], run['analysis_type'])} · "
                         f"{fmt_time(run['created_at'])} · n={run['results'].get('n')}"):
            render_results(run["results"])
            st.caption("Parameters: " + ", ".join(f"{k}={v}" for k, v in run["parameters"].items()))
            c1, c2 = st.columns([1, 4])
            if run["results"].get("tables"):
                csv = api.safe_get(f"/datasets/{dataset_id}/export", params={"format": "csv", "analysis_id": run["id"]})
                if csv:
                    c1.download_button("CSV", csv, file_name=f"analysis_{run['id']}.csv", mime="text/csv",
                                       key=f"csv_{run['id']}")
            if c2.button("Delete", key=f"del_run_{run['id']}", icon=":material/delete:"):
                call(api.delete, f"/datasets/{dataset_id}/analyses/{run['id']}")
                st.rerun()
