"""Validate the frozen v1 signal against dated LSEG futures contracts.

The model, features, nested folds, hyperparameter search, and action thresholds
remain unchanged.  Only the realized target and execution measurement change.
Aggregate outputs are suitable for the repository; the row-level licensed
ledger is written beneath ignored ``data/analysis``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

# Avoid joblib's macOS physical-core probe warning; the model is deterministic
# and one logical worker is sufficient for this small evaluation.
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.config import load_geography  # noqa: E402
from pipeline.futures_execution import load_execution_config  # noqa: E402
from pipeline.lseg_replay import replay_frozen_signals  # noqa: E402
from pipeline.signal_eval import WEEKLY_PERIODS, evaluate  # noqa: E402
from pipeline.signals import FEATURE_SETS, TARGETS, build  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402


MODEL_VERSION = "corn-wheat-all-public-boosting-v1"
FEATURE_SET = "all_public"
MODEL_NAME = "boosting"
HORIZON = 5
ROLL_WINDOWS = (5, 10, 15)
COST_SCENARIOS = {
    "zero": (0.0, 0.0),
    "base": (2.50, 1.0),
    "stress": (5.00, 2.0),
}

DEFAULT_REPORT = ROOT / "analysis" / "lseg_signal_replay_report.md"
DEFAULT_PERFORMANCE = ROOT / "analysis" / "lseg_signal_replay_performance.csv"
DEFAULT_AGREEMENT = ROOT / "analysis" / "lseg_signal_replay_agreement.csv"
DEFAULT_SENSITIVITY = ROOT / "analysis" / "lseg_signal_replay_sensitivity.csv"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frozen_predictions(store: ProcessedStore) -> tuple[pd.DataFrame, object]:
    frames, available = build(store, load_geography(), horizons=(HORIZON,))
    features = FEATURE_SETS[FEATURE_SET]
    missing = sorted(set(features) - set(available[FEATURE_SET]))
    if missing:
        raise ValueError("frozen features are unavailable: " + ", ".join(missing))
    target = next(item for item in TARGETS if item.name == "corn_wheat")
    result = evaluate(
        frames[HORIZON],
        features=features,
        feature_set=FEATURE_SET,
        target=target,
        horizon=HORIZON,
        model_name=MODEL_NAME,
    )
    development = result.predictions.assign(sample="development")
    holdout = result.holdout_predictions.assign(sample="holdout")
    predictions = pd.concat([development, holdout], ignore_index=True)
    target_dates = frames[HORIZON][
        ["date", f"target_end_date_{HORIZON}"]
    ].rename(columns={f"target_end_date_{HORIZON}": "target_end_date"})
    predictions = predictions.merge(
        target_dates, on="date", how="left", validate="one_to_one"
    )
    return predictions.sort_values("date").reset_index(drop=True), result


def _sharpe(values: pd.Series) -> float:
    numbers = values.to_numpy(dtype="float64")
    deviation = float(np.std(numbers, ddof=1)) if len(numbers) > 1 else 0.0
    return (
        float(np.sqrt(WEEKLY_PERIODS) * np.mean(numbers) / deviation)
        if deviation > 0 else float("nan")
    )


def _performance_row(
    group: pd.DataFrame,
    *,
    sample: str,
    method: str,
    usable: pd.Series,
    target_column: str,
    return_values: pd.Series,
    signal_column: str = "signal",
) -> dict[str, object]:
    subset = group.loc[usable].copy()
    subset["evaluated_return"] = return_values.loc[usable].astype(float)
    traded = subset[signal_column].astype(int) != 0
    trade_returns = subset.loc[traded, "evaluated_return"]
    equity = (1.0 + subset["evaluated_return"]).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    hit_rate = (
        float((
            np.sign(subset.loc[traded, target_column].astype(float))
            == subset.loc[traded, signal_column].astype(int)
        ).mean())
        if traded.any() else float("nan")
    )
    signaled = int((group["signal"].astype(int) != 0).sum())
    scored = int(traded.sum())
    return {
        "sample": sample,
        "method": method,
        "observations": len(subset),
        "signals": signaled,
        "trades_scored": scored,
        "skipped_signals": signaled - scored,
        "hit_rate": hit_rate,
        "mean_net_per_trade": float(trade_returns.mean()) if scored else float("nan"),
        "total_net": float(subset["evaluated_return"].sum()),
        "compounded_net": float(equity.iloc[-1] - 1.0) if len(equity) else 0.0,
        "sharpe": _sharpe(subset["evaluated_return"]),
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
    }


def performance_table(ledger: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for sample, group in ledger.groupby("sample", sort=False):
        available = pd.Series(True, index=group.index)
        rows.append(_performance_row(
            group,
            sample=sample,
            method="yahoo_continuous_close",
            usable=available,
            target_column="actual",
            return_values=group["net"],
        ))
        close_ok = group["close_status"] == "OK"
        close_net = (
            group["signal"].astype(float) * group["lseg_close_spread_return"].astype(float)
            - group["signal"].abs().astype(float) * group["cost_rate"].astype(float)
        )
        rows.append(_performance_row(
            group,
            sample=sample,
            method="lseg_dated_settlement",
            usable=close_ok,
            target_column="lseg_close_spread_return",
            return_values=close_net,
        ))
        execution_ok = group["execution_unsigned_gross_return"].notna()
        rows.append(_performance_row(
            group,
            sample=sample,
            method="lseg_next_open",
            usable=execution_ok,
            target_column="execution_unsigned_gross_return",
            return_values=group["execution_net_return"],
            signal_column="execution_signal",
        ))
    return pd.DataFrame(rows)


def agreement_table(ledger: pd.DataFrame) -> pd.DataFrame:
    rows = []
    samples = [("all", ledger), *list(ledger.groupby("sample", sort=False))]
    for sample, group in samples:
        close = group[group["close_status"] == "OK"].dropna(
            subset=["actual", "lseg_close_spread_return"]
        )
        execution = group[group["execution_unsigned_gross_return"].notna()].dropna(
            subset=["actual", "execution_unsigned_gross_return"]
        )
        signaled_close = close[close["signal"] != 0]
        signaled_execution = execution[execution["signal"] != 0]
        rows.append({
            "sample": sample,
            "observations": len(close),
            "yahoo_lseg_close_correlation": close["actual"].corr(
                close["lseg_close_spread_return"]
            ),
            "yahoo_lseg_close_median_absolute_difference": (
                close["actual"] - close["lseg_close_spread_return"]
            ).abs().median(),
            "yahoo_lseg_close_sign_agreement": (
                np.sign(close["actual"])
                == np.sign(close["lseg_close_spread_return"])
            ).mean(),
            "signal_close_sign_agreement": (
                np.sign(signaled_close["actual"])
                == np.sign(signaled_close["lseg_close_spread_return"])
            ).mean(),
            "yahoo_next_open_correlation": execution["actual"].corr(
                execution["execution_unsigned_gross_return"]
            ),
            "signal_next_open_sign_agreement": (
                np.sign(signaled_execution["actual"])
                == np.sign(signaled_execution["execution_unsigned_gross_return"])
            ).mean(),
        })
    return pd.DataFrame(rows)


def sensitivity_table(
    predictions: pd.DataFrame,
    contracts: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for roll_days in ROLL_WINDOWS:
        for scenario, (commission, slippage) in COST_SCENARIOS.items():
            ledger = replay_frozen_signals(
                predictions,
                contracts,
                roll_business_days=roll_days,
                commission_usd_per_contract_side=commission,
                slippage_ticks_per_contract_side=slippage,
            )
            table = performance_table(ledger)
            execution = table[table["method"] == "lseg_next_open"].copy()
            execution.insert(0, "cost_scenario", scenario)
            execution.insert(0, "roll_business_days", roll_days)
            rows.append(execution)
    return pd.concat(rows, ignore_index=True)


def _percent(value: object, digits: int = 1) -> str:
    number = float(value)
    return "—" if not np.isfinite(number) else f"{number:.{digits}%}"


def _number(value: object, digits: int = 2) -> str:
    number = float(value)
    return "—" if not np.isfinite(number) else f"{number:+.{digits}f}"


def _performance_markdown(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| Sample | Outcome | Obs. | Trades | Hit | Net | Sharpe | Max drawdown |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "yahoo_continuous_close": "Yahoo continuous close",
        "lseg_dated_settlement": "LSEG dated settlement",
        "lseg_next_open": "LSEG next-open execution",
    }
    for row in frame.itertuples(index=False):
        lines.append(
            f"| {row.sample} | {labels[row.method]} | {row.observations} | "
            f"{row.trades_scored} | {_percent(row.hit_rate)} | "
            f"{_percent(row.total_net)} | {_number(row.sharpe)} | "
            f"{_percent(row.max_drawdown)} |"
        )
    return lines


def _agreement_markdown(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| Sample | Obs. | Close corr. | Close sign | Signal close sign | "
        "Next-open corr. | Signal next-open sign |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in frame.itertuples(index=False):
        lines.append(
            f"| {row.sample} | {row.observations} | "
            f"{_number(row.yahoo_lseg_close_correlation, 3)} | "
            f"{_percent(row.yahoo_lseg_close_sign_agreement)} | "
            f"{_percent(row.signal_close_sign_agreement)} | "
            f"{_number(row.yahoo_next_open_correlation, 3)} | "
            f"{_percent(row.signal_next_open_sign_agreement)} |"
        )
    return lines


def _sensitivity_markdown(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| Roll days | Costs | Sample | Trades | Net | Sharpe | Drawdown |",
        "|---:|---|---|---:|---:|---:|---:|",
    ]
    for row in frame.itertuples(index=False):
        lines.append(
            f"| {row.roll_business_days} | {row.cost_scenario} | {row.sample} | "
            f"{row.trades_scored} | {_percent(row.total_net)} | "
            f"{_number(row.sharpe)} | {_percent(row.max_drawdown)} |"
        )
    return lines


def render_report(
    performance: pd.DataFrame,
    agreement: pd.DataFrame,
    sensitivity: pd.DataFrame,
    ledger: pd.DataFrame,
    *,
    contracts_path: Path,
    yahoo_path: Path,
) -> str:
    base = performance[
        (performance["method"] == "lseg_next_open")
    ].set_index("sample")
    dev_positive = float(base.loc["development", "total_net"]) > 0
    holdout_positive = float(base.loc["holdout", "total_net"]) > 0
    if dev_positive and holdout_positive:
        decision = (
            "The frozen v1 signal remains profitable in both the development replay "
            "and the consumed holdout under the base LSEG next-open assumptions."
        )
    elif dev_positive or holdout_positive:
        decision = (
            "The frozen v1 signal survives in only one evaluation segment under the "
            "base LSEG next-open assumptions; effectiveness is not stable."
        )
    else:
        decision = (
            "The frozen v1 signal does not remain profitable under the base LSEG "
            "next-open assumptions."
        )
    close_missing = int((ledger["close_status"] != "OK").sum())
    execution_missing = int(ledger["execution_unsigned_gross_return"].isna().sum())
    median_imbalance = float(ledger["notional_imbalance_fraction"].median())
    lines = [
        "# Frozen v1 LSEG Contract Replay",
        "",
        "## Preliminary decision",
        "",
        f"**{decision}**",
        "",
        "This is an execution and data-source validation of the already selected",
        f"`{MODEL_VERSION}` specification. The model family, 32 features, five-session",
        "target, nested folds, fitted thresholds, and actions are unchanged. The",
        "2024–2026 holdout is already consumed; replacing Yahoo outcomes with LSEG",
        "contracts does not create a new independent holdout.",
        "",
        "## Base comparison",
        "",
        *_performance_markdown(performance),
        "",
        "Yahoo rows reproduce the original baseline. LSEG dated settlement keeps the",
        "same decision and outcome dates and charges the original 10 bps two-leg cost.",
        "LSEG next-open execution enters on the next shared session, exits after five",
        "further shared sessions, sizes an integer approximately dollar-neutral spread,",
        "and charges $2.50 commission plus one tick of slippage per contract side.",
        "Returns use average leg notional, not unavailable historical margin.",
        "Net is the arithmetic sum of per-decision returns, matching the baseline;",
        "the aggregate CSV also reports the compounded path.",
        "",
        "## Target agreement",
        "",
        *_agreement_markdown(agreement),
        "",
        "Close agreement isolates the effect of replacing the continuous series with",
        "dated contracts. Next-open agreement also includes the deliberately conservative",
        "one-session timing shift and integer contract selection.",
        "Signal-sign columns include every frozen non-HOLD decision; performance rows",
        "separately apply the configured no-overlap execution policy.",
        "",
        "## Predeclared sensitivity",
        "",
        "Cost cases are zero; base ($2.50 plus one tick per contract side); and stress",
        "($5 plus two ticks). No row in this grid changes the model or its threshold.",
        "",
        *_sensitivity_markdown(sensitivity),
        "",
        "## Coverage and audit",
        "",
        f"- Frozen out-of-sample decisions: {len(ledger)} "
        f"({int((ledger['sample'] == 'development').sum())} development; "
        f"{int((ledger['sample'] == 'holdout').sum())} consumed holdout).",
        f"- Signals: {int((ledger['signal'] != 0).sum())}; signals suppressed by the "
        f"no-overlap policy: {int((ledger['execution_status'] == 'SKIPPED_OVERLAP').sum())}.",
        f"- Unavailable dated-settlement outcomes: {close_missing}; unavailable next-open outcomes: {execution_missing}.",
        f"- Median entry notional imbalance: {median_imbalance:.2%}.",
        f"- Contract panel SHA-256: `{_sha256(contracts_path)}`.",
        f"- Yahoo price panel SHA-256: `{_sha256(yahoo_path)}`.",
        "- The detailed licensed ledger remains beneath ignored `data/analysis/` and",
        "  must not be committed or redistributed as row-level LSEG-derived data.",
        "",
        "## Interpretation",
        "",
        "This replay can falsify the hypothesis that v1's apparent edge is purely a",
        "Yahoo continuous-contract or roll artifact. It cannot establish a fresh",
        "out-of-sample edge because the dates and underlying market outcomes are the",
        "same history already reviewed during model selection. Dated maintenance margin",
        "is still required before reporting return on capital or enforcing a capital",
        "budget. The next honest evidence after this study is prospective paper trading",
        "on decisions logged before their outcomes are known.",
        "",
    ]
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument(
        "--contracts", type=Path,
        default=default_data_root() / "processed" / "futures_contracts.parquet",
    )
    result.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    result.add_argument("--performance", type=Path, default=DEFAULT_PERFORMANCE)
    result.add_argument("--agreement", type=Path, default=DEFAULT_AGREEMENT)
    result.add_argument("--sensitivity", type=Path, default=DEFAULT_SENSITIVITY)
    result.add_argument(
        "--ledger", type=Path,
        default=default_data_root() / "analysis" / "lseg_signal_replay_ledger.parquet",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.contracts.exists():
        raise SystemExit(f"contract panel not found: {args.contracts}")
    store = ProcessedStore(default_data_root())
    predictions, _ = frozen_predictions(store)
    contracts = pd.read_parquet(args.contracts, engine="pyarrow")
    config = load_execution_config()
    base_ledger = replay_frozen_signals(predictions, contracts, config=config)
    performance = performance_table(base_ledger)
    agreement = agreement_table(base_ledger)
    sensitivity = sensitivity_table(predictions, contracts)

    for path in (args.report, args.performance, args.agreement, args.sensitivity, args.ledger):
        path.parent.mkdir(parents=True, exist_ok=True)
    performance.to_csv(args.performance, index=False, float_format="%.8f")
    agreement.to_csv(args.agreement, index=False, float_format="%.8f")
    sensitivity.to_csv(args.sensitivity, index=False, float_format="%.8f")
    base_ledger.to_parquet(args.ledger, index=False, engine="pyarrow")
    yahoo_path = store.path("yahoo_prices_daily")
    report = render_report(
        performance,
        agreement,
        sensitivity,
        base_ledger,
        contracts_path=args.contracts,
        yahoo_path=yahoo_path,
    )
    args.report.write_text(report, encoding="utf-8")
    print(report)
    print(f"performance: {args.performance}")
    print(f"agreement: {args.agreement}")
    print(f"sensitivity: {args.sensitivity}")
    print(f"licensed ledger (ignored): {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
