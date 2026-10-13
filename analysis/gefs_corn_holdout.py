"""Reveal the frozen GEFS corn model's sealed 2026 holdout exactly once."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.gefs_validation import (  # noqa: E402
    DEFAULT_PROTOCOL,
    MODELS,
    evaluate_development,
    evaluate_holdout,
    load_protocol,
)
from pipeline.storage import default_data_root  # noqa: E402

DEFAULT_INPUT = default_data_root() / "processed" / "gefs_corn_model_frame.parquet"
DEFAULT_RESULTS = ROOT / "analysis" / "gefs_corn_holdout_results.csv"
DEFAULT_REPORT = ROOT / "analysis" / "gefs_corn_holdout_report.md"


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


def render_report(holdout, *, frozen_commit: str, protocol_hash: str,
                  input_hash: str) -> str:
    result = holdout.headline_metrics
    verdict = (
        "The frozen GEFS candidate passes the one-time 2026 holdout assessment."
        if holdout.passes_assessment else
        "The frozen GEFS candidate fails the one-time 2026 holdout assessment."
    )
    lines = [
        "# Operational GEFS Corn: Sealed 2026 Holdout",
        "",
        "## Decision",
        "",
        f"**{verdict}**",
        "",
        f"`{holdout.headline}` made {result.trades} non-overlapping trades, "
        f"returned {_percent(result.total_net)} net after modeled costs, recorded "
        f"{_percent(result.hit_rate)} hit rate, and had Sharpe "
        f"{_number(result.sharpe)}.",
        "",
        "## Frozen comparisons",
        "",
        "| Specification | Trades | Hit | Net | Sharpe |",
        "|---|---:|---:|---:|---:|",
        f"| Frozen GEFS candidate | {result.trades} | {_percent(result.hit_rate)} | "
        f"{_percent(result.total_net)} | {_number(result.sharpe)} |",
        f"| Same-model market-only | {holdout.market_metrics.trades} | "
        f"{_percent(holdout.market_metrics.hit_rate)} | "
        f"{_percent(holdout.market_metrics.total_net)} | "
        f"{_number(holdout.market_metrics.sharpe)} |",
        f"| Fixed heat-minus-rain rule | {holdout.simple_rule_metrics.trades} | "
        f"{_percent(holdout.simple_rule_metrics.hit_rate)} | "
        f"{_percent(holdout.simple_rule_metrics.total_net)} | "
        f"{_number(holdout.simple_rule_metrics.sharpe)} |",
        "",
        f"At doubled costs, the frozen candidate returns "
        f"{_percent(holdout.double_cost_total_net)}.",
        "",
    ]
    if holdout.assessment_failures:
        lines.extend(["Failed predeclared checks:", ""])
        lines.extend(f"- {failure}" for failure in holdout.assessment_failures)
        lines.append("")
    else:
        lines.extend(["All predeclared holdout checks passed.", ""])
    lines.extend([
        "## Freeze and information boundary",
        "",
        f"- Frozen headline: `{holdout.headline}`",
        f"- Final training-selected parameter: `{holdout.parameter}`",
        f"- Pre-reveal Git commit: `{frozen_commit}`",
        f"- Protocol SHA-256: `{protocol_hash}`",
        f"- Model-frame SHA-256: `{input_hash}`",
        "- 2026 was excluded from candidate ranking, hyperparameter evaluation, "
        "development gating, and every prior aggregate report.",
        "- This report is a one-time diagnostic. The specification must not be "
        "retuned against this holdout.",
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

    existing = [path for path in (args.results, args.report) if path.exists()]
    if existing:
        raise FileExistsError(
            "holdout output already exists; refusing to overwrite the one-time "
            f"assessment: {', '.join(map(str, existing))}"
        )
    tracked_changes = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        text=True,
    ).strip()
    if tracked_changes:
        raise ValueError(
            "tracked working tree must be clean before revealing the holdout"
        )

    protocol = load_protocol(args.protocol)
    frame = pd.read_parquet(args.input)
    development = evaluate_development(frame, protocol=protocol)
    if not development.passes_gate:
        raise ValueError("development gate does not permit revealing the 2026 holdout")
    frozen_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    holdout = evaluate_holdout(
        frame, headline=development.headline, protocol=protocol
    )
    matched_model = next(
        model for model in MODELS if holdout.headline.endswith(f"_{model}")
    )
    rows = []
    for name, item in (
        (holdout.headline, holdout.headline_metrics),
        (f"market_only_{matched_model}", holdout.market_metrics),
        ("fixed_heat_minus_rain", holdout.simple_rule_metrics),
    ):
        rows.append({"specification": name, **asdict(item)})
    results = pd.DataFrame(rows)
    results["double_cost_total_net"] = np.nan
    results.loc[0, "double_cost_total_net"] = holdout.double_cost_total_net
    results["passes_assessment"] = False
    results.loc[0, "passes_assessment"] = holdout.passes_assessment
    results["assessment_failures"] = ""
    results.loc[0, "assessment_failures"] = " | ".join(
        holdout.assessment_failures
    )
    args.results.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.results, index=False)
    args.report.write_text(render_report(
        holdout,
        frozen_commit=frozen_commit,
        protocol_hash=_sha256(args.protocol),
        input_hash=_sha256(args.input),
    ), encoding="utf-8")
    print(
        f"GEFS 2026 holdout: headline={holdout.headline}, "
        f"assessment={'PASS' if holdout.passes_assessment else 'FAIL'}"
    )
    print(f"wrote {args.results}")
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
