"""Can market-neutral targets turn the weather null into a usable signal?

Builds weekly, publication-aligned decisions and compares three target designs,
three feature sets, and three deliberately small model families. Hyperparameters
and buy/sell/hold thresholds are chosen inside each training block; 2024 onward
is held out from those choices. Results include five basis points of round-trip
cost per traded leg.

This is a model-development report, not an order-execution system. A model must
clear the explicit gate in pipeline.signal_eval.Evaluation before it can be
described as a candidate for paper trading.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.config import load_geography  # noqa: E402
from pipeline.signal_eval import (  # noqa: E402
    DEFAULT_COST_BPS_PER_LEG,
    HOLDOUT_YEAR,
    MODEL_NAMES,
    Evaluation,
    evaluate,
)
from pipeline.signals import FEATURE_SETS, TARGETS, build  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

DEFAULT_REPORT = ROOT / "analysis" / "ml_signal_baseline_report.md"
DEFAULT_CSV = ROOT / "analysis" / "ml_signal_baseline_results.csv"


def _number(value: float, digits: int = 3) -> str:
    return "—" if not np.isfinite(value) else f"{value:+.{digits}f}"


def _percent(value: float, digits: int = 1) -> str:
    return "—" if not np.isfinite(value) else f"{value:.{digits}%}"


def result_rows(evaluations: list[Evaluation]) -> pd.DataFrame:
    rows = []
    for item in evaluations:
        dev, holdout = item.development, item.holdout
        rows.append({
            "horizon": item.horizon,
            "target": item.target,
            "target_label": item.target_label,
            "feature_set": item.feature_set,
            "model": item.model,
            "features": len(item.features),
            "cost_bps": item.cost_rate * 10_000,
            "dev_observations": dev.observations,
            "dev_r2": dev.r2,
            "dev_trades": dev.trades,
            "dev_coverage": dev.coverage,
            "dev_hit_rate": dev.hit_rate,
            "dev_total_net": dev.total_net,
            "dev_sharpe": dev.sharpe,
            "dev_max_drawdown": dev.max_drawdown,
            "dev_positive_folds": dev.positive_folds,
            "dev_folds": dev.folds,
            "dev_net_ex_2012": dev.net_excluding_2012,
            "dev_baseline_net": dev.baseline_total_net,
            "dev_incremental_net": dev.total_net - dev.baseline_total_net,
            "holdout_observations": holdout.observations,
            "holdout_trades": holdout.trades,
            "holdout_coverage": holdout.coverage,
            "holdout_hit_rate": holdout.hit_rate,
            "holdout_total_net": holdout.total_net,
            "holdout_sharpe": holdout.sharpe,
            "holdout_max_drawdown": holdout.max_drawdown,
            "holdout_baseline_net": holdout.baseline_total_net,
            "holdout_incremental_net": holdout.total_net - holdout.baseline_total_net,
            "passes_gate": item.passes_gate,
        })
    return pd.DataFrame(rows)


def _best(rows: pd.DataFrame, target_group: str) -> pd.Series | None:
    if target_group == "raw":
        candidates = rows.loc[rows["target"] == "raw_corn"]
    else:
        candidates = rows.loc[rows["target"] != "raw_corn"]
    finite = candidates.loc[candidates["dev_sharpe"].notna()]
    if finite.empty:
        return None
    return finite.sort_values(
        ["dev_sharpe", "holdout_total_net"], ascending=False).iloc[0]


def conclusion(rows: pd.DataFrame) -> tuple[str, pd.Series | None, pd.Series | None]:
    raw = _best(rows, "raw")
    relative = _best(rows, "relative")
    if relative is None:
        return "No market-neutral configuration produced a scorable strategy.", raw, relative
    improved = (raw is None or (
        relative["dev_sharpe"] > raw["dev_sharpe"]
        and relative["holdout_sharpe"] > raw["holdout_sharpe"]))
    if improved and bool(relative["passes_gate"]):
        message = ("A market-neutral configuration improves on the raw-return baseline "
                   "and clears the explicit paper-trading gate.")
    elif improved:
        message = ("Market-neutral modeling improves on the raw-return comparison, but "
                   "no configuration clears the paper-trading gate. The deliverable "
                   "signal remains HOLD / NO DEMONSTRATED EDGE.")
    else:
        message = ("Market-neutral modeling does not improve on the raw-return null in "
                   "both development folds and the fixed holdout. The deliverable "
                   "signal remains HOLD / NO DEMONSTRATED EDGE.")
    return message, raw, relative


def _configuration(row: pd.Series | None) -> str:
    if row is None:
        return "none"
    return (f"{row['target_label']} / {row['feature_set']} / {row['model']} "
            f"({int(row['horizon'])}d)")


def _table(rows: pd.DataFrame) -> list[str]:
    columns = [
        "Target", "Features", "Model", "Dev R²", "Dev Sharpe", "Trades",
        "Hit", "Dev net", "vs constant", "Holdout net", "vs constant",
        "Holdout Sharpe", "Gate",
    ]
    output = ["| " + " | ".join(columns) + " |",
              "|" + "|".join(["---"] * len(columns)) + "|"]
    ordered = rows.sort_values(
        ["target", "feature_set", "model", "horizon"], ignore_index=True)
    for _, row in ordered.iterrows():
        output.append("| " + " | ".join([
            str(row["target_label"]), str(row["feature_set"]), str(row["model"]),
            _number(float(row["dev_r2"])), _number(float(row["dev_sharpe"]), 2),
            str(int(row["dev_trades"])), _percent(float(row["dev_hit_rate"])),
            _percent(float(row["dev_total_net"])), _percent(float(row["dev_incremental_net"])),
            _percent(float(row["holdout_total_net"])),
            _percent(float(row["holdout_incremental_net"])),
            _number(float(row["holdout_sharpe"]), 2),
            "PASS" if bool(row["passes_gate"]) else "fail",
        ]) + " |")
    return output


def render_report(rows: pd.DataFrame, frames: dict[int, pd.DataFrame],
                  feature_sets: dict[str, tuple[str, ...]]) -> str:
    message, raw, relative = conclusion(rows)
    spans = []
    for horizon, frame in sorted(frames.items()):
        spans.append(
            f"{horizon}d: {len(frame)} weekly decisions, "
            f"{frame['date'].min().date()}–{frame['date'].max().date()}")
    lines = [
        "# ML Signal Baseline",
        "",
        "## Decision",
        "",
        f"**{message}**",
        "",
        f"Best raw configuration: {_configuration(raw)}.",
        "",
        f"Best market-neutral configuration: {_configuration(relative)}.",
        "",
        "## Modeling contract",
        "",
        "- Decisions occur once per Drought Monitor publication week, on the first",
        "  tradable date on or after publication.",
        "- Targets are raw corn, corn minus wheat, and corn minus a trailing-beta",
        "  soybean/wheat basket.",
        "- Crop-stage and weather features use point-in-time values. Price regimes",
        "  exclude the target-entry close; CPC forecasts are delayed one session",
        "  because the archive has issuance dates but not release times.",
        "- Models are Ridge regression, multinomial logistic regression, and shallow",
        "  histogram gradient boosting.",
        "- Hyperparameters and buy/sell/hold thresholds are selected in an inner",
        "  trailing validation block. Outer folds move forward with an embargo.",
        f"- Calendar years {HOLDOUT_YEAR} onward are excluded from all model and",
        "  threshold selection, then evaluated once as a fixed holdout.",
        f"- Costs are {DEFAULT_COST_BPS_PER_LEG:g} basis points per traded leg; raw",
        "  signals carry one leg and spreads carry two.",
        "- Longer horizons are thinned so adjacent decision labels do not overlap.",
        "",
        "Coverage: " + "; ".join(spans) + ".",
        "",
        "Feature sets: " + "; ".join(
            f"{name} ({len(features)})" for name, features in feature_sets.items()) + ".",
        "Forecast-enhanced configurations begin with CPC coverage in 2012; missing",
        "cells inside the covered period are imputed within each training fold.",
        "",
        "## Acceptance gate",
        "",
        "A configuration passes only with at least 30 development trades, development",
        "Sharpe above 0.50, positive net return in at least 60% of forward folds,",
        "positive development return excluding 2012, and net returns above a constant",
        "position chosen from the training mean in both development and holdout.",
        "Development drawdown must be no worse than -25%, holdout drawdown no worse",
        "than -15%, and the holdout must contain at least three trades with positive",
        "net return.",
        "Passing is necessary for paper trading, not",
        "evidence sufficient for live capital.",
        "",
        "## Comparison",
        "",
        *_table(rows),
        "",
        "## Interpretation limits",
        "",
        "- This report compares a finite set of predeclared targets, features, and",
        "  small models. It does not establish that no other data or strategy can work.",
        "- Reviewing the holdout result consumes it for future model redesign. Any",
        "  revised specification needs a new untouched period or paper-trading sample.",
        "- Returns are research targets built from continuous Yahoo series. Before",
        "  execution, contract-level rolls, margins, liquidity, and fill assumptions",
        "  require separate validation.",
        "- A PASS is a candidate for paper trading. A failure means the model's honest",
        "  current output is HOLD / NO DEMONSTRATED EDGE.",
        "",
    ]
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("--horizons", nargs="+", type=int, default=[5])
    result.add_argument("--feature-sets", nargs="+", choices=tuple(FEATURE_SETS),
                        default=list(FEATURE_SETS))
    result.add_argument("--models", nargs="+", choices=MODEL_NAMES,
                        default=list(MODEL_NAMES))
    result.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    result.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    horizons = tuple(sorted(set(args.horizons)))
    if any(horizon <= 0 for horizon in horizons):
        raise SystemExit("horizons must be positive trading-day counts")

    store = ProcessedStore(default_data_root())
    frames, available = build(store, load_geography(), horizons=horizons)
    selected_features = {name: available[name] for name in args.feature_sets}

    evaluations: list[Evaluation] = []
    total = len(horizons) * len(TARGETS) * len(selected_features) * len(args.models)
    count = 0
    for horizon in horizons:
        for target in TARGETS:
            for feature_name, features in selected_features.items():
                for model_name in args.models:
                    count += 1
                    print(f"[{count:02d}/{total}] {horizon}d {target.name} "
                          f"{feature_name} {model_name}", flush=True)
                    evaluations.append(evaluate(
                        frames[horizon], features=features, feature_set=feature_name,
                        target=target, horizon=horizon, model_name=model_name))

    rows = result_rows(evaluations)
    report = render_report(rows, frames, selected_features)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    rows.to_csv(args.csv, index=False, float_format="%.8f")
    print(f"\n{report.splitlines()[4]}")
    print(f"report: {args.report}")
    print(f"results: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
