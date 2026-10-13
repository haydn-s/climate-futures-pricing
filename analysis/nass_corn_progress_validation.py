"""Run the locked development-only USDA Crop Progress corn validation."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.crop_progress_validation import (  # noqa: E402
    CONFIRMATION_YEARS,
    DEFAULT_PROTOCOL,
    SELECTION_YEARS,
    UNTOUCHED_HOLDOUT_YEAR,
    ValidationBundle,
    evaluate_development,
    load_protocol,
)
from pipeline.storage import default_data_root  # noqa: E402

DEFAULT_INPUT = default_data_root() / "processed" / "nass_corn_progress_model_frame.parquet"
DEFAULT_RESULTS = ROOT / "analysis" / "nass_corn_progress_validation_results.csv"
DEFAULT_REPORT = ROOT / "analysis" / "nass_corn_progress_validation_report.md"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _percent(value: float) -> str:
    return "—" if not np.isfinite(value) else f"{value:+.1%}"


def _number(value: float) -> str:
    return "—" if not np.isfinite(value) else f"{value:+.2f}"


def render_report(bundle: ValidationBundle, *, protocol_hash: str,
                  input_hash: str, coverage: tuple[pd.Timestamp, pd.Timestamp]) -> str:
    headline = bundle.results.set_index("candidate").loc[bundle.headline]
    decision = (
        "The predeclared development gate passes. The model-specific sealed 2026 "
        "holdout may now be evaluated once, without changing the specification."
        if bundle.passes_gate else
        "The predeclared development gate fails. The USDA Crop Progress avenue "
        "does not advance, and the model-specific 2026 holdout remains sealed."
    )
    lines = [
        "# USDA Crop Progress Corn Validation",
        "",
        "## Decision",
        "",
        f"**{decision}**",
        "",
        f"The development-only headline is `{bundle.headline}`: "
        f"{int(headline.trades)} non-overlapping trades, "
        f"{_percent(float(headline.total_net))} net, Sharpe "
        f"{_number(float(headline.sharpe))}, and "
        f"{int(headline.positive_years)}/{int(headline.years)} positive years.",
        "",
        "## Locked candidate comparison",
        "",
        "| Candidate | Features | Trades | Hit | Net | Sharpe | +folds | +years |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    ordered = bundle.results.sort_values(
        ["sharpe", "total_net"], ascending=[False, False], na_position="last"
    )
    for row in ordered.itertuples(index=False):
        lines.append(
            f"| `{row.candidate}` | {row.features} | {row.trades} | "
            f"{_percent(float(row.hit_rate))} | {_percent(float(row.total_net))} | "
            f"{_number(float(row.sharpe))} | {row.positive_folds}/{row.folds} | "
            f"{row.positive_years}/{row.years} |"
        )
    lines.extend([
        "",
        "## Gate diagnostics",
        "",
        f"- Headline at doubled costs: {_percent(bundle.double_cost_total_net)}.",
        f"- Fixed condition-deterioration rule: "
        f"{_percent(bundle.simple_rule.total_net)} net, Sharpe "
        f"{_number(bundle.simple_rule.sharpe)}.",
        f"- USDA features shifted forward one within-year release: "
        f"{_percent(bundle.placebo_forward.total_net)} net.",
        f"- USDA features shifted backward one within-year release: "
        f"{_percent(bundle.placebo_backward.total_net)} net.",
        f"- Calendar-year block probability of positive net: "
        f"{bundle.year_block_probability_positive:.1%}.",
        "",
    ])
    if bundle.gate_failures:
        lines.extend(["Failed checks:", ""])
        lines.extend(f"- {failure}" for failure in bundle.gate_failures)
        lines.append("")
    else:
        lines.extend(["All locked development checks passed.", ""])
    lines.extend([
        "## Information boundary",
        "",
        f"- Development feature coverage: {coverage[0].date()} through "
        f"{coverage[1].date()}.",
        f"- Candidate identity is ranked only on the {SELECTION_YEARS[0]}–"
        f"{SELECTION_YEARS[-1]} outer predictions; "
        f"{CONFIRMATION_YEARS[0]}–{CONFIRMATION_YEARS[-1]} are final development "
        "confirmation years.",
        "- Hyperparameter selection remains nested inside each earlier training block.",
        f"- {UNTOUCHED_HOLDOUT_YEAR} is not scored, returned, or used by this script.",
        "- Entries use the first dated LSEG ZC session open strictly after the "
        "4:00 p.m. Eastern USDA release; overlapping positions are suppressed.",
        "- Row-level licensed inputs and predictions remain under ignored `data/`; "
        "only aggregate results are written here.",
        "",
        "## Reproducibility",
        "",
        f"- Protocol SHA-256: `{protocol_hash}`",
        f"- Model-frame SHA-256: `{input_hash}`",
        "- Orders authorized or submitted: **none**",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    protocol = load_protocol(args.protocol)
    frame = pd.read_parquet(args.input)
    bundle = evaluate_development(frame, protocol=protocol)
    results = bundle.results.copy()
    results["is_headline"] = results["candidate"].eq(bundle.headline)
    results["passes_gate"] = False
    results.loc[results["is_headline"], "passes_gate"] = bundle.passes_gate
    results["gate_failures"] = ""
    results.loc[results["is_headline"], "gate_failures"] = " | ".join(
        bundle.gate_failures
    )
    results["double_cost_total_net"] = np.nan
    results.loc[results["is_headline"], "double_cost_total_net"] = (
        bundle.double_cost_total_net
    )
    results["year_block_probability_positive"] = np.nan
    results.loc[results["is_headline"], "year_block_probability_positive"] = (
        bundle.year_block_probability_positive
    )

    args.results.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.results, index=False)
    development = frame[pd.to_numeric(frame["year"]) < UNTOUCHED_HOLDOUT_YEAR]
    report = render_report(
        bundle,
        protocol_hash=_sha256(args.protocol),
        input_hash=_sha256(args.input),
        coverage=(pd.to_datetime(development["date"]).min(),
                  pd.to_datetime(development["date"]).max()),
    )
    args.report.write_text(report, encoding="utf-8")
    print(
        f"USDA Crop Progress validation: headline={bundle.headline}, "
        f"gate={'PASS' if bundle.passes_gate else 'FAIL'}"
    )
    print(f"wrote {args.results}")
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
