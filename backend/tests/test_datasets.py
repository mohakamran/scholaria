"""Dataset upload, preview and validated statistical analyses."""

import io

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def dataset_id(client):
    rng = np.random.default_rng(0)
    n = 60
    hours = rng.normal(20, 5, n).round(1)
    df = pd.DataFrame({
        "hours": hours,
        "score": (2 * hours + rng.normal(0, 3, n)).round(1),
        "group": rng.choice(["A", "B"], n),
        "age": rng.integers(20, 40, n).astype(float),
    })
    df.loc[[3, 7], "age"] = np.nan
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)  # one duplicate row
    buf = io.BytesIO(df.to_csv(index=False).encode())
    response = client.post("/api/datasets/upload", files={"file": ("study.csv", buf, "text/csv")})
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["row_count"] == 61 and data["column_count"] == 4
    return data["id"]


def analyze(client, dataset_id, **body):
    response = client.post(f"/api/datasets/{dataset_id}/analyze", json=body)
    assert response.status_code == 201, response.text
    return response.json()["results"]


def test_preview(client, dataset_id):
    data = client.get(f"/api/datasets/{dataset_id}/preview", params={"rows": 5}).json()
    assert data["rows"] == 61 and len(data["head"]["rows"]) == 5
    info = {c["name"]: c for c in data["column_info"]}
    assert info["age"]["missing"] == 2 and info["group"]["numeric"] is False
    assert data["duplicate_rows"] == 1


def test_descriptive_statistics_match_pandas(client, dataset_id):
    res = analyze(client, dataset_id, analysis_type="descriptive", columns=["hours", "age"])
    rows = {r[0]: dict(zip(res["tables"]["descriptive_statistics"]["columns"], r))
            for r in res["tables"]["descriptive_statistics"]["rows"]}
    preview = client.get(f"/api/datasets/{dataset_id}/preview", params={"rows": 500}).json()
    df = pd.DataFrame(preview["head"]["rows"], columns=preview["head"]["columns"])
    assert rows["hours"]["mean"] == pytest.approx(df["hours"].mean(), rel=1e-6)
    assert rows["hours"]["sd"] == pytest.approx(df["hours"].std(), rel=1e-6)
    assert rows["age"]["n"] == 59 and rows["age"]["missing"] == 2
    assert res["dataset_name"] == "study.csv" and res["variables"] == ["hours", "age"]
    assert res["missing_handling"] and res["assumptions"] and res["limitations"]


def test_missing_value_analysis(client, dataset_id):
    res = analyze(client, dataset_id, analysis_type="missing")
    assert res["summary"]["rows_with_missing"] == 2
    rows = {r[0]: r for r in res["tables"]["missing_values"]["rows"]}
    assert rows["age"][1] == 2


def test_duplicates_and_frequencies(client, dataset_id):
    assert analyze(client, dataset_id, analysis_type="duplicates")["summary"]["duplicate_rows"] == 1
    res = analyze(client, dataset_id, analysis_type="frequencies", columns=["group"])
    counts = res["charts"]["group"]["values"]
    assert sum(counts) == 61


def test_correlation_and_regression(client, dataset_id):
    corr = analyze(client, dataset_id, analysis_type="correlation", columns=["hours", "score"])
    r = corr["tables"]["correlation_coefficients"]["rows"][0][2]
    assert r > 0.8 and corr["n"] == 61
    reg = analyze(client, dataset_id, analysis_type="regression", dependent="score", independents=["hours", "group"])
    terms = {row[0]: row for row in reg["tables"]["coefficients"]["rows"]}
    assert terms["hours"][1] == pytest.approx(2.0, abs=0.3)
    assert "group_B" in terms and reg["summary"]["r_squared"] > 0.7
    assert reg["n"] == 61 and "Listwise" in reg["missing_handling"]


def test_histogram_and_scatter(client, dataset_id):
    hist = analyze(client, dataset_id, analysis_type="histogram", x="hours", bins=10)
    assert sum(hist["charts"]["histogram"]["counts"]) == 61
    scatter = analyze(client, dataset_id, analysis_type="scatter", x="hours", y="score")
    assert len(scatter["charts"]["scatter"]["points"]) == 61


def test_invalid_inputs_rejected(client, dataset_id):
    bad_col = client.post(f"/api/datasets/{dataset_id}/analyze", json={"analysis_type": "descriptive", "columns": ["nope"]})
    assert bad_col.status_code == 422
    non_numeric = client.post(f"/api/datasets/{dataset_id}/analyze", json={"analysis_type": "correlation", "columns": ["group", "hours"]})
    assert non_numeric.status_code == 422
    unknown = client.post(f"/api/datasets/{dataset_id}/analyze", json={"analysis_type": "exec_python"})
    assert unknown.status_code == 422


def test_analyses_saved_and_exported(client, dataset_id):
    analyze(client, dataset_id, analysis_type="descriptive")
    analyze(client, dataset_id, analysis_type="missing")
    runs = client.get(f"/api/datasets/{dataset_id}/analyses").json()
    assert len(runs) == 2
    xlsx = client.get(f"/api/datasets/{dataset_id}/export", params={"format": "xlsx"})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    sheets = pd.read_excel(io.BytesIO(xlsx.content), sheet_name=None)
    assert "analyses" in sheets
    csv = client.get(f"/api/datasets/{dataset_id}/export", params={"format": "csv", "analysis_id": runs[0]["id"]})
    assert csv.status_code == 200 and b"variable" in csv.content


def test_interpretation_uses_stored_results(client, dataset_id, fake_ollama):
    run_id = client.post(f"/api/datasets/{dataset_id}/analyze", json={"analysis_type": "descriptive"}).json()["id"]
    data = client.post(f"/api/datasets/{dataset_id}/analyses/{run_id}/interpret", json={}).json()
    assert data["explanation"]
    assert "descriptive_statistics" in fake_ollama.requests[-1]["messages"][1]["content"]


def test_bad_dataset_file(client):
    response = client.post("/api/datasets/upload", files={"file": ("x.xlsx", io.BytesIO(b"not excel"), "application/octet-stream")})
    assert response.status_code == 422
