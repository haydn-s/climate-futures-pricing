"""Run the frozen Coffee C frost-curve historical extension test."""

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

from pipeline.coffee_frost_validation import (  # noqa: E402
    acceptance_gate,
    daily_curve_outcomes,
    frost_metrics,
    frost_trade_ledger,
    season_matched_null,
)
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

PROTOCOL_PATH = ROOT / "config" / "coffee_frost_curve_validation.yaml"
RESULTS_PATH = ROOT / "analysis" / "coffee_frost_curve_validation_results.csv"
UNCERTAINTY_PATH = ROOT / "analysis" / "coffee_frost_curve_validation_uncertainty.csv"
REPORT_PATH = ROOT / "analysis" / "coffee_frost_curve_validation_report.md"
LEDGER_PATH = ROOT / "data" / "analysis" / "coffee_frost_curve_validation_ledger.parquet"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fmt(value: float, kind: str = "number") -> str:
    if not np.isfinite(value):
        return "n/a"
    return f"{value:.2%}" if kind == "pct" else f"{value:.3f}"


def main() -> None:
    store = ProcessedStore(default_data_root())
    contracts = store.read("coffee_futures_contracts")
    power = store.read("nasa_power_point_daily")
    ledger = frost_trade_ledger(contracts, power)
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    ledger.to_parquet(LEDGER_PATH, index=False)

    samples = {
        "new_2000_2011_extension": ledger.loc[
            ledger["sample"] == "new_2000_2011_extension"
        ],
        "selected_2012_2023": ledger.loc[
            ledger["sample"] == "selected_2012_2023"
        ],
        "consumed_2024_plus": ledger.loc[
            ledger["sample"] == "consumed_2024_plus"
        ],
        "pooled_pre_2024_descriptive": ledger.loc[ledger["event_year"] < 2024],
    }
    metric_rows = []
    metrics_by_sample = {}
    for name, sample in samples.items():
        metrics = frost_metrics(sample)
        metrics_by_sample[name] = metrics
        metric_rows.append({"sample": name, **asdict(metrics)})
    pd.DataFrame(metric_rows).to_csv(RESULTS_PATH, index=False)

    daily = daily_curve_outcomes(contracts)
    extension = samples["new_2000_2011_extension"]
    pooled = samples["pooled_pre_2024_descriptive"]
    nulls = {
        "new_2000_2011_extension": season_matched_null(extension, daily),
        "pooled_pre_2024_descriptive": season_matched_null(pooled, daily),
    }
    uncertainty = pd.DataFrame([
        {"sample": sample, **values} for sample, values in nulls.items()
    ])
    uncertainty.to_csv(UNCERTAINTY_PATH, index=False)

    gate_passed, checks = acceptance_gate(
        metrics_by_sample["new_2000_2011_extension"],
        nulls["new_2000_2011_extension"],
    )
    extension_metrics = metrics_by_sample["new_2000_2011_extension"]
    pooled_metrics = metrics_by_sample["pooled_pre_2024_descriptive"]

    lines = [
        "# Coffee frost-curve validation",
        "",
        "## Decision",
        "",
        "**The frozen frost-curve rule fails the historical-extension gate. "
        "Close the coffee modeling avenue as suggestive but operationally unsupported.**",
        "",
        "The newly added 2000–2011 history contains only "
        f"{extension_metrics.events} eligible frost episodes, below the locked minimum "
        "of five. The long-nearby/short-deferred trade is positive after costs in "
        f"{extension_metrics.positive_events}/{extension_metrics.events} events, with "
        f"mean net {_fmt(extension_metrics.mean_net_return, 'pct')} and median net "
        f"{_fmt(extension_metrics.median_net_return, 'pct')}.",
        "",
        "The same-year June–August placebo test gives a one-sided p-value of "
        f"{float(nulls['new_2000_2011_extension']['p_value']):.3f}. The extension "
        "therefore fails on sample size, return sign, consistency, and the null test.",
        "",
        "## Evidence boundary",
        "",
        f"- Protocol: `coffee-frost-curve-validation-v1`; SHA-256 `{_sha256(PROTOCOL_PATH)}`.",
        "- The fixed direction, horizon, threshold, geography, and gate were saved "
        "before the 2000–2011 LSEG contracts were downloaded.",
        "- The three 2012–2023 episodes selected the rule and cannot confirm it.",
        "- The 2024+ period was already consumed and remains diagnostic only.",
        "- This is a retrospective historical extension, not prospective evidence.",
        "",
        "## Results by evidence class",
        "",
        "| Sample | Events | Mean net | Median net | Positive | Positive fraction |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in metric_rows:
        lines.append(
            f"| {row['sample']} | {row['events']} | "
            f"{_fmt(float(row['mean_net_return']), 'pct')} | "
            f"{_fmt(float(row['median_net_return']), 'pct')} | "
            f"{row['positive_events']} | {_fmt(float(row['positive_fraction']), 'pct')} |"
        )

    lines.extend([
        "",
        "Pooling selected history with the new extension is descriptive only. Across "
        f"all {pooled_metrics.events} pre-2024 episodes, mean net is "
        f"{_fmt(pooled_metrics.mean_net_return, 'pct')}, with "
        f"{pooled_metrics.positive_events}/{pooled_metrics.events} positive. Its "
        f"season-matched p-value is {float(nulls['pooled_pre_2024_descriptive']['p_value']):.3f}; "
        "it does not repair the failed independent extension.",
        "",
        "## Season-matched null",
        "",
        "| Sample | Events | Observed mean net | Null mean | Null 95% interval | p-value |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for row in uncertainty.itertuples(index=False):
        lines.append(
            f"| {row.sample} | {row.events} | {_fmt(row.observed_mean_net, 'pct')} | "
            f"{_fmt(row.null_mean, 'pct')} | "
            f"{_fmt(row.null_95_lower, 'pct')} to {_fmt(row.null_95_upper, 'pct')} | "
            f"{row.p_value:.3f} |"
        )

    lines.extend([
        "",
        "## Locked gate",
        "",
    ])
    for label, passed in checks.items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} — {label}")
    lines.extend([
        "",
        f"Overall gate: **{'PASS' if gate_passed else 'FAIL'}**.",
        "",
        "## Data and execution",
        "",
        f"- LSEG panel: {len(contracts):,} rows, {contracts['contract'].nunique()} "
        f"contracts, {contracts['date'].min().date()}–{contracts['date'].max().date()}.",
        "- Signal: coldest Brazil point below 4°C in June–August, episodes ten "
        "calendar days apart, available on NASA POWER publication date.",
        "- Trade: long nearby and short deferred at the next shared open; exit at "
        "the open 20 shared sessions later.",
        "- Net return subtracts one tick plus $2.50 commission per contract per side "
        "for both legs using row-specific entry notionals.",
        "- Placebos are non-frost June–August decisions from each event's own year.",
        "- Licensed row-level outcomes remain under ignored `data/`; committed files "
        "contain aggregates only.",
        "",
        "## Interpretation",
        "",
        "The initial 3/3 pattern was real in the observed sample, but it did not "
        "replicate backward. Additional algorithms would now be specification search, "
        "not validation. Coffee can remain a useful case study about mechanism, sparse "
        "extremes, and curve targets, but this rule is not a deliverable trading model.",
        "",
    ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {RESULTS_PATH.relative_to(ROOT)}")
    print(f"wrote {UNCERTAINTY_PATH.relative_to(ROOT)}")
    print(f"wrote {REPORT_PATH.relative_to(ROOT)}")
    print(pd.DataFrame(metric_rows).to_string(index=False))
    print("\nnull diagnostics")
    print(uncertainty.to_string(index=False))
    print(f"\noverall gate: {'PASS' if gate_passed else 'FAIL'}")


if __name__ == "__main__":
    main()
