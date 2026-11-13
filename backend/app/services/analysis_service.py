"""Predefined, validated statistical analyses.

All numbers are computed here with pandas / SciPy / statsmodels. The language
model never produces statistics; it may only *explain* stored results. No
user- or LLM-supplied code is ever executed: callers choose an analysis type and
column names, which are validated against the dataset.
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.database.models import AnalysisRun, Dataset
from app.database.repositories import AnalysisRunRepository, DatasetRepository
from app.schemas.datasets import AnalyzeRequest
from app.services.storage_service import StoredFile, delete_stored, resolve_inside

logger = get_logger("analysis")

ALLOWED_DATASET_TYPES = {"csv", "xlsx"}
MAX_COLUMNS_FREQ = 10
MAX_SCATTER_POINTS = 2000

# Single-entry cache: avoids re-parsing the same file on consecutive requests
# while never holding more than one DataFrame in memory.
_cache: dict[str, Any] = {"key": None, "df": None}


# --------------------------------------------------------------------------- #
# Loading & JSON helpers
# --------------------------------------------------------------------------- #
def load_dataframe(path: Path, file_type: str) -> pd.DataFrame:
    key = (str(path), path.stat().st_mtime)
    if _cache["key"] == key:
        return _cache["df"]
    try:
        if file_type == "csv":
            df = pd.read_csv(path, sep=None, engine="python", encoding_errors="replace")
        elif file_type == "xlsx":
            df = pd.read_excel(path, engine="openpyxl")
        else:
            raise ValidationFailed(f"Unsupported dataset type: {file_type}")
    except ValidationFailed:
        raise
    except Exception as exc:
        raise ValidationFailed(f"Could not read the dataset ({type(exc).__name__}). Check the file format.") from exc
    df.columns = [str(c).strip() or f"column_{i + 1}" for i, c in enumerate(df.columns)]
    if df.columns.duplicated().any():
        df.columns = [f"{c}_{i}" if dup else c for i, (c, dup) in enumerate(zip(df.columns, df.columns.duplicated()))]
    _cache.update(key=key, df=df)
    return df


def clear_cache() -> None:
    _cache.update(key=None, df=None)


def _clean(value: Any) -> Any:
    """Convert numpy/pandas scalars to JSON-safe Python values."""
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        # Keep 6 significant digits so very small p-values are not rounded to 0.
        return None if math.isnan(f) or math.isinf(f) else float(f"{f:.6g}")
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(value)
    if value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value if isinstance(value, (int, str)) else str(value)


def table(df: pd.DataFrame, index_name: str | None = None) -> dict[str, Any]:
    frame = df.reset_index() if index_name is not None else df
    if index_name is not None:
        frame = frame.rename(columns={frame.columns[0]: index_name})
    return {
        "columns": [str(c) for c in frame.columns],
        "rows": [[_clean(v) for v in row] for row in frame.itertuples(index=False, name=None)],
    }


def _require_columns(df: pd.DataFrame, columns: list[str]) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValidationFailed(f"Unknown column(s): {', '.join(missing)}")


def _numeric(df: pd.DataFrame, columns: list[str]) -> list[str]:
    bad = [c for c in columns if not pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_bool_dtype(df[c])]
    if bad:
        raise ValidationFailed(f"Column(s) must be numeric for this analysis: {', '.join(bad)}")
    return columns


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]


# --------------------------------------------------------------------------- #
# Analyses
# --------------------------------------------------------------------------- #
def analysis_overview(df: pd.DataFrame, req: AnalyzeRequest) -> dict[str, Any]:
    info = pd.DataFrame({
        "dtype": df.dtypes.astype(str),
        "non_missing": df.notna().sum(),
        "missing": df.isna().sum(),
        "unique": df.nunique(dropna=True),
    })
    return {
        "variables": list(df.columns), "n": int(len(df)),
        "summary": {"rows": int(len(df)), "columns": int(df.shape[1]),
                    "duplicate_rows": int(df.duplicated().sum()),
                    "cells_missing": int(df.isna().sum().sum()),
                    "numeric_columns": len(numeric_columns(df))},
        "tables": {"columns": table(info, "column")},
        "missing_handling": "None (structural overview of all rows).",
        "assumptions": [], "limitations": ["Column types are inferred automatically by pandas."],
    }


def analysis_descriptive(df: pd.DataFrame, req: AnalyzeRequest) -> dict[str, Any]:
    cols = req.columns or numeric_columns(df)
    if not cols:
        raise ValidationFailed("The dataset has no numeric columns for descriptive statistics.")
    _require_columns(df, cols)
    _numeric(df, cols)
    sub = df[cols]
    desc = pd.DataFrame({
        "n": sub.count(), "missing": sub.isna().sum(), "mean": sub.mean(), "sd": sub.std(ddof=1),
        "min": sub.min(), "q1": sub.quantile(0.25), "median": sub.median(), "q3": sub.quantile(0.75),
        "max": sub.max(), "skewness": sub.skew(), "kurtosis": sub.kurt(),
    })
    return {
        "variables": cols, "n": int(len(df)), "tables": {"descriptive_statistics": table(desc, "variable")},
        "missing_handling": "Available-case: each statistic uses the non-missing values of that variable.",
        "assumptions": ["Mean and SD are most informative for approximately symmetric interval/ratio data."],
        "limitations": ["Skewness/kurtosis are sample estimates (Fisher definition; normal kurtosis = 0).",
                        "Numeric codes for categories (e.g. 1=male, 2=female) are summarised as numbers."],
    }


def analysis_missing(df: pd.DataFrame, req: AnalyzeRequest) -> dict[str, Any]:
    cols = req.columns or list(df.columns)
    _require_columns(df, cols)
    sub = df[cols]
    miss = pd.DataFrame({"missing": sub.isna().sum(), "percent": (sub.isna().mean() * 100).round(2)})
    complete = int(sub.dropna().shape[0])
    return {
        "variables": cols, "n": int(len(df)),
        "summary": {"rows": int(len(df)), "complete_cases": complete,
                    "rows_with_missing": int(len(df) - complete),
                    "percent_complete_cases": round(complete / len(df) * 100, 2) if len(df) else None},
        "tables": {"missing_values": table(miss, "variable")},
        "missing_handling": "Reports missingness; no imputation performed.",
        "assumptions": [], "limitations": [
            "Only empty cells / NaN are counted. Codes such as -99 or 'N/A' text are not treated as missing.",
            "This analysis does not test whether data are missing completely at random (MCAR).",
        ],
    }


def analysis_duplicates(df: pd.DataFrame, req: AnalyzeRequest) -> dict[str, Any]:
    cols = req.columns or list(df.columns)
    _require_columns(df, cols)
    mask = df.duplicated(subset=cols, keep="first")
    dup_rows = df.loc[mask].head(20)
    return {
        "variables": cols, "n": int(len(df)),
        "summary": {"duplicate_rows": int(mask.sum()),
                    "percent_duplicates": round(float(mask.mean()) * 100, 2) if len(df) else 0.0},
        "tables": {"duplicate_examples": table(dup_rows.reset_index().rename(columns={"index": "row"}))},
        "missing_handling": "Missing values are treated as equal when comparing rows.",
        "assumptions": [], "limitations": ["Exact matching only; near-duplicates are not detected."],
    }


def analysis_frequencies(df: pd.DataFrame, req: AnalyzeRequest) -> dict[str, Any]:
    cols = req.columns or [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])][:3]
    if not cols:
        raise ValidationFailed("Select at least one column for frequencies.")
    if len(cols) > MAX_COLUMNS_FREQ:
        raise ValidationFailed(f"Select at most {MAX_COLUMNS_FREQ} columns for frequencies.")
    _require_columns(df, cols)
    tables: dict[str, Any] = {}
    charts: dict[str, Any] = {}
    for col in cols:
        counts = df[col].value_counts(dropna=False)
        total = len(df)
        valid_total = int(df[col].notna().sum())
        frame = pd.DataFrame({"count": counts})
        frame.index = ["(missing)" if pd.isna(v) else str(v) for v in frame.index]
        frame["percent"] = (frame["count"] / total * 100).round(2) if total else 0
        frame["valid_percent"] = [
            None if label == "(missing)" or not valid_total else round(c / valid_total * 100, 2)
            for label, c in zip(frame.index, frame["count"])
        ]
        truncated = len(frame) > req.top_n
        frame = frame.head(req.top_n)
        tables[col] = table(frame, "value")
        tables[col]["truncated"] = truncated
        charts[col] = {"type": "bar", "labels": list(frame.index), "values": [int(v) for v in frame["count"]]}
    return {
        "variables": cols, "n": int(len(df)), "tables": tables, "charts": charts,
        "missing_handling": "Missing values shown as '(missing)'; valid percent excludes them.",
        "assumptions": [], "limitations": [f"Only the {req.top_n} most frequent values are listed per variable."],
    }


def analysis_correlation(df: pd.DataFrame, req: AnalyzeRequest) -> dict[str, Any]:
    cols = req.columns or numeric_columns(df)
    if len(cols) < 2:
        raise ValidationFailed("Select at least two numeric columns for correlation.")
    if len(cols) > 30:
        raise ValidationFailed("Select at most 30 columns for a correlation matrix.")
    _require_columns(df, cols)
    _numeric(df, cols)
    sub = df[cols].dropna()
    n = len(sub)
    if n < 3:
        raise ValidationFailed("At least 3 complete cases are required for correlation.")
    func: Callable[..., Any] = {"pearson": stats.pearsonr, "spearman": stats.spearmanr, "kendall": stats.kendalltau}[req.method]
    r = pd.DataFrame(np.eye(len(cols)), index=cols, columns=cols)
    p = pd.DataFrame(np.zeros((len(cols), len(cols))), index=cols, columns=cols)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            if sub[a].nunique() < 2 or sub[b].nunique() < 2:
                coef, pval = float("nan"), float("nan")
            else:
                res = func(sub[a], sub[b])
                coef, pval = float(res[0]), float(res[1])
            r.loc[a, b] = r.loc[b, a] = coef
            p.loc[a, b] = p.loc[b, a] = pval
    return {
        "variables": cols, "n": n, "method": req.method,
        "tables": {"correlation_coefficients": table(r.round(4), "variable"),
                   "p_values": table(p, "variable")},
        "charts": {"heatmap": {"type": "heatmap", "variables": cols,
                               "values": [[_clean(v) for v in row] for row in r.to_numpy()]}},
        "missing_handling": f"Listwise deletion: {len(df) - n} row(s) with missing values in the selected columns excluded.",
        "assumptions": {
            "pearson": ["Linear relationship", "Approximately bivariate normal data", "No influential outliers",
                        "Interval/ratio measurement"],
            "spearman": ["Monotonic relationship", "Ordinal or continuous data"],
            "kendall": ["Monotonic relationship", "Ordinal or continuous data; robust with ties/small samples"],
        }[req.method],
        "limitations": ["Correlation does not imply causation.",
                        "p-values are not adjusted for multiple comparisons."],
    }


def analysis_regression(df: pd.DataFrame, req: AnalyzeRequest) -> dict[str, Any]:
    import statsmodels.api as sm
    from statsmodels.stats.diagnostic import het_breuschpagan
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    from statsmodels.stats.stattools import durbin_watson

    if not req.dependent or not req.independents:
        raise ValidationFailed("Choose one dependent variable and at least one independent variable.")
    if req.dependent in req.independents:
        raise ValidationFailed("The dependent variable cannot also be an independent variable.")
    cols = [req.dependent, *req.independents]
    _require_columns(df, cols)
    _numeric(df, [req.dependent])
    sub = df[cols].dropna()
    excluded = len(df) - len(sub)
    categorical = [c for c in req.independents if not pd.api.types.is_numeric_dtype(sub[c])]
    for c in categorical:
        if sub[c].nunique() > 20:
            raise ValidationFailed(f"Categorical predictor '{c}' has more than 20 levels.")
    X = pd.get_dummies(sub[req.independents], columns=categorical, drop_first=True, dtype=float)
    X = X.astype(float)
    y = sub[req.dependent].astype(float)
    k = X.shape[1]
    if len(sub) < k + 2:
        raise ValidationFailed(f"Not enough complete cases ({len(sub)}) for {k} predictor(s).")
    X = sm.add_constant(X, has_constant="add")
    model = sm.OLS(y, X).fit()
    ci = model.conf_int()
    coef = pd.DataFrame({
        "coefficient": model.params, "std_error": model.bse, "t": model.tvalues, "p_value": model.pvalues,
        "ci_95_lower": ci[0], "ci_95_upper": ci[1],
    })
    vif = {}
    if k >= 2:
        for i, name in enumerate(X.columns):
            if name != "const":
                try:
                    vif[name] = _clean(variance_inflation_factor(X.values, i))
                except Exception:  # pragma: no cover
                    vif[name] = None
    resid = model.resid
    diagnostics: dict[str, Any] = {"durbin_watson": _clean(durbin_watson(resid))}
    if 3 <= len(resid) <= 5000:
        sw = stats.shapiro(resid)
        diagnostics["shapiro_wilk_residuals"] = {"W": _clean(sw.statistic), "p_value": _clean(sw.pvalue)}
    try:
        bp = het_breuschpagan(resid, X)
        diagnostics["breusch_pagan"] = {"LM": _clean(bp[0]), "p_value": _clean(bp[1])}
    except Exception:  # pragma: no cover
        pass
    return {
        "variables": cols, "dependent": req.dependent, "independents": req.independents,
        "dummy_coded": categorical, "n": int(model.nobs),
        "summary": {"r_squared": _clean(model.rsquared), "adj_r_squared": _clean(model.rsquared_adj),
                    "f_statistic": _clean(model.fvalue), "f_p_value": _clean(model.f_pvalue),
                    "df_model": _clean(model.df_model), "df_residual": _clean(model.df_resid),
                    "aic": _clean(model.aic), "bic": _clean(model.bic)},
        "tables": {"coefficients": table(coef, "term")},
        "vif": vif, "diagnostics": diagnostics,
        "missing_handling": f"Listwise deletion: {excluded} row(s) with missing values excluded.",
        "assumptions": ["Linearity between predictors and outcome", "Independence of errors (see Durbin-Watson ≈ 2)",
                        "Homoscedasticity (see Breusch-Pagan)", "Normally distributed residuals (see Shapiro-Wilk)",
                        "No severe multicollinearity (VIF < 5-10)"],
        "limitations": ["Ordinary least squares with non-robust standard errors.",
                        "Associations do not establish causality.",
                        "Categorical predictors are dummy-coded against the first level."],
    }


def analysis_histogram(df: pd.DataFrame, req: AnalyzeRequest) -> dict[str, Any]:
    col = req.x or (req.columns[0] if req.columns else None)
    if not col:
        raise ValidationFailed("Choose a numeric column for the histogram.")
    _require_columns(df, [col])
    _numeric(df, [col])
    values = df[col].dropna().to_numpy(dtype=float)
    if values.size == 0:
        raise ValidationFailed("The column has no non-missing values.")
    counts, edges = np.histogram(values, bins=req.bins)
    normality: dict[str, Any] = {"skewness": _clean(stats.skew(values)), "kurtosis": _clean(stats.kurtosis(values))}
    if 3 <= values.size <= 5000:
        sw = stats.shapiro(values)
        normality["shapiro_wilk"] = {"W": _clean(sw.statistic), "p_value": _clean(sw.pvalue)}
    return {
        "variables": [col], "n": int(values.size),
        "charts": {"histogram": {"type": "histogram", "edges": [_clean(e) for e in edges],
                                 "counts": [int(c) for c in counts]}},
        "summary": normality,
        "missing_handling": f"{int(df[col].isna().sum())} missing value(s) excluded.",
        "assumptions": [], "limitations": ["Shapiro-Wilk is very sensitive in large samples; inspect the shape too."],
    }


def analysis_scatter(df: pd.DataFrame, req: AnalyzeRequest) -> dict[str, Any]:
    if not req.x or not req.y:
        raise ValidationFailed("Choose numeric X and Y columns for the scatter plot.")
    _require_columns(df, [req.x, req.y])
    _numeric(df, [req.x, req.y])
    sub = df[[req.x, req.y]].dropna()
    if len(sub) < 3:
        raise ValidationFailed("At least 3 complete cases are required.")
    sample = sub.sample(MAX_SCATTER_POINTS, random_state=0) if len(sub) > MAX_SCATTER_POINTS else sub
    r, p = stats.pearsonr(sub[req.x], sub[req.y]) if sub[req.x].nunique() > 1 and sub[req.y].nunique() > 1 else (float("nan"), float("nan"))
    return {
        "variables": [req.x, req.y], "n": int(len(sub)),
        "summary": {"pearson_r": _clean(r), "p_value": _clean(p), "points_plotted": int(len(sample))},
        "charts": {"scatter": {"type": "scatter", "x": req.x, "y": req.y,
                               "points": [[_clean(a), _clean(b)] for a, b in sample.itertuples(index=False)]}},
        "missing_handling": f"Listwise deletion: {len(df) - len(sub)} row(s) excluded.",
        "assumptions": ["Pearson r assumes a linear relationship."],
        "limitations": [f"At most {MAX_SCATTER_POINTS} randomly sampled points are plotted."],
    }


ANALYSES: dict[str, Callable[[pd.DataFrame, AnalyzeRequest], dict[str, Any]]] = {
    "overview": analysis_overview,
    "descriptive": analysis_descriptive,
    "missing": analysis_missing,
    "duplicates": analysis_duplicates,
    "frequencies": analysis_frequencies,
    "correlation": analysis_correlation,
    "regression": analysis_regression,
    "histogram": analysis_histogram,
    "scatter": analysis_scatter,
}


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class AnalysisService:
    def __init__(self, db: Session, user_id: int | None = None) -> None:
        self.db = db
        self.settings = get_settings()
        self.datasets = DatasetRepository(db, user_id)
        self.runs = AnalysisRunRepository(db, user_id)

    def path_for(self, ds: Dataset) -> Path:
        return resolve_inside(self.settings.datasets_dir, ds.stored_filename)

    def load(self, ds: Dataset) -> pd.DataFrame:
        path = self.path_for(ds)
        if not path.exists():
            raise ValidationFailed("The stored dataset file is missing.")
        return load_dataframe(path, ds.file_type)

    def register_upload(self, stored: StoredFile, project_id: int | None) -> Dataset:
        try:
            df = load_dataframe(stored.path, stored.extension)
        except ValidationFailed:
            delete_stored(self.settings.datasets_dir, stored.stored_name)
            raise
        if df.empty or df.shape[1] == 0:
            delete_stored(self.settings.datasets_dir, stored.stored_name)
            raise ValidationFailed("The dataset contains no rows or columns.")
        return self.datasets.create(
            project_id=project_id, filename=stored.original_name, stored_filename=stored.stored_name,
            file_type=stored.extension, file_size=stored.size, row_count=int(df.shape[0]),
            column_count=int(df.shape[1]),
        )

    def preview(self, ds: Dataset, rows: int = 50) -> dict[str, Any]:
        df = self.load(ds)
        return {
            "dataset_id": ds.id, "filename": ds.filename, "rows": int(df.shape[0]), "columns": int(df.shape[1]),
            "column_info": [
                {"name": c, "dtype": str(df[c].dtype), "missing": int(df[c].isna().sum()),
                 "unique": int(df[c].nunique(dropna=True)),
                 "numeric": c in numeric_columns(df)}
                for c in df.columns
            ],
            "duplicate_rows": int(df.duplicated().sum()),
            "head": table(df.head(rows)),
        }

    def analyze(self, ds: Dataset, req: AnalyzeRequest) -> AnalysisRun:
        df = self.load(ds)
        results = ANALYSES[req.analysis_type](df, req)
        results = {"dataset_name": ds.filename, "analysis_type": req.analysis_type,
                   "total_rows": int(len(df)), **results}
        params = req.model_dump(exclude_none=True)
        logger.info("Analysis %s on dataset %s (n=%s)", req.analysis_type, ds.id, results.get("n"))
        return self.runs.create(dataset_id=ds.id, analysis_type=req.analysis_type,
                                parameters=json.dumps(params), results=json.dumps(results, default=str))

    def delete(self, ds: Dataset) -> None:
        stored = ds.stored_filename
        self.datasets.delete(ds)
        clear_cache()
        delete_stored(self.settings.datasets_dir, stored)

    # --- Export ----------------------------------------------------------------
    def export(self, ds: Dataset, fmt: str, analysis_id: int | None = None) -> tuple[bytes, str, str]:
        runs = list(self.runs.list_for_dataset(ds.id))
        if analysis_id is not None:
            runs = [r for r in runs if r.id == analysis_id]
            if not runs:
                raise ValidationFailed(f"Analysis {analysis_id} does not belong to this dataset.")
        if not runs:
            raise ValidationFailed("There are no saved analyses to export for this dataset.")
        stem = Path(ds.filename).stem[:50] or "dataset"
        if fmt == "json":
            payload = [{"id": r.id, "analysis_type": r.analysis_type, "created_at": str(r.created_at),
                        "parameters": json.loads(r.parameters), "results": json.loads(r.results)} for r in runs]
            return json.dumps(payload, indent=2).encode(), "application/json", f"{stem}_analyses.json"
        if fmt == "csv":
            run = runs[0]
            res = json.loads(run.results)
            tables = res.get("tables") or {}
            if not tables:
                raise ValidationFailed("This analysis has no tabular results to export as CSV.")
            name, tbl = next(iter(tables.items()))
            frame = pd.DataFrame(tbl["rows"], columns=tbl["columns"])
            return frame.to_csv(index=False).encode(), "text/csv", f"{stem}_{run.analysis_type}_{run.id}.csv"
        if fmt == "xlsx":
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
                index_rows = []
                for run in runs:
                    res = json.loads(run.results)
                    index_rows.append({
                        "analysis_id": run.id, "type": run.analysis_type, "created_at": str(run.created_at),
                        "dataset": res.get("dataset_name"), "variables": ", ".join(map(str, res.get("variables", []))),
                        "n": res.get("n"), "missing_handling": res.get("missing_handling"),
                        "summary": json.dumps(res.get("summary")) if res.get("summary") else "",
                    })
                    for t_name, tbl in (res.get("tables") or {}).items():
                        sheet = f"{run.id}_{t_name}"[:31]
                        pd.DataFrame(tbl["rows"], columns=tbl["columns"]).to_excel(writer, sheet_name=sheet, index=False)
                pd.DataFrame(index_rows).to_excel(writer, sheet_name="analyses", index=False)
            return (buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"{stem}_analyses.xlsx")
        raise ValidationFailed("Export format must be one of: xlsx, csv, json")
