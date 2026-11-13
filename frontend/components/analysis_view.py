"""Rendering of stored statistical analysis results (tables, charts, notes)."""

from __future__ import annotations

from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

LABELS = {
    "overview": "Dataset overview", "descriptive": "Descriptive statistics", "missing": "Missing values",
    "duplicates": "Duplicate rows", "frequencies": "Frequencies", "correlation": "Correlation",
    "regression": "Linear regression (OLS)", "histogram": "Histogram", "scatter": "Scatter plot",
}


METRIC_LABELS = {
    "r_squared": "R²", "adj_r_squared": "Adjusted R²", "f_statistic": "F statistic", "f_p_value": "p (F test)",
    "df_model": "df (model)", "df_residual": "df (residual)", "aic": "AIC", "bic": "BIC",
    "pearson_r": "Pearson r", "p_value": "p value", "points_plotted": "Points plotted",
}


def _frame(tbl: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(tbl["rows"], columns=tbl["columns"])


def render_chart(name: str, chart: dict[str, Any]) -> None:
    kind = chart.get("type")
    if kind == "bar":
        df = pd.DataFrame({"value": chart["labels"], "count": chart["values"]})
        st.altair_chart(alt.Chart(df).mark_bar().encode(
            x=alt.X("value:N", sort="-y", title=name), y=alt.Y("count:Q", title="Count")), width="stretch")
    elif kind == "histogram":
        edges = chart["edges"]
        df = pd.DataFrame({"start": edges[:-1], "end": edges[1:], "count": chart["counts"]})
        st.altair_chart(alt.Chart(df).mark_bar().encode(
            x=alt.X("start:Q", bin="binned", title="Value"), x2="end:Q", y=alt.Y("count:Q", title="Count")),
            width="stretch")
    elif kind == "scatter":
        df = pd.DataFrame(chart["points"], columns=[chart["x"], chart["y"]])
        st.altair_chart(alt.Chart(df).mark_circle(opacity=0.6).encode(
            x=alt.X(f"{chart['x']}:Q", scale=alt.Scale(zero=False)),
            y=alt.Y(f"{chart['y']}:Q", scale=alt.Scale(zero=False))), width="stretch")
    elif kind == "heatmap":
        variables = chart["variables"]
        rows = [{"x": variables[j], "y": variables[i], "r": v}
                for i, row in enumerate(chart["values"]) for j, v in enumerate(row)]
        df = pd.DataFrame(rows)
        base = alt.Chart(df).encode(x=alt.X("x:N", title=None, sort=variables), y=alt.Y("y:N", title=None, sort=variables))
        heat = base.mark_rect().encode(color=alt.Color("r:Q", scale=alt.Scale(domain=[-1, 1], scheme="redblue")))
        text = base.mark_text(fontSize=11).encode(text=alt.Text("r:Q", format=".2f"))
        size = max(220, 60 * len(variables))
        st.altair_chart((heat + text).properties(height=size), width="stretch")


def render_results(results: dict[str, Any]) -> None:
    st.markdown(f"#### {LABELS.get(results.get('analysis_type', ''), results.get('analysis_type'))}")
    variables = results.get("variables") or []
    st.caption(f"Dataset: **{results.get('dataset_name')}** · n = **{results.get('n')}** "
               f"(of {results.get('total_rows')} rows) · variables: {', '.join(map(str, variables[:12]))}"
               + ("…" if len(variables) > 12 else ""))
    summary = results.get("summary")
    if summary:
        flat = {k: v for k, v in summary.items() if not isinstance(v, dict)}
        cols = st.columns(min(4, max(1, len(flat))))
        for i, (key, value) in enumerate(flat.items()):
            shown = f"{value:.4g}" if isinstance(value, float) else ("–" if value is None else value)
            cols[i % len(cols)].metric(METRIC_LABELS.get(key, key.replace("_", " ").capitalize()), shown)
        nested = {k: v for k, v in summary.items() if isinstance(v, dict)}
        if nested:
            st.json(nested, expanded=False)
    for name, tbl in (results.get("tables") or {}).items():
        st.markdown(f"**{name.replace('_', ' ').capitalize()}**")
        # p-values use general format so tiny values show as e.g. 1.2e-40 rather than 0.
        pcols = tbl["columns"][1:] if "p_value" in name else [c for c in tbl["columns"] if "p_value" in c]
        st.dataframe(_frame(tbl), hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.3g") for c in pcols})
        if tbl.get("truncated"):
            st.caption("Only the most frequent values are shown.")
    if results.get("vif"):
        st.markdown("**Variance inflation factors (VIF)**")
        st.dataframe(pd.DataFrame(results["vif"].items(), columns=["predictor", "VIF"]), hide_index=True)
    if results.get("diagnostics"):
        st.markdown("**Regression diagnostics**")
        st.json(results["diagnostics"], expanded=True)
    for name, chart in (results.get("charts") or {}).items():
        render_chart(name, chart)
    st.markdown(f"**Missing data handling:** {results.get('missing_handling', '—')}")
    if results.get("assumptions"):
        st.markdown("**Assumptions**\n" + "\n".join(f"- {a}" for a in results["assumptions"]))
    if results.get("limitations"):
        st.markdown("**Limitations**\n" + "\n".join(f"- {item}" for item in results["limitations"]))
