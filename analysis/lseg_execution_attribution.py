"""Attribute frozen v1 performance changes without tuning the model.

The ordered counterfactuals change one measurement or execution assumption at a
time: continuous to dated settlement, close to next open, overlapping to the
frozen no-overlap policy, and finally fixed 10-bps costs to contract costs.  The
study is diagnostic and may not be used to re-select v1 on consumed history.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from lseg_signal_replay import frozen_predictions  # noqa: E402
from pipeline.futures_execution import ExecutionConfig, load_execution_config  # noqa: E402
from pipeline.lseg_replay import replay_frozen_signals  # noqa: E402
from pipeline.signal_eval import WEEKLY_PERIODS  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402


DEFAULT_REPORT = ROOT / "analysis" / "lseg_execution_attribution_report.md"
DEFAULT_WATERFALL = ROOT / "analysis" / "lseg_execution_attribution_waterfall.csv"
DEFAULT_STRATA = ROOT / "analysis" / "lseg_execution_attribution_strata.csv"
DEFAULT_UNCERTAINTY = ROOT / "analysis" / "lseg_execution_attribution_uncertainty.csv"
STAGE_LABELS = {
    "yahoo_close_fixed_cost": "Yahoo continuous close, fixed 10 bps",
    "lseg_settlement_fixed_cost": "LSEG dated settlement, fixed 10 bps",
    "lseg_next_open_overlap_fixed_cost": "LSEG next open, overlap allowed, fixed 10 bps",
    "lseg_next_open_no_overlap_fixed_cost": "LSEG next open, no overlap, fixed 10 bps",
    "lseg_next_open_no_overlap_contract_cost": "LSEG next open, no overlap, contract costs",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sharpe(values: pd.Series) -> float:
    numbers = values.to_numpy(dtype="float64")
    deviation = float(np.std(numbers, ddof=1)) if len(numbers) > 1 else 0.0
    return (
        float(np.sqrt(WEEKLY_PERIODS) * np.mean(numbers) / deviation)
        if deviation > 0 else float("nan")
    )


def _metric_row(
    *,
    sample: str,
    stage: str,
    values: pd.Series,
    gross_values: pd.Series,
    traded: pd.Series,
) -> dict[str, object]:
    equity = (1.0 + values.astype(float)).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    trade_values = values.loc[traded]
    return {
        "sample": sample,
        "stage": stage,
        "observations": len(values),
        "trades": int(traded.sum()),
        "gross_hit_rate": float((gross_values.loc[traded] > 0).mean()) if traded.any() else np.nan,
        "mean_net_per_trade": float(trade_values.mean()) if traded.any() else np.nan,
        "total_net": float(values.sum()),
        "compounded_net": float(equity.iloc[-1] - 1.0),
        "sharpe": _sharpe(values),
        "max_drawdown": float(drawdown.min()),
    }


def build_waterfall(
    predictions: pd.DataFrame,
    overlap: pd.DataFrame,
    no_overlap: pd.DataFrame,
    base: pd.DataFrame,
) -> pd.DataFrame:
    """Create ordered one-change-at-a-time aggregate counterfactuals."""

    rows: list[dict[str, object]] = []
    for sample in predictions["sample"].drop_duplicates():
        pred = predictions[predictions["sample"] == sample].reset_index(drop=True)
        over = overlap[overlap["sample"] == sample].reset_index(drop=True)
        no = no_overlap[no_overlap["sample"] == sample].reset_index(drop=True)
        actual = base[base["sample"] == sample].reset_index(drop=True)
        fixed_cost = pred["cost_rate"].astype(float)
        original_signal = pred["signal"].astype(int)
        no_overlap_signal = no["execution_signal"].astype(int)

        stages = [
            (
                "yahoo_close_fixed_cost",
                pred["net"].astype(float),
                original_signal * pred["actual"].astype(float),
                original_signal != 0,
            ),
            (
                "lseg_settlement_fixed_cost",
                original_signal * actual["lseg_close_spread_return"].astype(float)
                - original_signal.abs() * fixed_cost,
                original_signal * actual["lseg_close_spread_return"].astype(float),
                original_signal != 0,
            ),
            (
                "lseg_next_open_overlap_fixed_cost",
                original_signal * over["execution_unsigned_gross_return"].astype(float)
                - original_signal.abs() * fixed_cost,
                original_signal * over["execution_unsigned_gross_return"].astype(float),
                original_signal != 0,
            ),
            (
                "lseg_next_open_no_overlap_fixed_cost",
                no_overlap_signal * no["execution_unsigned_gross_return"].astype(float)
                - no_overlap_signal.abs() * fixed_cost,
                no_overlap_signal * no["execution_unsigned_gross_return"].astype(float),
                no_overlap_signal != 0,
            ),
            (
                "lseg_next_open_no_overlap_contract_cost",
                actual["execution_net_return"].astype(float),
                actual["execution_gross_return"].astype(float),
                actual["execution_signal"].astype(int) != 0,
            ),
        ]
        previous: float | None = None
        for stage, values, gross, traded in stages:
            row = _metric_row(
                sample=sample,
                stage=stage,
                values=values,
                gross_values=gross,
                traded=traded,
            )
            row["incremental_total_net_change"] = (
                np.nan if previous is None else float(row["total_net"]) - previous
            )
            previous = float(row["total_net"])
            rows.append(row)
    return pd.DataFrame(rows)


def _confidence_quartiles(frame: pd.DataFrame) -> pd.Series:
    output = pd.Series(index=frame.index, dtype="object")
    labels = ["Q1 lowest", "Q2", "Q3", "Q4 highest"]
    for _, group in frame.groupby("sample", sort=False):
        distance = group["score"].abs() - group["threshold"]
        output.loc[group.index] = pd.qcut(
            distance.rank(method="first"), 4, labels=labels
        ).astype(str)
    return output


def _stratum_rows(frame: pd.DataFrame, dimension: str, bucket: pd.Series) -> list[dict[str, object]]:
    rows = []
    work = frame.assign(_bucket=bucket.astype(str))
    for (sample, value), group in work.groupby(["sample", "_bucket"], sort=False):
        total_contracts = group["corn_contracts"] + group["wheat_contracts"]
        contract_sides = 2 * total_contracts
        breakeven = group["execution_gross_pnl_usd"] / contract_sides
        actual_cost = (
            group["execution_commission_usd"] + group["execution_slippage_usd"]
        ) / contract_sides
        rows.append({
            "sample": sample,
            "dimension": dimension,
            "bucket": value,
            "trades": len(group),
            "buy_trades": int((group["execution_signal"] == 1).sum()),
            "sell_trades": int((group["execution_signal"] == -1).sum()),
            "gross_hit_rate": float((group["execution_gross_return"] > 0).mean()),
            "mean_gross_per_trade": float(group["execution_gross_return"].mean()),
            "mean_net_per_trade": float(group["execution_net_return"].mean()),
            "total_gross": float(group["execution_gross_return"].sum()),
            "total_net": float(group["execution_net_return"].sum()),
            "median_breakeven_usd_per_contract_side": float(breakeven.median()),
            "share_covering_base_cost": float((breakeven > actual_cost).mean()),
            "median_actual_cost_usd_per_contract_side": float(actual_cost.median()),
        })
    return rows


def build_strata(base: pd.DataFrame) -> pd.DataFrame:
    """Aggregate only executed trades; no row-level licensed data are emitted."""

    frame = base[base["execution_status"] == "EXECUTED"].copy()
    frame["direction"] = frame["execution_signal"].map({1: "BUY", -1: "SELL"})
    frame["season"] = pd.cut(
        pd.to_datetime(frame["date"]).dt.month,
        bins=[3, 5, 7, 10],
        labels=["early Apr-May", "mid Jun-Jul", "late Aug-Oct"],
    ).astype(str)
    frame["roll_selection"] = np.where(
        frame["corn_rolled_forward"] | frame["wheat_rolled_forward"],
        "rolled beyond nearest",
        "nearest contracts",
    )
    frame["confidence_quartile"] = _confidence_quartiles(frame)
    dimensions = {
        "overall": pd.Series("all", index=frame.index),
        "year": frame["year"].astype(int).astype(str),
        "direction": frame["direction"],
        "season": frame["season"],
        "confidence": frame["confidence_quartile"],
        "roll_selection": frame["roll_selection"],
    }
    rows: list[dict[str, object]] = []
    for dimension, buckets in dimensions.items():
        rows.extend(_stratum_rows(frame, dimension, buckets))
    return pd.DataFrame(rows)


def build_uncertainty(base: pd.DataFrame, *, draws: int = 10_000) -> pd.DataFrame:
    """Year-block bootstrap the frozen base replay; this is uncertainty, not tuning."""

    rows = []
    for offset, (sample, group) in enumerate(base.groupby("sample", sort=False)):
        blocks = [
            year_group["execution_net_return"].to_numpy(dtype="float64")
            for _, year_group in group.groupby(pd.to_datetime(group["date"]).dt.year)
        ]
        rng = np.random.default_rng(590 + offset)
        totals = np.empty(draws)
        for draw in range(draws):
            chosen = rng.integers(0, len(blocks), size=len(blocks))
            totals[draw] = sum(float(blocks[index].sum()) for index in chosen)
        rows.append({
            "sample": sample,
            "year_blocks": len(blocks),
            "bootstrap_draws": draws,
            "observed_total_net": float(group["execution_net_return"].sum()),
            "bootstrap_total_net_p025": float(np.quantile(totals, 0.025)),
            "bootstrap_total_net_median": float(np.median(totals)),
            "bootstrap_total_net_p975": float(np.quantile(totals, 0.975)),
            "bootstrap_probability_positive": float((totals > 0).mean()),
        })
    return pd.DataFrame(rows)


def _percent(value: object, digits: int = 1) -> str:
    number = float(value)
    return "—" if not np.isfinite(number) else f"{number:.{digits}%}"


def _number(value: object, digits: int = 2) -> str:
    number = float(value)
    return "—" if not np.isfinite(number) else f"{number:+.{digits}f}"


def render_report(
    waterfall: pd.DataFrame,
    strata: pd.DataFrame,
    uncertainty: pd.DataFrame,
    base: pd.DataFrame,
    *,
    contracts_path: Path,
) -> str:
    lines = [
        "# Frozen v1 LSEG Execution Attribution",
        "",
        "## Decision",
        "",
        "**The dated-contract target is valid, but the frozen signal does not show a",
        "stable edge after its operational policy and contract costs are applied.**",
        "",
        "This is a post-freeze diagnostic. No feature, fitted model, threshold, action,",
        "holding period, or production rule is selected from these results.",
        "",
        "## One-change-at-a-time waterfall",
        "",
        "| Sample | Stage | Trades | Hit | Net | Change | Sharpe | Drawdown |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in waterfall.itertuples(index=False):
        lines.append(
            f"| {row.sample} | {STAGE_LABELS[row.stage]} | {row.trades} | "
            f"{_percent(row.gross_hit_rate)} | {_percent(row.total_net)} | "
            f"{_percent(row.incremental_total_net_change)} | {_number(row.sharpe)} | "
            f"{_percent(row.max_drawdown)} |"
        )
    lines.extend([
        "",
        "Net and Change are arithmetic sums of per-decision returns, matching the",
        "original benchmark. The first stage has no incremental change by definition.",
        "The 10-bps next-open counterfactual is a measurement bridge, not a claim that",
        "futures commissions are proportional to notional.",
        "",
        "## Cost capacity and strata",
        "",
    ])
    selected = strata[
        strata["dimension"].isin(["overall", "direction", "confidence", "roll_selection"])
    ]
    lines.extend([
        "| Sample | Dimension | Bucket | Trades | Gross hit | Gross | Net | "
        "Median break-even/side | Covers base cost |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for row in selected.itertuples(index=False):
        lines.append(
            f"| {row.sample} | {row.dimension} | {row.bucket} | {row.trades} | "
            f"{_percent(row.gross_hit_rate)} | {_percent(row.total_gross)} | "
            f"{_percent(row.total_net)} | "
            f"${row.median_breakeven_usd_per_contract_side:,.2f} | "
            f"{_percent(row.share_covering_base_cost)} |"
        )
    lines.extend([
        "",
        "Break-even is the signed gross P&L divided by round-trip contract sides.",
        "The base all-in assumption is $15 per contract side: $2.50 commission plus",
        "one $12.50 tick. A negative break-even means the trade lost before costs.",
        "Year and seasonal aggregates are retained in the machine-readable strata file.",
        "",
        "## Year-block uncertainty",
        "",
        "| Sample | Years | Observed net | 95% block interval | Probability positive |",
        "|---|---:|---:|---:|---:|",
    ])
    for row in uncertainty.itertuples(index=False):
        interval = (
            f"{_percent(row.bootstrap_total_net_p025)} to "
            f"{_percent(row.bootstrap_total_net_p975)}"
        )
        lines.append(
            f"| {row.sample} | {row.year_blocks} | {_percent(row.observed_total_net)} | "
            f"{interval} | {_percent(row.bootstrap_probability_positive)} |"
        )
    executed = base[base["execution_status"] == "EXECUTED"]
    lines.extend([
        "",
        "The block bootstrap resamples calendar-year clusters and quantifies how fragile",
        "the observed path is. It does not repair the consumed holdout or create a new",
        "independent test.",
        "The holdout's 100% positive bootstrap frequency is mechanical: each of its only",
        "three observed calendar-year blocks is positive, so their resamples are too.",
        "",
        "## Audit boundary",
        "",
        f"- Decisions: {len(base)}; original signals: {int((base['signal'] != 0).sum())}; "
        f"executed trades: {len(executed)}.",
        f"- Suppressed overlaps: {int((base['execution_status'] == 'SKIPPED_OVERLAP').sum())}.",
        f"- Median notional imbalance: {base['notional_imbalance_fraction'].median():.2%}.",
        f"- Contract panel SHA-256: `{_sha256(contracts_path)}`.",
        "- Row-level LSEG-derived ledgers remain ignored under `data/analysis/`.",
        "- This attribution remains normalized by average leg notional; the separate",
        "  dated-margin study reports return on capital for the public CME window.",
        "",
        "## Next evidence",
        "",
        "Freeze v1 and log decisions before outcomes exist. The prospective ledger—not",
        "another retrospective threshold or model search—is the next valid effectiveness",
        "test. Any v2 specification should be pre-registered and evaluated on a later,",
        "untouched growing season.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    store = ProcessedStore(default_data_root())
    contracts_path = default_data_root() / "processed" / "futures_contracts.parquet"
    if not contracts_path.exists():
        raise SystemExit(f"contract panel not found: {contracts_path}")
    predictions, _ = frozen_predictions(store)
    contracts = pd.read_parquet(contracts_path, engine="pyarrow")
    loaded = load_execution_config()
    overlap_config = ExecutionConfig(
        contracts=loaded.contracts,
        assumptions=replace(
            loaded.assumptions,
            allow_overlapping_positions=True,
            commission_usd_per_contract_side=0.0,
            slippage_ticks_per_contract_side=0.0,
        ),
    )
    no_overlap_zero_config = ExecutionConfig(
        contracts=loaded.contracts,
        assumptions=replace(
            loaded.assumptions,
            allow_overlapping_positions=False,
            commission_usd_per_contract_side=0.0,
            slippage_ticks_per_contract_side=0.0,
        ),
    )
    overlap = replay_frozen_signals(predictions, contracts, config=overlap_config)
    no_overlap = replay_frozen_signals(predictions, contracts, config=no_overlap_zero_config)
    base = replay_frozen_signals(predictions, contracts, config=loaded)
    waterfall = build_waterfall(predictions, overlap, no_overlap, base)
    strata = build_strata(base)
    uncertainty = build_uncertainty(base)
    report = render_report(
        waterfall, strata, uncertainty, base, contracts_path=contracts_path
    )
    for path in (DEFAULT_REPORT, DEFAULT_WATERFALL, DEFAULT_STRATA, DEFAULT_UNCERTAINTY):
        path.parent.mkdir(parents=True, exist_ok=True)
    waterfall.to_csv(DEFAULT_WATERFALL, index=False, float_format="%.8f")
    strata.to_csv(DEFAULT_STRATA, index=False, float_format="%.8f")
    uncertainty.to_csv(DEFAULT_UNCERTAINTY, index=False, float_format="%.8f")
    DEFAULT_REPORT.write_text(report, encoding="utf-8")
    print(report)
    print(f"waterfall: {DEFAULT_WATERFALL}")
    print(f"strata: {DEFAULT_STRATA}")
    print(f"uncertainty: {DEFAULT_UNCERTAINTY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
