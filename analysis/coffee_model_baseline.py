"""Predeclared Coffee C baseline using dated LSEG contracts and Brazil weather.

Run from the repository root after importing ``coffee_futures_contracts``:

    PYTHONPATH=src python analysis/coffee_model_baseline.py

The protocol in ``config/coffee_model_validation.yaml`` was committed to disk
before this script inspected forward Coffee C returns.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.coffee_model import (  # noqa: E402
    COFFEE_CURVE_TARGET,
    COFFEE_OUTRIGHT_TARGET,
    FEATURE_SETS,
    coffee_model_frame,
    frost_event_outcomes,
)
from pipeline.signal_eval import evaluate  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

PROTOCOL_PATH = ROOT / "config" / "coffee_model_validation.yaml"
RESULTS_PATH = ROOT / "analysis" / "coffee_model_baseline_results.csv"
EVENT_SUMMARY_PATH = ROOT / "analysis" / "coffee_frost_event_summary.csv"
REPORT_PATH = ROOT / "analysis" / "coffee_model_baseline_report.md"
LEDGER_PATH = ROOT / "data" / "analysis" / "coffee_frost_event_ledger.parquet"
MODELS = ("ridge", "logistic")
TARGETS = (COFFEE_OUTRIGHT_TARGET, COFFEE_CURVE_TARGET)
HOLDOUT_YEAR = 2024
MODEL_START_DATE = pd.Timestamp("2011-10-03")
BASELINE_CONTRACTS = {
    f"KC{month}{year % 100:02d}"
    for year in range(2012, 2027)
    for month in "HKNUZ"
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row(result) -> dict[str, object]:
    development = asdict(result.development)
    diagnostic = asdict(result.holdout)
    return {
        "candidate": f"{result.target}_{result.feature_set}_{result.model}",
        "target": result.target,
        "feature_set": result.feature_set,
        "model": result.model,
        "features": len(result.features),
        "cost_bps": result.cost_rate * 10_000,
        **{f"development_{key}": value for key, value in development.items()},
        **{f"consumed_2024_plus_{key}": value for key, value in diagnostic.items()},
    }


def _event_summary(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for sample in ("development", "consumed_2024_plus"):
        for horizon in (20, 60):
            subset = events.loc[
                (events["sample"] == sample)
                & (events["horizon_sessions"] == horizon)
            ]
            for target in ("outright", "curve"):
                column = f"target_coffee_{target}_{horizon}"
                cost_column = f"cost_rate_{target}"
                valid = subset[column].notna() & subset[cost_column].notna()
                values = subset.loc[valid, column].astype(float)
                net = values - subset.loc[valid, cost_column].astype(float)
                rows.append({
                    "sample": sample,
                    "horizon_sessions": horizon,
                    "target": target,
                    "events": len(values),
                    "mean_gross_return": values.mean() if len(values) else np.nan,
                    "median_gross_return": values.median() if len(values) else np.nan,
                    "mean_net_return": net.mean() if len(net) else np.nan,
                    "median_net_return": net.median() if len(net) else np.nan,
                    "positive_gross_events": int((values > 0).sum()),
                    "positive_net_events": int((net > 0).sum()),
                })
    return pd.DataFrame(rows)


def _fmt(value: float, kind: str = "number") -> str:
    if not np.isfinite(value):
        return "n/a"
    if kind == "pct":
        return f"{value:.2%}"
    return f"{value:+.2f}"


def main() -> None:
    store = ProcessedStore(default_data_root())
    contracts = store.read("coffee_futures_contracts")
    power = store.read("nasa_power_point_daily")
    baseline_contracts = contracts.loc[
        (contracts["date"] >= MODEL_START_DATE)
        & contracts["contract"].isin(BASELINE_CONTRACTS)
    ].copy()
    frame = coffee_model_frame(baseline_contracts, power)
    development = frame.loc[frame["year"] < HOLDOUT_YEAR]

    cost_bps = {
        "coffee_outright": float(
            development["cost_rate_outright"].median() * 10_000
        ),
        # signal_eval multiplies the supplied per-leg rate by target.legs.
        "coffee_curve": float(
            development["cost_rate_curve"].median() * 10_000 / 2.0
        ),
    }
    evaluations = []
    for target in TARGETS:
        for feature_set, features in FEATURE_SETS.items():
            for model in MODELS:
                evaluations.append(evaluate(
                    frame,
                    features=tuple(features),
                    feature_set=feature_set,
                    target=target,
                    horizon=20,
                    model_name=model,
                    cost_bps_per_leg=cost_bps[target.name],
                    holdout_year=HOLDOUT_YEAR,
                    outer_splits=5,
                ))

    results = pd.DataFrame([_row(result) for result in evaluations]).sort_values(
        ["target", "development_sharpe", "development_total_net"],
        ascending=[True, False, False], na_position="last", ignore_index=True,
    )
    results.to_csv(RESULTS_PATH, index=False)

    events = frost_event_outcomes(baseline_contracts, power)
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    events.to_parquet(LEDGER_PATH, index=False)
    event_summary = _event_summary(events)
    event_summary.to_csv(EVENT_SUMMARY_PATH, index=False)

    best = (results.sort_values(
        ["development_sharpe", "development_total_net"],
        ascending=False, na_position="last")
        .groupby("target", as_index=False).first().set_index("target"))
    deltas = []
    for target in ("coffee_outright", "coffee_curve"):
        for model in MODELS:
            pair = results.loc[
                (results["target"] == target) & (results["model"] == model)
            ].set_index("feature_set")
            deltas.append({
                "target": target,
                "model": model,
                "sharpe_delta": float(
                    pair.loc["weather_market", "development_sharpe"]
                    - pair.loc["market_only", "development_sharpe"]
                ),
                "net_delta": float(
                    pair.loc["weather_market", "development_total_net"]
                    - pair.loc["market_only", "development_total_net"]
                ),
            })
    delta_frame = pd.DataFrame(deltas)

    lines = [
        "# Coffee model baseline v1",
        "",
        "## Research boundary",
        "",
        f"- Protocol: `coffee-model-baseline-v1`; SHA-256 `{_sha256(PROTOCOL_PATH)}`.",
        "- The protocol was written before forward-return ranking.",
        "- Model selection ends in 2023; 2024+ is a consumed diagnostic only.",
        "- Twenty-session decisions do not overlap. Sixty-session results are a "
        "frost event study, not a fitted model.",
        "",
        "## Model comparison",
        "",
    ]
    for target, label in (
        ("coffee_outright", "Coffee C outright"),
        ("coffee_curve", "nearby-minus-deferred curve"),
    ):
        row = best.loc[target]
        lines.append(
            f"The best development-ranked **{label}** candidate is "
            f"`{row['candidate']}`: {_fmt(float(row['development_total_net']), 'pct')} "
            f"net, Sharpe {_fmt(float(row['development_sharpe']))}, and "
            f"{int(row['development_positive_folds'])}/{int(row['development_folds'])} "
            "positive forward folds."
        )
        lines.append("")

    lines.extend([
        "The primary weather test is the within-model change from `market_only` "
        "to `weather_market`:",
        "",
        "| Target | Model | Δ development net | Δ development Sharpe |",
        "|---|---|---:|---:|",
    ])
    for row in delta_frame.itertuples(index=False):
        lines.append(
            f"| {row.target} | {row.model} | {_fmt(row.net_delta, 'pct')} | "
            f"{_fmt(row.sharpe_delta)} |"
        )

    lines.extend([
        "",
        "| Candidate | Dev trades | Dev net | Dev Sharpe | Positive folds | "
        "Consumed 2024+ net | Consumed Sharpe |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for row in results.itertuples(index=False):
        lines.append(
            f"| {row.candidate} | {row.development_trades} | "
            f"{_fmt(row.development_total_net, 'pct')} | "
            f"{_fmt(row.development_sharpe)} | "
            f"{row.development_positive_folds}/{row.development_folds} | "
            f"{_fmt(row.consumed_2024_plus_total_net, 'pct')} | "
            f"{_fmt(row.consumed_2024_plus_sharpe)} |"
        )

    lines.extend([
        "",
        "## Frost event study",
        "",
        "Frost means the coldest of the three Brazil points fell below 4°C in "
        "June–August. Days within ten calendar days are one episode. These tiny "
        "samples are descriptive and cannot establish a trading rule.",
        "",
        "| Sample | Horizon | Target | Events | Mean gross | Mean net | +net |",
        "|---|---:|---|---:|---:|---:|---:|",
    ])
    for row in event_summary.itertuples(index=False):
        lines.append(
            f"| {row.sample} | {row.horizon_sessions} | {row.target} | "
            f"{row.events} | {_fmt(row.mean_gross_return, 'pct')} | "
            f"{_fmt(row.mean_net_return, 'pct')} | {row.positive_net_events} |"
        )

    lines.extend([
        "",
        "## Data and execution",
        "",
        f"- Dated contracts: {baseline_contracts['contract'].nunique()} across "
        f"{baseline_contracts['date'].min().date()}–{baseline_contracts['date'].max().date()} "
        f"({len(baseline_contracts):,} rows).",
        f"- Independent model decisions: {len(frame)}; development through 2023: "
        f"{len(development)}; consumed 2024+: {int((frame['year'] >= 2024).sum())}.",
        "- Entry/exit: next shared open through the open 20 shared sessions later; "
        "25-business-day roll buffer.",
        f"- Development median estimated round-trip cost: "
        f"{cost_bps['coffee_outright']:.2f} bps outright and "
        f"{2 * cost_bps['coffee_curve']:.2f} bps for both curve legs.",
        "- Weather uses only Sul de Minas, Cerrado Mineiro, and Mogiana, joined "
        "by NASA POWER publication date.",
        "",
        "## Interpretation guardrails",
        "",
        "- This round ranks a bounded preliminary candidate set; it cannot promote a model.",
        "- The frost sample is too small for significance or machine learning.",
        "- Curve returns use equal percentage-return legs; an integer-contract "
        "capital replay requires dated ICE margin and explicit spread sizing.",
        "- Any candidate worth retaining must next beat deterministic baselines "
        "under exact row costs, uncertainty tests, doubled costs, and a frozen gate.",
        "- Licensed row-level outcomes remain under ignored `data/`; committed "
        "outputs contain aggregates only.",
        "",
    ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {RESULTS_PATH.relative_to(ROOT)}")
    print(f"wrote {EVENT_SUMMARY_PATH.relative_to(ROOT)}")
    print(f"wrote {REPORT_PATH.relative_to(ROOT)}")
    print(results[[
        "candidate", "development_trades", "development_total_net",
        "development_sharpe", "development_positive_folds",
        "consumed_2024_plus_total_net", "consumed_2024_plus_sharpe",
    ]].to_string(index=False))
    print("\nfrost event aggregates")
    print(event_summary.to_string(index=False))


if __name__ == "__main__":
    main()
