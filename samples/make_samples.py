"""Generate the fictional sample paper and dataset used for demos and QA.

Run from the project root:  .venv/bin/python samples/make_samples.py
Everything in these files is invented for testing; it is not a real study.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pymupdf

HERE = Path(__file__).parent

PAGES = [
    ("Sleep Quality and Academic Productivity in Graduate Students",
     "SAMPLE DOCUMENT FOR TESTING - FICTIONAL STUDY AND DATA.\n\n"
     "Abstract. This fictional study examines whether sleep quality predicts self-reported academic productivity "
     "among graduate students, and whether daily screen time moderates this relationship.\n\n"
     "1. Introduction. Graduate students report high workloads and irregular sleep. The research objective is to "
     "estimate the association between sleep quality and productivity. Research question 1: Does sleep quality "
     "predict productivity? Research question 2: Does screen time moderate this relationship?"),
    ("2. Theoretical framework",
     "The study draws on the Conservation of Resources theory, which proposes that people strive to protect "
     "personal resources such as energy and attention. Poor sleep depletes these resources and is expected to "
     "reduce productivity. Hypothesis H1: Higher sleep quality is associated with higher productivity. "
     "Hypothesis H2: The association is weaker for students with high daily screen time."),
    ("3. Methodology",
     "Design. A cross-sectional online survey was used. Participants. The sample comprised 312 graduate students "
     "recruited from three universities using convenience sampling (mean age 27.4 years; 58% women). "
     "Measures. Sleep quality was measured with a 7-item sleep quality scale; productivity with a 5-item "
     "self-report productivity index; screen time as self-reported hours per day. Analysis. Data were analysed "
     "with descriptive statistics, Pearson correlations and multiple linear regression."),
    ("4. Findings and limitations",
     "Results. Sleep quality was positively associated with productivity (r = 0.46, p < .001). In the regression "
     "model, sleep quality remained a significant predictor after controlling for age and screen time "
     "(B = 0.52, p < .001); the interaction with screen time was small but significant. "
     "Limitations. The cross-sectional design prevents causal conclusions, all measures were self-reported, and "
     "convenience sampling limits generalisability. Future research should use longitudinal designs and "
     "objective sleep measures such as actigraphy."),
]


def make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for title, body in PAGES:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(60, 60, 540, 100), title, fontsize=15, fontname="helv")
        page.insert_textbox(pymupdf.Rect(60, 110, 540, 780), body, fontsize=11, fontname="helv")
    doc.set_metadata({"title": "Sleep Quality and Academic Productivity in Graduate Students (Sample)",
                      "author": "Scholaria sample generator"})
    doc.save(path)


def make_csv(path: Path) -> None:
    rng = np.random.default_rng(42)
    n = 312
    sleep = rng.normal(4.6, 1.1, n).clip(1, 7)
    screen = rng.normal(5.5, 2.0, n).clip(0.5, 14)
    age = rng.normal(27.4, 3.5, n).clip(21, 45).round()
    productivity = (1.2 + 0.52 * sleep - 0.06 * screen + 0.01 * age + rng.normal(0, 0.8, n)).clip(1, 7)
    df = pd.DataFrame({
        "participant": [f"P{i:03d}" for i in range(1, n + 1)],
        "age": age.astype(int),
        "gender": rng.choice(["woman", "man", "non-binary"], n, p=[0.58, 0.39, 0.03]),
        "university": rng.choice(["North", "Central", "Coastal"], n),
        "sleep_quality": sleep.round(2),
        "screen_time_hours": screen.round(1),
        "productivity": productivity.round(2),
    })
    df.loc[rng.choice(n, 9, replace=False), "screen_time_hours"] = np.nan
    df.to_csv(path, index=False)


if __name__ == "__main__":
    make_pdf(HERE / "sample_paper.pdf")
    make_csv(HERE / "sample_survey.csv")
    print("Created samples/sample_paper.pdf and samples/sample_survey.csv")
