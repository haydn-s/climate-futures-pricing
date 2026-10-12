"""Run fixed-specification robustness tests for the selected cocoa model."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "8")

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.cocoa_model import COCOA_TARGET, FEATURE_SETS, weekly_model_frame  # noqa: E402
from pipeline.cocoa_validation import (  # noqa: E402
    BASELINE_RULES,
    acceptance_gate,
    align_predictions,
    baseline_ledger,
    best_baseline,
    cost_stress,
    strata,
)
from pipeline.model_validation import metrics, uncertainty  # noqa: E402
from pipeline.signal_eval import evaluate  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

PROTOCOL_PATH = ROOT / "config" / "cocoa_fixed_validation.yaml"
RESULTS_PATH = ROOT / "analysis" / "cocoa_fixed_validation_results.csv"
BASELINES_PATH = ROOT / "analysis" / "cocoa_fixed_validation_baselines.csv"
UNCERTAINTY_PATH = ROOT / "analysis" / "cocoa_fixed_validation_uncertainty.csv"
STRATA_PATH = ROOT / "analysis" / "cocoa_fixed_validation_strata.csv"
REPORT_PATH = ROOT / "analysis" / "cocoa_fixed_validation_report.md"
LEDGER_PATH = default_data_root() / "analysis" / "cocoa_fixed_validation_ledger.parquet"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_protocol() -> dict[str, object]:
    protocol = yaml.safe_load(PROTOCOL_PATH.read_text(encoding="utf-8"))
    if not isinstance(protocol, dict) or protocol.get("schema_version") != 1:
        raise ValueError("cocoa fixed-validation protocol must use schema version 1")
    candidate = protocol.get("candidate", {})
    expected = {
        "name": "weather_market_logistic",
        "feature_set": "weather_market",
        "model": "logistic",
        "horizon_sessions": 5,
    }
    if candidate != expected:
        raise ValueError("protocol candidate differs from the implemented frozen specification")
    if protocol.get("baselines") != list(BASELINE_RULES):
        raise ValueError("protocol baselines differ from the implemented rules")
    boundary = protocol.get("information_boundary", {})
    if boundary.get("development_end_year") != 2023:
        raise ValueError("fixed validation requires development through 2023")
    if boundary.get("consumed_diagnostic_used_for_gate") is not False:
        raise ValueError("consumed diagnostic data cannot enter the gate")
    return protocol


def _metrics_row(sample: str, multiplier: float, values) -> dict[str, object]:
    return {"sample": sample, "cost_multiplier": multiplier, **asdict(values)}


def _pct(value: object, digits: int = 1) -> str:
    number = float(value)
    return "—" if not np.isfinite(number) else f"{number:.{digits}%}"


def _num(value: object, digits: int = 2) -> str:
    number = float(value)
    return "—" if not np.isfinite(number) else f"{number:+.{digits}f}"


def _metrics_table(rows: pd.DataFrame) -> list[str]:
    lines = [
        "| Sample | Cost | Trades | Hit | Net | Sharpe | Drawdown | +years |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows.itertuples(index=False):
        lines.append(
            f"| {row.sample} | {row.cost_multiplier:g}× | {row.trades} | "
            f"{_pct(row.hit_rate)} | {_pct(row.total_net)} | {_num(row.sharpe)} | "
            f"{_pct(row.max_drawdown)} | {row.positive_years}/{row.years} |"
        )
    return lines


def _baseline_table(rows: pd.DataFrame) -> list[str]:
    lines = [
        "| Rule | Trades | Hit | Net | Sharpe | Drawdown | +years |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    ordered = rows.sort_values(["sharpe", "total_net"], ascending=False, na_position="last")
    for row in ordered.itertuples(index=False):
        lines.append(
            f"| `{row.rule}` | {row.trades} | {_pct(row.hit_rate)} | "
            f"{_pct(row.total_net)} | {_num(row.sharpe)} | "
            f"{_pct(row.max_drawdown)} | {row.positive_years}/{row.years} |"
        )
    return lines


def _strata_table(rows: pd.DataFrame) -> list[str]:
    selected = rows[
        (rows["sample"] == "development")
        & rows["dimension"].isin(["year", "season", "price_regime"])
    ]
    lines = [
        "| Dimension | Bucket | Trades | Hit | Net | Sharpe |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in selected.itertuples(index=False):
        lines.append(
            f"| {row.dimension} | {row.bucket} | {row.trades} | "
            f"{_pct(row.hit_rate)} | {_pct(row.total_net)} | {_num(row.sharpe)} |"
        )
    return lines


def render_report(
    protocol: dict[str, object],
    result_rows: pd.DataFrame,
    baseline_rows: pd.DataFrame,
    uncertain: dict[str, float | int],
    strata_rows: pd.DataFrame,
    checks: dict[str, bool],
    passed: bool,
    best_rule: str,
    protocol_hash: str,
    contracts_hash: str,
    model_skipped_overlap: int,
) -> str:
    base = result_rows[
        (result_rows["sample"] == "development")
        & (result_rows["cost_multiplier"] == 1.0)
    ].iloc[0]
    diagnostic = result_rows[
        (result_rows["sample"] == "consumed_2024_plus")
        & (result_rows["cost_multiplier"] == 1.0)
    ].iloc[0]
    failures = [label for label, ok in checks.items() if not ok]
    decision = (
        "The frozen cocoa candidate passes every retrospective robustness check."
        if passed else
        "The frozen cocoa candidate fails the acceptance gate. "
        "The validated conclusion is NO ROBUST EDGE DEMONSTRATED."
    )
    lines = [
        "# Cocoa Fixed-Specification Validation",
        "",
        "## Decision",
        "",
        f"**{decision}**",
        "",
        f"With exact row-specific costs and the no-overlap policy, the selected model "
        f"produces {_pct(base.total_net)} summed development net return, "
        f"Sharpe {_num(base.sharpe)}, and positive results in "
        f"{base.positive_years}/{base.years} scored years. Its 95% year-block "
        f"bootstrap interval is {_pct(uncertain['bootstrap_p025'])} to "
        f"{_pct(uncertain['bootstrap_p975'])}; the within-year circular-shift "
        f"null p-value is {_pct(uncertain['permutation_p_value'])}.",
        "",
        f"The strongest deterministic rule is `{best_rule}`. The 2024+ consumed "
        f"diagnostic remains sharply negative at {_pct(diagnostic.total_net)} and "
        f"Sharpe {_num(diagnostic.sharpe)}; it is reported but does not enter a gate.",
        "",
        "## Audit boundary",
        "",
        f"- Protocol: `{protocol['protocol_id']}`; SHA-256 `{protocol_hash}`.",
        "- Candidate: `weather_market_logistic`, selected before this robustness run.",
        "- Selection and all gate calculations end in 2023.",
        "- Outcomes use dated LSEG ICE-US CC contracts, next-open entry, and the open",
        "  five sessions later on the same contract.",
        "- Costs are row-specific; one tick plus $2.50 commission is charged per side.",
        f"- The no-overlap policy skipped {model_skipped_overlap} otherwise active model "
        "decisions in the development ledger.",
        f"- Ignored contract-panel SHA-256: `{contracts_hash}`.",
        "- Licensed row-level results remain under ignored `data/`; committed outputs",
        "  contain aggregates only.",
        "",
        "## Cost sensitivity",
        "",
        *_metrics_table(result_rows),
        "",
        "## Exact-date deterministic baselines — development only",
        "",
        *_baseline_table(baseline_rows),
        "",
        "Every rule uses the same 530 forward-fold dates, contracts, outcomes, costs,",
        "and overlap policy as the model. There is no resampling or favorable date cut.",
        "",
        "## Stability — development only",
        "",
        *_strata_table(strata_rows),
        "",
        "## Gate checks",
        "",
        *[f"- {'PASS' if ok else 'FAIL'} — {label}" for label, ok in checks.items()],
        "",
        "The failed conditions are:",
        "",
        *([f"- {label}" for label in failures] if failures else ["- none"]),
        "",
        "## Interpretation",
        "",
        "Weather improved the original development ranking relative to market-only",
        "features, but this fixed test asks a harder question: whether the chosen actions",
        "are unusually well aligned with subsequent dated-contract returns and stable",
        "across years and regimes. A gate failure means the evidence does not support",
        "margin-history work or a capital-constrained cocoa simulation at this stage.",
        "The appropriate deliverable is a negative result plus a genuinely prospective",
        "observation plan, not another retrospective model search.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    protocol = load_protocol()
    store = ProcessedStore(default_data_root())
    contracts_path = default_data_root() / "processed" / "cocoa_futures_contracts.parquet"
    contracts = store.read("cocoa_futures_contracts")
    power = store.read("nasa_power_point_daily")
    frame = weekly_model_frame(contracts, power)
    development = frame.loc[frame["year"] < 2024]
    cost_bps = float(development["cost_rate"].median() * 10_000)

    frozen = evaluate(
        frame,
        features=tuple(FEATURE_SETS["weather_market"]),
        feature_set="weather_market",
        target=COCOA_TARGET,
        horizon=5,
        model_name="logistic",
        cost_bps_per_leg=cost_bps,
        holdout_year=2024,
        outer_splits=5,
    )
    development_ledger = align_predictions(frozen.predictions, frame)
    diagnostic_ledger = align_predictions(frozen.holdout_predictions, frame)

    baseline_ledgers = {
        rule: baseline_ledger(development_ledger, rule) for rule in BASELINE_RULES
    }
    best_rule, best_rule_metrics = best_baseline(baseline_ledgers)
    draws = int(protocol["uncertainty"]["draws"])
    seed = int(protocol["uncertainty"]["random_seed"])
    uncertain = uncertainty(development_ledger, draws=draws, seed=seed)

    result_rows = []
    stressed_ledgers: dict[float, pd.DataFrame] = {}
    for multiplier in (0.0, 1.0, 2.0):
        stressed, values = cost_stress(development_ledger, multiplier)
        stressed_ledgers[multiplier] = stressed
        result_rows.append(_metrics_row("development", multiplier, values))
    diagnostic_exact, diagnostic_values = cost_stress(diagnostic_ledger, 1.0)
    result_rows.append(_metrics_row("consumed_2024_plus", 1.0, diagnostic_values))
    result_rows_frame = pd.DataFrame(result_rows)

    model_metrics = metrics(stressed_ledgers[1.0])
    double_metrics = metrics(stressed_ledgers[2.0])
    passed, checks = acceptance_gate(
        model_metrics, best_rule_metrics, uncertain, double_metrics
    )

    baseline_rows = pd.DataFrame([
        {"rule": rule, **asdict(metrics(ledger))}
        for rule, ledger in baseline_ledgers.items()
    ])
    uncertainty_row = pd.DataFrame([{
        **uncertain,
        "year_blocks": model_metrics.years,
        "observed_total_net": model_metrics.total_net,
    }])
    strata_rows = pd.concat([
        strata(stressed_ledgers[1.0], sample="development"),
        strata(diagnostic_exact, sample="consumed_2024_plus"),
    ], ignore_index=True)

    result_rows_frame.to_csv(RESULTS_PATH, index=False, float_format="%.8f")
    baseline_rows.to_csv(BASELINES_PATH, index=False, float_format="%.8f")
    uncertainty_row.to_csv(UNCERTAINTY_PATH, index=False, float_format="%.8f")
    strata_rows.to_csv(STRATA_PATH, index=False, float_format="%.8f")
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    pd.concat([
        development_ledger.assign(sample="development"),
        diagnostic_ledger.assign(sample="consumed_2024_plus"),
    ], ignore_index=True).to_parquet(LEDGER_PATH, index=False)

    model_skipped = int(
        ((development_ledger["raw_signal"] != 0)
         & (development_ledger["signal"] == 0)).sum()
    )
    report = render_report(
        protocol, result_rows_frame, baseline_rows, uncertain, strata_rows,
        checks, passed, best_rule, _sha256(PROTOCOL_PATH), _sha256(contracts_path),
        model_skipped,
    )
    REPORT_PATH.write_text(report, encoding="utf-8")

    print(f"decision: {'PASS' if passed else 'NO ROBUST EDGE DEMONSTRATED'}")
    print(f"best baseline: {best_rule}")
    for label, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'} {label}")
    print(f"report: {REPORT_PATH.relative_to(ROOT)}")
    print(f"results: {RESULTS_PATH.relative_to(ROOT)}")
    print(f"baselines: {BASELINES_PATH.relative_to(ROOT)}")
    print(f"uncertainty: {UNCERTAINTY_PATH.relative_to(ROOT)}")
    print(f"strata: {STRATA_PATH.relative_to(ROOT)}")
    print(f"ignored ledger: {LEDGER_PATH}")


if __name__ == "__main__":
    main()
