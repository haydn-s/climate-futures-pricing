"""Run the frozen signal through dated CME margins and a realized-capital account."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from lseg_signal_replay import frozen_predictions  # noqa: E402
from pipeline.futures_execution import (  # noqa: E402
    load_execution_config,
    simulate_spread_trades,
)
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402


DEFAULT_CONTRACTS = (
    default_data_root() / "processed" / "futures_contracts_with_margin.parquet"
)
DEFAULT_REPORT = ROOT / "analysis" / "lseg_capital_simulation_report.md"
DEFAULT_RESULTS = ROOT / "analysis" / "lseg_capital_simulation_results.csv"
DEFAULT_MARGIN_SUMMARY = ROOT / "analysis" / "cme_margin_history_summary.csv"
DEFAULT_LEDGER = default_data_root() / "analysis" / "lseg_capital_simulation_ledger.parquet"
CAPITAL_SCENARIOS = (10_000.0, 25_000.0, 50_000.0, 100_000.0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _action(signal: int) -> str:
    return {-1: "SELL", 0: "HOLD", 1: "BUY"}[int(signal)]


def summarize_margin_history(contracts: pd.DataFrame) -> pd.DataFrame:
    rates = contracts[[
        "margin_business_date", "root", "margin_roll_index",
        "maintenance_margin_usd",
    ]].drop_duplicates()
    rates["year"] = pd.to_datetime(rates["margin_business_date"]).dt.year
    return (
        rates.groupby(["root", "year"], as_index=False)
        .agg(
            business_dates=("margin_business_date", "nunique"),
            roll_rates=("maintenance_margin_usd", "size"),
            minimum_margin_usd=("maintenance_margin_usd", "min"),
            median_margin_usd=("maintenance_margin_usd", "median"),
            maximum_margin_usd=("maintenance_margin_usd", "max"),
        )
    )


def _max_drawdown(ledger: pd.DataFrame, initial_capital: float) -> float:
    equity = initial_capital + ledger.get(
        "net_pnl_usd", pd.Series(0.0, index=ledger.index)
    ).fillna(0.0).cumsum()
    path = pd.concat([pd.Series([initial_capital]), equity], ignore_index=True)
    return float((path / path.cummax() - 1.0).min())


def scenario_row(
    ledger: pd.DataFrame,
    *,
    sample: str,
    initial_capital: float,
) -> dict[str, object]:
    status = ledger["status"].value_counts()
    executed = ledger[ledger["status"] == "EXECUTED"]
    net = float(executed["net_pnl_usd"].sum()) if len(executed) else 0.0
    return {
        "sample": sample,
        "initial_capital_usd": initial_capital,
        "decision_start": ledger["decision_date"].min(),
        "decision_end": ledger["decision_date"].max(),
        "decisions": len(ledger),
        "signals": int((ledger["action"] != "HOLD").sum()),
        "executed_trades": len(executed),
        "skipped_overlap": int(status.get("SKIPPED_OVERLAP", 0)),
        "skipped_margin": int(status.get("SKIPPED_MARGIN", 0)),
        "skipped_no_exit": int(status.get("SKIPPED_NO_EXIT", 0)),
        "gross_hit_rate": (
            float((executed["gross_pnl_usd"] > 0).mean()) if len(executed) else np.nan
        ),
        "net_pnl_usd": net,
        "ending_capital_usd": initial_capital + net,
        "return_on_initial_capital": net / initial_capital,
        "max_drawdown": _max_drawdown(ledger, initial_capital),
        "median_posted_margin_usd": (
            float(executed["maintenance_margin_usd"].median())
            if len(executed) else np.nan
        ),
        "maximum_margin_utilization": (
            float(executed["margin_utilization"].max()) if len(executed) else np.nan
        ),
        "median_notional_imbalance": (
            float(executed["notional_imbalance_fraction"].median())
            if len(executed) else np.nan
        ),
    }


def run_scenarios(
    predictions: pd.DataFrame,
    contracts: pd.DataFrame,
    *,
    capital_scenarios: tuple[float, ...] = CAPITAL_SCENARIOS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = load_execution_config()
    start = pd.to_datetime(contracts["date"]).min()
    end = pd.to_datetime(contracts["date"]).max()
    covered = predictions[
        pd.to_datetime(predictions["date"]).between(start, end)
    ].copy()
    covered["decision_date"] = pd.to_datetime(covered["date"]).dt.normalize()
    covered["action"] = covered["signal"].map(_action)

    rows: list[dict[str, object]] = []
    ledgers: list[pd.DataFrame] = []
    for sample, sample_frame in covered.groupby("sample", sort=False):
        decisions = sample_frame[["decision_date", "action"]]
        for capital in capital_scenarios:
            ledger = simulate_spread_trades(
                decisions,
                contracts,
                config=config,
                capital_usd=capital,
            )
            ledger.insert(0, "sample", sample)
            ledger.insert(1, "initial_capital_usd", capital)
            ledgers.append(ledger)
            rows.append(scenario_row(
                ledger, sample=sample, initial_capital=capital
            ))
    return pd.DataFrame(rows), pd.concat(ledgers, ignore_index=True)


def _money(value: object) -> str:
    number = float(value)
    return "-" if not np.isfinite(number) else f"${number:,.0f}"


def _percent(value: object, digits: int = 1) -> str:
    number = float(value)
    return "-" if not np.isfinite(number) else f"{number:.{digits}%}"


def render_report(
    results: pd.DataFrame,
    margins: pd.DataFrame,
    contracts: pd.DataFrame,
    *,
    contracts_path: Path,
    manifest: dict[str, object],
) -> str:
    dates = pd.to_datetime(contracts["date"])
    best_development = results[results["sample"] == "development"].sort_values(
        "return_on_initial_capital", ascending=False
    ).iloc[0]
    lines = [
        "# Frozen v1 capital-constrained LSEG replay",
        "",
        "## Decision",
        "",
        "**Dated exchange margins make the replay executable, but they do not rescue",
        "the weak development evidence.** Capital changes position feasibility and",
        "the return denominator; it does not create a stable pre-cost edge.",
        "",
        "The least-negative development result in the fixed capital grid is "
        f"{_percent(best_development.return_on_initial_capital)} on "
        f"{_money(best_development.initial_capital_usd)} of starting capital. "
        "The holdout remains already consumed and is reported descriptively only.",
        "",
        "## Capital scenarios",
        "",
        "| Sample | Start capital | Signals | Executed | Margin skips | Net P&L | "
        "Return on capital | Max drawdown | Median margin | Max utilization |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results.itertuples(index=False):
        lines.append(
            f"| {row.sample} | {_money(row.initial_capital_usd)} | {row.signals} | "
            f"{row.executed_trades} | {row.skipped_margin} | {_money(row.net_pnl_usd)} | "
            f"{_percent(row.return_on_initial_capital)} | "
            f"{_percent(row.max_drawdown)} | {_money(row.median_posted_margin_usd)} | "
            f"{_percent(row.maximum_margin_utilization)} |"
        )
    lines.extend([
        "",
        "Each sample starts with fresh capital. Realized net P&L changes the budget",
        "available to the next non-overlapping trade. The simulator sums the two",
        "outright leg margins and applies no portfolio spread credit, a conservative",
        "choice. Costs remain $2.50 commission plus one $12.50 tick of slippage per",
        "contract side. The quantity search still favors the smallest integer pair",
        "with the lowest dollar-notional imbalance rather than scaling merely to use",
        "all available capital.",
        "Integer sizing makes P&L non-monotonic across account sizes; the grid is a",
        "capacity diagnostic, not a choice of an 'optimal' starting balance. Drawdown",
        "is measured on realized trade-close equity and excludes intratrade variation.",
        "",
        "## Margin data",
        "",
        f"- Official [CME historical-margin](https://www.cmegroup.com/solutions/"
        f"risk-management/margin-services/historical-margins.html) coverage used: "
        f"{dates.min().date()} through {dates.max().date()}.",
        f"- Official PDF rows parsed: {int(manifest['margin_rows']):,}; exact rates "
        f"matched to LSEG contracts: {len(contracts):,}.",
        f"- Observed outright maintenance range: "
        f"{_money(margins['minimum_margin_usd'].min())} to "
        f"{_money(margins['maximum_margin_usd'].max())} per contract.",
        "- CME's reports label the values `Minimum Performance Bond Requirements`;",
        "  this study uses that minimum account requirement as maintenance margin.",
        "- `C-01`, `W-01`, and subsequent roll codes are joined to each date's",
        "  expiry-ordered LSEG contract strip. All covered contract rows matched",
        "  exactly by business date and roll index; no current rate was backfilled.",
        "- The public PDFs currently begin June 30, 2020. Earlier frozen decisions",
        "  are excluded. Full 2015 coverage requires an authoritative older source;",
        "  the [CME DataMine agriculture margin CSV](https://datamine.new.cmegroup.com/catalog/dataset/77ccab5f5c224ab6811fad891b497276) is paid.",
        "",
        "## Audit boundary",
        "",
        f"- Margin-enriched contract panel SHA-256: `{_sha256(contracts_path)}`.",
        f"- Corn PDF SHA-256: `{manifest['margin_files'][0]['sha256']}`.",
        f"- Wheat PDF SHA-256: `{manifest['margin_files'][1]['sha256']}`.",
        "- Row-level LSEG-derived panels and capital ledgers remain ignored under `data/`.",
        "- The committed results are aggregates; they do not redistribute licensed prices.",
        "- This is a post-freeze execution study, not a new model selection round.",
        "- Exchange minimums do not include a broker's possible margin add-ons.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    if not DEFAULT_CONTRACTS.exists():
        raise SystemExit(
            f"margin-enriched contract panel not found: {DEFAULT_CONTRACTS}"
        )
    store = ProcessedStore(default_data_root())
    predictions, _ = frozen_predictions(store)
    contracts = pd.read_parquet(DEFAULT_CONTRACTS, engine="pyarrow")
    manifest_path = DEFAULT_CONTRACTS.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results, ledger = run_scenarios(predictions, contracts)
    margin_summary = summarize_margin_history(contracts)
    report = render_report(
        results,
        margin_summary,
        contracts,
        contracts_path=DEFAULT_CONTRACTS,
        manifest=manifest,
    )
    for path in (DEFAULT_REPORT, DEFAULT_RESULTS, DEFAULT_MARGIN_SUMMARY, DEFAULT_LEDGER):
        path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(DEFAULT_RESULTS, index=False, float_format="%.8f")
    margin_summary.to_csv(DEFAULT_MARGIN_SUMMARY, index=False, float_format="%.2f")
    ledger.to_parquet(DEFAULT_LEDGER, index=False, engine="pyarrow")
    DEFAULT_REPORT.write_text(report, encoding="utf-8")
    print(report)
    print(f"results: {DEFAULT_RESULTS}")
    print(f"margin summary: {DEFAULT_MARGIN_SUMMARY}")
    print(f"row-level ledger (ignored): {DEFAULT_LEDGER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
