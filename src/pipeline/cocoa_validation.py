"""Fixed-specification diagnostics for the selected cocoa model.

The model and its action thresholds are produced by the nested development
evaluation in :mod:`pipeline.signal_eval`.  This module does not refit or select
anything.  It aligns those already out-of-sample actions with row-specific ICE
contract costs, applies the no-overlap policy, and evaluates deterministic rule
baselines on exactly the same dates and outcomes.
"""

from __future__ import annotations

from math import ceil

import numpy as np
import pandas as pd

from .model_validation import (
    ValidationMetrics,
    apply_no_overlap,
    metrics,
)

BASELINE_RULES = ("no_trade", "long_only", "short_only", "momentum", "mean_reversion")


def align_predictions(predictions: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    """Apply exact row costs and overlap rules to frozen model predictions."""

    prediction_required = {"date", "actual", "score", "signal", "fold"}
    frame_required = {
        "date", "year", "entry_date", "exit_date", "target_cocoa_5",
        "cost_rate", "cocoa_mom20", "cocoa_vol20",
    }
    missing_predictions = sorted(prediction_required - set(predictions))
    missing_frame = sorted(frame_required - set(frame))
    if missing_predictions or missing_frame:
        raise ValueError(
            f"missing prediction columns={missing_predictions or 'none'}, "
            f"frame columns={missing_frame or 'none'}"
        )
    frozen = predictions[["date", "score", "signal", "fold"]].rename(
        columns={"signal": "raw_signal"}
    )
    metadata = frame[[
        "date", "year", "entry_date", "exit_date", "target_cocoa_5",
        "cost_rate", "cocoa_mom20", "cocoa_vol20",
    ]]
    ledger = frozen.merge(metadata, on="date", how="left", validate="one_to_one")
    if ledger[["entry_date", "exit_date", "target_cocoa_5", "cost_rate"]].isna().any().any():
        raise ValueError("a frozen prediction does not have a matching execution outcome")
    ledger = ledger.rename(columns={
        "target_cocoa_5": "gross",
        "cost_rate": "cost",
        "cocoa_mom20": "momentum",
        "cocoa_vol20": "volatility",
    }).sort_values("date", ignore_index=True)
    ledger["month"] = pd.to_datetime(ledger["date"]).dt.month
    ledger["signal"] = apply_no_overlap(
        ledger, ledger["raw_signal"].to_numpy(dtype="int64")
    )
    ledger["net"] = (
        ledger["signal"] * ledger["gross"]
        - ledger["signal"].abs() * ledger["cost"]
    )
    return ledger


def baseline_ledger(model_ledger: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Score one deterministic rule on the model's exact observations."""

    if rule not in BASELINE_RULES:
        raise ValueError(f"unknown cocoa baseline rule: {rule!r}")
    result = model_ledger.copy()
    if rule == "no_trade":
        raw = np.zeros(len(result), dtype="int64")
    elif rule == "long_only":
        raw = np.ones(len(result), dtype="int64")
    elif rule == "short_only":
        raw = -np.ones(len(result), dtype="int64")
    else:
        momentum = np.sign(result["momentum"].fillna(0.0)).astype("int64").to_numpy()
        raw = momentum if rule == "momentum" else -momentum
    result["raw_signal"] = raw
    result["signal"] = apply_no_overlap(result, raw)
    result["score"] = np.nan
    result["net"] = (
        result["signal"] * result["gross"]
        - result["signal"].abs() * result["cost"]
    )
    result["rule"] = rule
    return result


def cost_stress(ledger: pd.DataFrame, multiplier: float) -> tuple[pd.DataFrame, ValidationMetrics]:
    """Reprice frozen actions without changing a signal or threshold."""

    if multiplier < 0:
        raise ValueError("cost multiplier cannot be negative")
    stressed = ledger.copy()
    stressed["net"] = (
        stressed["signal"] * stressed["gross"]
        - stressed["signal"].abs() * multiplier * stressed["cost"]
    )
    return stressed, metrics(stressed)


def best_baseline(baselines: dict[str, pd.DataFrame]) -> tuple[str, ValidationMetrics]:
    """Rank rules by Sharpe and then total net, as declared by the protocol."""

    scored = [(name, metrics(ledger)) for name, ledger in baselines.items()]
    return max(scored, key=lambda item: (
        item[1].sharpe if np.isfinite(item[1].sharpe) else -np.inf,
        item[1].total_net,
    ))


def acceptance_gate(
    model: ValidationMetrics,
    baseline: ValidationMetrics,
    uncertainty: dict[str, float | int],
    double_cost: ValidationMetrics,
) -> tuple[bool, dict[str, bool]]:
    """Apply the same robustness standards used in final corn/wheat validation."""

    required_folds = ceil(0.60 * model.folds) if model.folds else 1
    required_years = ceil(0.60 * model.years) if model.years else 1
    checks = {
        "at least 30 development trades": model.trades >= 30,
        "positive development net": model.total_net > 0,
        "development Sharpe above 0.50": (
            np.isfinite(model.sharpe) and model.sharpe > 0.50
        ),
        "positive in at least 60% of folds": model.positive_folds >= required_folds,
        "positive in at least 60% of years": model.positive_years >= required_years,
        "beats best simple baseline net": model.total_net > baseline.total_net,
        "beats best simple baseline Sharpe": (
            np.isfinite(model.sharpe)
            and (not np.isfinite(baseline.sharpe) or model.sharpe > baseline.sharpe)
        ),
        "positive with doubled costs": double_cost.total_net > 0,
        "year-bootstrap probability positive at least 80%": (
            float(uncertainty["bootstrap_probability_positive"]) >= 0.80
        ),
        "circular-shift null p-value at most 10%": (
            float(uncertainty["permutation_p_value"]) <= 0.10
        ),
        "development drawdown no worse than -25%": model.max_drawdown > -0.25,
    }
    return all(checks.values()), checks


def strata(ledger: pd.DataFrame, *, sample: str) -> pd.DataFrame:
    """Aggregate fixed actions by year, cocoa season, and price regime."""

    if ledger.empty:
        return pd.DataFrame()
    work = ledger.copy()
    month = work["month"].astype(int)
    work["season"] = np.select(
        [month.isin([12, 1, 2]), month.isin([6, 7, 8, 9])],
        ["Harmattan Dec-Feb", "main-pod Jun-Sep"],
        default="shoulder Mar-May/Oct-Nov",
    )
    work["price_regime"] = np.where(
        work["momentum"] > 0, "positive 20-session momentum",
        np.where(work["momentum"] < 0, "negative 20-session momentum", "flat"),
    )
    work["direction"] = work["signal"].map({1: "BUY", -1: "SELL", 0: "HOLD"})
    dimensions = {
        "year": work["year"].astype(int).astype(str),
        "season": work["season"],
        "price_regime": work["price_regime"],
        "direction": work["direction"],
    }
    rows: list[dict[str, object]] = []
    for dimension, buckets in dimensions.items():
        for bucket, group in work.assign(bucket=buckets).groupby("bucket", sort=False):
            traded = group["signal"] != 0
            values = group["net"].to_numpy(dtype="float64")
            deviation = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
            rows.append({
                "sample": sample,
                "dimension": dimension,
                "bucket": str(bucket),
                "observations": len(group),
                "trades": int(traded.sum()),
                "hit_rate": (
                    float((group.loc[traded, "signal"]
                           * group.loc[traded, "gross"] > 0).mean())
                    if traded.any() else np.nan
                ),
                "total_net": float(values.sum()),
                "mean_net_per_trade": (
                    float(group.loc[traded, "net"].mean()) if traded.any() else np.nan
                ),
                "sharpe": (
                    float(np.sqrt(52) * values.mean() / deviation)
                    if deviation > 0 else np.nan
                ),
            })
    return pd.DataFrame(rows)
