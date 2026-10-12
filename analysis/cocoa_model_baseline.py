"""Execution-aligned cocoa model baseline using dated LSEG contracts.

Run from the repository root after importing ``cocoa_futures_contracts``:

    PYTHONPATH=src python analysis/cocoa_model_baseline.py

Candidate and threshold selection stop at 2023. The 2024+ period has already
been observed during this project and is reported only as a consumed diagnostic.
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.cocoa_model import (  # noqa: E402
    COCOA_TARGET,
    FEATURE_SETS,
    weekly_model_frame,
)
from pipeline.signal_eval import evaluate  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

RESULTS_PATH = ROOT / "analysis" / "cocoa_model_baseline_results.csv"
REPORT_PATH = ROOT / "analysis" / "cocoa_model_baseline_report.md"
MODELS = ("ridge", "logistic", "boosting")
HOLDOUT_YEAR = 2024


def _row(result) -> dict[str, object]:
    development = asdict(result.development)
    diagnostic = asdict(result.holdout)
    return {
        "candidate": f"{result.feature_set}_{result.model}",
        "feature_set": result.feature_set,
        "model": result.model,
        "features": len(result.features),
        "cost_bps": result.cost_rate * 10_000,
        **{f"development_{key}": value for key, value in development.items()},
        **{f"consumed_2024_plus_{key}": value for key, value in diagnostic.items()},
    }


def _fmt(value: float, kind: str = "number") -> str:
    if not np.isfinite(value):
        return "n/a"
    if kind == "pct":
        return f"{value:.2%}"
    return f"{value:+.2f}"


def main() -> None:
    store = ProcessedStore(default_data_root())
    contracts = store.read("cocoa_futures_contracts")
    power = store.read("nasa_power_point_daily")
    frame = weekly_model_frame(contracts, power)
    development = frame.loc[frame["year"] < HOLDOUT_YEAR]
    cost_bps = float(development["cost_rate"].median() * 10_000)

    evaluations = []
    for feature_set, features in FEATURE_SETS.items():
        for model in MODELS:
            evaluations.append(evaluate(
                frame,
                features=tuple(features),
                feature_set=feature_set,
                target=COCOA_TARGET,
                horizon=5,
                model_name=model,
                cost_bps_per_leg=cost_bps,
                holdout_year=HOLDOUT_YEAR,
                outer_splits=5,
            ))

    results = pd.DataFrame([_row(result) for result in evaluations]).sort_values(
        ["development_sharpe", "development_total_net"], ascending=False,
        na_position="last", ignore_index=True,
    )
    results.to_csv(RESULTS_PATH, index=False)

    best = results.iloc[0]
    best_by_set = (results.sort_values(
        ["development_sharpe", "development_total_net"], ascending=False,
        na_position="last")
        .groupby("feature_set", as_index=False).first()
        .set_index("feature_set"))
    market = best_by_set.loc["market_only"]
    combined = best_by_set.loc["weather_market"]
    weather_delta = float(combined["development_sharpe"] - market["development_sharpe"])

    lines = [
        "# Cocoa model baseline v1",
        "",
        "## Result",
        "",
        (
            f"The development-ranked candidate is **{best['candidate']}**, with "
            f"development Sharpe {_fmt(float(best['development_sharpe']))}, "
            f"net return {_fmt(float(best['development_total_net']), 'pct')}, and "
            f"{int(best['development_positive_folds'])}/{int(best['development_folds'])} "
            "positive forward folds."
        ),
        "",
        (
            "Adding weather to the best market-only specification changes "
            f"development Sharpe by {weather_delta:+.2f}. This comparison—not the "
            "largest individual model result—is the primary test of whether the "
            "climate data adds cocoa timing information."
        ),
        "",
        (
            f"The leading candidate still trails its expanding-training-mean "
            f"long/short baseline in net return "
            f"({_fmt(float(best['development_total_net']), 'pct')} versus "
            f"{_fmt(float(best['development_baseline_total_net']), 'pct')}) and "
            "falls below the 0.50 Sharpe threshold used in the prior final-validation "
            "protocol."
        ),
        "",
        "No candidate is eligible for promotion from this round. It already misses "
        "those preliminary performance checks; the 2024+ period is also a consumed "
        "diagnostic, dated ICE margin history is not yet attached, and uncertainty "
        "tests have not yet been run.",
        "",
        "## Data and execution",
        "",
        f"- Dated contracts: {contracts['contract'].nunique()} across "
        f"{contracts['date'].min().date()}–{contracts['date'].max().date()} "
        f"({len(contracts):,} rows).",
        f"- Weekly decisions: {len(frame):,}; development through 2023: "
        f"{len(development):,}; consumed 2024+: {int((frame['year'] >= 2024).sum()):,}.",
        f"- Entry/exit: next same-contract open to the open five sessions later; "
        f"10-business-day roll buffer.",
        f"- Cost used for selection: {cost_bps:.2f} bps round trip, the development-only "
        "median of one ICE tick plus $2.50 commission per side.",
        f"- Weather coverage at decisions: "
        f"{int(frame['weather_countries'].min())} countries, joined by publication date.",
        "",
        "## Candidate comparison",
        "",
        "| Candidate | Dev trades | Dev net | Dev Sharpe | Positive folds | "
        "Dev R² | Consumed 2024+ net | Consumed Sharpe |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results.itertuples(index=False):
        lines.append(
            f"| {row.candidate} | {row.development_trades} | "
            f"{_fmt(row.development_total_net, 'pct')} | {_fmt(row.development_sharpe)} | "
            f"{row.development_positive_folds}/{row.development_folds} | "
            f"{_fmt(row.development_r2)} | "
            f"{_fmt(row.consumed_2024_plus_total_net, 'pct')} | "
            f"{_fmt(row.consumed_2024_plus_sharpe)} |"
        )
    lines.extend([
        "",
        "## Interpretation guardrails",
        "",
        "- Candidate selection and threshold tuning use pre-2024 data only.",
        "- The 2024 cocoa shock is not an untouched holdout and cannot validate the model.",
        "- R² is secondary: the decision objective is net return after the stated cost.",
        "- Missing dated margin prevents a capital-constrained performance claim.",
        "- A follow-up must freeze one specification before bootstrap, circular-shift, "
        "double-cost, and margin-constrained tests.",
        "",
    ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {RESULTS_PATH.relative_to(ROOT)}")
    print(f"wrote {REPORT_PATH.relative_to(ROOT)}")
    print(results[[
        "candidate", "development_trades", "development_total_net",
        "development_sharpe", "development_positive_folds",
        "consumed_2024_plus_total_net", "consumed_2024_plus_sharpe",
    ]].to_string(index=False))


if __name__ == "__main__":
    main()
