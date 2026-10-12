"""Run the locked, execution-aligned final corn/wheat validation protocol."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.config import load_geography  # noqa: E402
from pipeline.model_validation import (  # noqa: E402
    BASELINE_RULES,
    CANDIDATES,
    TARGETS,
    best_baseline,
    build_execution_frame,
    evaluate_baseline,
    evaluate_candidate,
    gate,
    strata,
    uncertainty,
)
from pipeline.signals import build  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

PROTOCOL_PATH = ROOT / "config" / "corn_wheat_final_validation.yaml"
DEFAULT_REPORT = ROOT / "analysis" / "corn_wheat_final_validation_report.md"
DEFAULT_RESULTS = ROOT / "analysis" / "corn_wheat_final_validation_results.csv"
DEFAULT_BASELINES = ROOT / "analysis" / "corn_wheat_final_validation_baselines.csv"
DEFAULT_UNCERTAINTY = ROOT / "analysis" / "corn_wheat_final_validation_uncertainty.csv"
DEFAULT_STRATA = ROOT / "analysis" / "corn_wheat_final_validation_strata.csv"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_protocol(path: Path) -> dict[str, object]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("validation protocol must be a YAML mapping")
    expected_candidates = [spec.name for spec in CANDIDATES]
    if payload.get("candidates") != expected_candidates:
        raise ValueError("protocol candidate list differs from the implemented locked set")
    if payload.get("baselines") != list(BASELINE_RULES):
        raise ValueError("protocol baseline list differs from the implemented locked set")
    boundary = payload.get("information_boundary", {})
    if boundary.get("development_end_year") != 2023:
        raise ValueError("implemented selection boundary requires development through 2023")
    if boundary.get("consumed_diagnostic_used_for_selection") is not False:
        raise ValueError("consumed diagnostic sample must not be used for selection")
    return payload


def _metrics_columns(prefix: str, item: object) -> dict[str, object]:
    return {
        f"{prefix}_observations": item.observations,
        f"{prefix}_trades": item.trades,
        f"{prefix}_hit_rate": item.hit_rate,
        f"{prefix}_mean_net_per_trade": item.mean_net_per_trade,
        f"{prefix}_total_net": item.total_net,
        f"{prefix}_sharpe": item.sharpe,
        f"{prefix}_max_drawdown": item.max_drawdown,
        f"{prefix}_positive_folds": item.positive_folds,
        f"{prefix}_folds": item.folds,
        f"{prefix}_positive_years": item.positive_years,
        f"{prefix}_years": item.years,
    }


def result_row(result: object) -> dict[str, object]:
    return {
        "name": result.name,
        "kind": result.kind,
        "target": result.target,
        "target_label": result.target_label,
        "feature_set": result.feature_set,
        "model": result.model,
        "objective": result.objective,
        **_metrics_columns("development", result.development),
        **_metrics_columns("consumed_holdout", result.consumed_holdout),
    }


def _percent(value: object, digits: int = 1) -> str:
    number = float(value)
    return "—" if not np.isfinite(number) else f"{number:.{digits}%}"


def _number(value: object, digits: int = 2) -> str:
    number = float(value)
    return "—" if not np.isfinite(number) else f"{number:+.{digits}f}"


def _candidate_table(rows: pd.DataFrame) -> list[str]:
    lines = [
        "| Candidate | Target | Trades | Hit | Net | Sharpe | +years | "
        "2× cost | Bootstrap + | Null p | Gate |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    ordered = rows.sort_values(
        ["passes_gate", "development_sharpe", "development_total_net"],
        ascending=[False, False, False],
    )
    for row in ordered.itertuples(index=False):
        years = f"{row.development_positive_years}/{row.development_years}"
        lines.append(
            f"| `{row.name}` | {row.target_label} | {row.development_trades} | "
            f"{_percent(row.development_hit_rate)} | "
            f"{_percent(row.development_total_net)} | "
            f"{_number(row.development_sharpe)} | {years} | "
            f"{_percent(row.double_cost_total_net)} | "
            f"{_percent(row.bootstrap_probability_positive)} | "
            f"{_percent(row.permutation_p_value)} | "
            f"{'PASS' if row.passes_gate else 'fail'} |"
        )
    return lines


def _baseline_table(rows: pd.DataFrame) -> list[str]:
    lines = [
        "| Target | Best rule | Trades | Net | Sharpe | +years |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in rows.itertuples(index=False):
        lines.append(
            f"| {row.target_label} | `{row.model}` | {row.development_trades} | "
            f"{_percent(row.development_total_net)} | "
            f"{_number(row.development_sharpe)} | "
            f"{row.development_positive_years}/{row.development_years} |"
        )
    return lines


def _holdout_table(rows: pd.DataFrame) -> list[str]:
    lines = [
        "| Candidate | Trades | Net | Sharpe | Drawdown |",
        "|---|---:|---:|---:|---:|",
    ]
    ordered = rows.sort_values("development_sharpe", ascending=False)
    for row in ordered.itertuples(index=False):
        lines.append(
            f"| `{row.name}` | {row.consumed_holdout_trades} | "
            f"{_percent(row.consumed_holdout_total_net)} | "
            f"{_number(row.consumed_holdout_sharpe)} | "
            f"{_percent(row.consumed_holdout_max_drawdown)} |"
        )
    return lines


def _headline_strata(frame: pd.DataFrame, headline: str) -> list[str]:
    selected = frame[
        (frame["name"] == headline)
        & (frame["sample"] == "development")
        & frame["dimension"].isin(["year", "direction", "market_regime"])
    ]
    lines = [
        "| Dimension | Bucket | Trades | Hit | Net | Mean/trade |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in selected.itertuples(index=False):
        lines.append(
            f"| {row.dimension} | {row.bucket} | {row.trades} | "
            f"{_percent(row.hit_rate)} | {_percent(row.total_net)} | "
            f"{_percent(row.mean_net_per_trade)} |"
        )
    return lines


def render_report(
    candidate_rows: pd.DataFrame,
    best_baseline_rows: pd.DataFrame,
    strata_rows: pd.DataFrame,
    *,
    headline: str,
    protocol: dict[str, object],
    protocol_hash: str,
    contracts_hash: str,
    coverage: tuple[pd.Timestamp, pd.Timestamp],
) -> str:
    passing = candidate_rows[candidate_rows["passes_gate"]]
    headline_row = candidate_rows.set_index("name").loc[headline]
    if passing.empty:
        decision = (
            "No predeclared candidate clears the development-only acceptance gate. "
            "The final retrospective conclusion is NO ROBUST EDGE DEMONSTRATED."
        )
        prospective = (
            "No v2 model is promoted. The already frozen v1 specification remains an "
            "observation-only falsification candidate for the prospective ledger."
        )
    else:
        decision = (
            f"{len(passing)} predeclared candidate(s) clear the development-only gate; "
            f"`{headline}` is the highest-development-Sharpe candidate."
        )
        prospective = (
            f"Freeze `{headline}` as a new version before its next eligible decision; "
            "the consumed diagnostic result is not a promotion criterion."
        )
    failures = str(headline_row["gate_failures"]).split(" | ")
    lines = [
        "# Corn/Wheat Final Execution-Aligned Validation",
        "",
        "## Decision",
        "",
        f"**{decision}**",
        "",
        "This result is narrower than saying weather never matters. It says that none",
        "of the finite, point-in-time strategies in protocol",
        f"`{protocol['protocol_id']}` established a repeatable edge after dated-contract",
        "entry, costs, and the no-overlap policy.",
        "",
        "## Locked protocol and audit boundary",
        "",
        f"- Protocol locked: {protocol['locked_on']}; SHA-256 `{protocol_hash}`.",
        f"- Execution-label coverage: {coverage[0].date()} through {coverage[1].date()}.",
        "- Selection sample: calendar years through 2023 only.",
        "- Calendar years 2024 onward are consumed diagnostics and never enter ranking",
        "  or any acceptance condition.",
        "- Labels use next-shared-session LSEG opens, dated ZC/ZW contracts, five",
        "  additional holding sessions, exact row costs, and no overlapping positions.",
        f"- Contract-panel SHA-256: `{contracts_hash}`.",
        "- Row-level licensed outcomes remain in ignored `data/analysis/`; committed",
        "  outputs are aggregates only.",
        "",
        "## Candidate results — development selection sample",
        "",
        *_candidate_table(candidate_rows),
        "",
        "Net is the arithmetic sum of per-decision returns. The null p-value compares",
        "the observed alignment with 2,000 within-year circular shifts of the same raw",
        "actions. Bootstrap + is the share of calendar-year block resamples above zero.",
        "No candidate is selected from the diagnostic columns below.",
        "The raw-wheat candidates are controls using the same corn-centered weather and",
        "crop-stage panel; they are not wheat-specific agronomic models.",
        "",
        "## Best simple rule for each target",
        "",
        *_baseline_table(best_baseline_rows),
        "",
        "The gate compares every model only with the strongest same-target rule ranked",
        "by development Sharpe and then net return. All seven rules remain in the CSV.",
        "",
        f"## Stability of headline candidate `{headline}`",
        "",
        *_headline_strata(strata_rows, headline),
        "",
        "The headline is the best passing candidate when one exists; otherwise it is",
        "the highest-development-Sharpe diagnostic candidate. That fallback is not a",
        "promotion. Its failed gate conditions are:",
        "",
        *[f"- {failure}" for failure in failures if failure],
        "",
        "## Consumed 2024+ diagnostic",
        "",
        *_holdout_table(candidate_rows),
        "",
        "These rows answer whether behavior persisted, but the dates were already seen",
        "during earlier work. They cannot rescue or disqualify a candidate and are not",
        "described as out-of-sample confirmation.",
        "",
        "## Prospective campaign",
        "",
        prospective,
        "The campaign starts October 12, 2026, authorizes no orders, and prohibits",
        "performance-driven tuning until at least 30 eligible decisions and one complete",
        "growing season have been recorded. Same-day registration remains mandatory; a",
        "late record is operational evidence only, never prospective evidence.",
        "",
    ]
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    result.add_argument(
        "--contracts", type=Path,
        default=default_data_root() / "processed" / "futures_contracts.parquet",
    )
    result.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    result.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    result.add_argument("--baselines", type=Path, default=DEFAULT_BASELINES)
    result.add_argument("--uncertainty", type=Path, default=DEFAULT_UNCERTAINTY)
    result.add_argument("--strata", type=Path, default=DEFAULT_STRATA)
    result.add_argument(
        "--ledger", type=Path,
        default=default_data_root() / "analysis" / "corn_wheat_final_validation.parquet",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    protocol = load_protocol(args.protocol)
    if not args.contracts.exists():
        raise SystemExit(f"contract panel not found: {args.contracts}")
    draws = int(protocol["uncertainty"]["draws"])
    seed = int(protocol["uncertainty"]["random_seed"])

    store = ProcessedStore(default_data_root())
    frames, _ = build(store, load_geography(), horizons=(5,))
    contracts = pd.read_parquet(args.contracts, engine="pyarrow")
    execution = build_execution_frame(frames[5], contracts)

    baselines = []
    for target in TARGETS:
        for rule in BASELINE_RULES:
            print(f"baseline {target} / {rule}", flush=True)
            baselines.append(evaluate_baseline(execution, target, rule))
    baseline_rows = pd.DataFrame([result_row(result) for result in baselines])
    baseline_best = {target: best_baseline(baselines, target) for target in TARGETS}
    best_rows = pd.DataFrame([result_row(result) for result in baseline_best.values()])

    candidates = []
    uncertainty_rows = []
    result_rows = []
    ledger_rows = []
    for offset, spec in enumerate(CANDIDATES):
        print(f"candidate {spec.name}", flush=True)
        result = evaluate_candidate(execution, spec)
        candidates.append(result)
        uncertain = uncertainty(
            result.development_predictions, draws=draws, seed=seed + offset)
        passed, failures, stress = gate(
            result, baseline_best[result.target], uncertain)
        row = result_row(result)
        row.update(uncertain)
        row.update({
            "best_baseline": baseline_best[result.target].name,
            "best_baseline_total_net": baseline_best[result.target].development.total_net,
            "best_baseline_sharpe": baseline_best[result.target].development.sharpe,
            "double_cost_total_net": stress,
            "passes_gate": passed,
            "gate_failures": " | ".join(failures),
        })
        result_rows.append(row)
        uncertainty_rows.append({"name": result.name, **uncertain})
        for sample, predictions in (
            ("development", result.development_predictions),
            ("consumed_holdout", result.holdout_predictions),
        ):
            if not predictions.empty:
                ledger_rows.append(predictions.assign(name=result.name, sample=sample))

    candidate_rows = pd.DataFrame(result_rows)
    passing = candidate_rows[candidate_rows["passes_gate"]]
    pool = passing if not passing.empty else candidate_rows
    headline = pool.sort_values(
        ["development_sharpe", "development_total_net"], ascending=False
    ).iloc[0]["name"]
    headline_result = next(result for result in candidates if result.name == headline)
    headline_baseline = baseline_best[headline_result.target]
    strata_frames = []
    for item in (headline_result, headline_baseline):
        for sample, predictions in (
            ("development", item.development_predictions),
            ("consumed_holdout", item.holdout_predictions),
        ):
            table = strata(predictions, name=item.name, sample=sample)
            if not table.empty:
                strata_frames.append(table)
    strata_rows = pd.concat(strata_frames, ignore_index=True)

    report = render_report(
        candidate_rows, best_rows, strata_rows, headline=headline,
        protocol=protocol, protocol_hash=_sha256(args.protocol),
        contracts_hash=_sha256(args.contracts),
        coverage=(execution["date"].min(), execution["date"].max()),
    )
    for path in (
        args.report, args.results, args.baselines, args.uncertainty,
        args.strata, args.ledger,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    candidate_rows.to_csv(args.results, index=False, float_format="%.8f")
    baseline_rows.to_csv(args.baselines, index=False, float_format="%.8f")
    pd.DataFrame(uncertainty_rows).to_csv(
        args.uncertainty, index=False, float_format="%.8f")
    strata_rows.to_csv(args.strata, index=False, float_format="%.8f")
    pd.concat(ledger_rows, ignore_index=True).to_parquet(
        args.ledger, index=False, engine="pyarrow")
    print("\n" + report.splitlines()[4])
    print(f"report: {args.report}")
    print(f"results: {args.results}")
    print(f"baselines: {args.baselines}")
    print(f"uncertainty: {args.uncertainty}")
    print(f"strata: {args.strata}")
    print(f"licensed ledger (ignored): {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
