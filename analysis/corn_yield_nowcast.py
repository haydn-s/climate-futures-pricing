"""Evaluate August/September/October corn yield hindcasts with cached inputs."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "climate-matplotlib"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.config import load_geography, load_sources  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402
from pipeline.yield_nowcast import build_issue_table, evaluate_issues  # noqa: E402
from pipeline.yield_validation import INPUT_TABLES, MIN_TRAIN_YEARS  # noqa: E402

COLORS = {"trend_only": "#555555", "points": "#2977b5", "points_drought": "#087f70"}
LABELS = {"trend_only": "Trend only", "points": "Point weather", "points_drought": "Point weather + drought"}
DATE_LABELS = ["Aug 1", "Sep 1", "Oct 1"]


def fingerprints(store: ProcessedStore) -> dict[str, str]:
    paths = {name: store.path(name) for name in INPUT_TABLES}
    paths.update({f"config/{name}.yaml": ROOT / "config" / f"{name}.yaml"
                  for name in ("sources", "geography")})
    result = {}
    for name, path in paths.items():
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        result[name] = digest.hexdigest()
    return result


def _save_svg(figure, path: Path) -> None:
    figure.savefig(path, metadata={"Date": None})
    # Matplotlib emits trailing spaces inside multiline path attributes.
    path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")


def plot_results(results: pd.DataFrame, predictions: pd.DataFrame, output_dir: Path) -> None:
    """Produce deterministic, presentation-ready research figures, without a UI."""
    with plt.rc_context({"svg.fonttype": "none", "svg.hashsalt": "corn-yield-nowcast-v1"}):
        fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.4), sharey=True)
        for ax, sample, title in zip(axes, ["all_years", "exclude_2012"],
                                     ["All evaluation years", "2012 excluded; models refitted"]):
            for name in COLORS:
                group = results.loc[(results["sample"] == sample)
                                    & (results["feature_set"] == name)].sort_values("issue_month")
                ax.plot(DATE_LABELS, group["mae_t_per_ha"], marker="o", color=COLORS[name],
                        linestyle="--" if name == "trend_only" else "-", label=LABELS[name])
            ax.set_title(title, fontsize=11)
            ax.set_xlabel("Forecast issue date")
            ax.grid(axis="y", alpha=0.2)
            ax.spines[["top", "right"]].set_visible(False)
        axes[0].set_ylabel("Yield MAE (tonnes per hectare; lower is better)")
        axes[1].legend(frameon=False, fontsize=8)
        fig.suptitle("How early can public weather data identify corn yield risk?", fontsize=13)
        fig.text(0.5, 0.015, "Earlier-year training only; matched years across dates; revised-data historical simulation",
                 ha="center", fontsize=8, color="#555555")
        fig.tight_layout(rect=(0, 0.045, 1, 0.96))
        _save_svg(fig, output_dir / "corn_yield_nowcast_accuracy.svg")
        plt.close(fig)

        fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.4), sharey=True)
        for ax, year in zip(axes, [2012, 2019]):
            case = predictions.loc[(predictions["sample"] == "all_years") & (predictions["year"] == year)]
            for name in ("points", "points_drought"):
                group = case.loc[case["feature_set"] == name].sort_values("issue_month")
                if not group.empty:
                    ax.plot(DATE_LABELS, group["predicted_anomaly"] * 100, marker="o",
                            color=COLORS[name], label=LABELS[name])
            if not case.empty:
                ax.axhline(case["actual_anomaly"].iloc[0] * 100, color="#b13b42", label="Observed harvest")
            ax.axhline(0, linestyle="--", color=COLORS["trend_only"], label="Trend forecast")
            ax.set_title(str(year))
            ax.set_xlabel("Forecast issue date")
            ax.grid(axis="y", alpha=0.2)
            ax.spines[["top", "right"]].set_visible(False)
        axes[0].set_ylabel("Yield deviation from training trend (%)")
        axes[1].legend(frameon=False, fontsize=8)
        fig.suptitle("Two illustrative seasons: predicted and observed yield anomalies", fontsize=13)
        fig.text(0.5, 0.015, "2012 and 2019 are illustrations, not independent validation; all years remain in the results",
                 ha="center", fontsize=8, color="#555555")
        fig.tight_layout(rect=(0, 0.045, 1, 0.96))
        _save_svg(fig, output_dir / "corn_yield_nowcast_cases.svg")
        plt.close(fig)


def render_report(results: pd.DataFrame, issues: pd.DataFrame, *, common_years: list[int],
                  hashes: dict[str, str], power_lag_days: int, min_train_years: int) -> str:
    full = results.loc[(results["sample"] == "all_years")
                       & (results["feature_set"] == "points_drought")].set_index("issue_month")
    excluded = results.loc[(results["sample"] == "exclude_2012")
                           & (results["feature_set"] == "points_drought")].set_index("issue_month")
    weather_august = results.loc[(results["sample"] == "exclude_2012")
                                 & (results["feature_set"] == "points")
                                 & (results["issue_month"] == 8)].iloc[0]
    positive_without_2012 = int((excluded["r2_vs_trend"] > 0).sum())
    lines = [
        "# Fixed-date corn yield nowcast", "", "## Question and fixed specification", "",
        "How accurately can public weather and drought data estimate final US "
        "corn yield at the end of August 1, September 1, and October 1? The three "
        "dates, two existing point-feature sets, and standardized Ridge(alpha=1) "
        "are fixed; there is no model, date, threshold, or hyperparameter search.", "",
        f"Training uses only earlier crop years, with at least {min_train_years} "
        "complete training seasons. Every fold refits the yield trend, scaling, "
        "and regression. The trend-only forecast uses the same training years. "
        f"All dates and models share {len(common_years)} complete seasons "
        f"({common_years[0]}–{common_years[-1]}). Removing 2012 excludes it "
        "from training and evaluation and refits every fold.", "",
        "## Information available at each issue", "",
        f"- POWER uses the existing {power_lag_days}-day publication-lag assumption. "
        "Both the observation date and recorded publication date must be within "
        "the cutoff. Every configured corn point must have finite temperature "
        "and precipitation and a known publication date for every expected day "
        "from April 1 through the lagged cutoff.",
        "- USDM uses the existing Tuesday-map/Thursday-release rule. Every "
        "expected released map and every county in the first archived map's "
        "fixed corn-state universe must be present, finite, and published. "
        "Counties are averaged within states and states equally across the belt.",
        "- Features retain the original study definitions: July heat degree-days "
        "above 29 °C, mean July maximum temperature, June–August rainfall, and "
        "April-to-issue peak D2-or-worse drought coverage. August 1 contains only "
        "the published portion of July. The points model uses heat, temperature, "
        "and rain; points+drought uses heat, rain, and peak drought.",
        "- Missing observations or publication dates block the affected feature "
        "record. The comparison then takes the common complete years across all "
        "dates and models; missing inputs are never imputed as mild conditions.", "",
        "## Results", "",
        "| Sample | Issue | Model | Years | MAE (t/ha) | RMSE (t/ha) | R² vs trend | MAE improvement |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in results.itertuples(index=False):
        lines.append(
            f"| {row.sample} | {row.issue_month:02d}-01 | {row.feature_set} | "
            f"{row.test_years} | {row.mae_t_per_ha:.3f} | {row.rmse_t_per_ha:.3f} | "
            f"{row.r2_vs_trend:+.3f} | {row.mae_improvement:+.1%} |"
        )
    lines += [
        "", "![MAE by issue date](corn_yield_nowcast_accuracy.svg)", "",
        "R² versus trend is 1 − SSE(model) / SSE(fold-specific trend forecast). "
        "MAE and RMSE use final yield in tonnes per hectare. Earlier-year "
        "training and matched cohorts make the three issue dates comparable; "
        "a later issue is not guaranteed to improve either metric.", "",
        "## Interpretation", "",
        f"The matched full run evaluates {int(full.loc[8, 'test_years'])} harvests "
        f"from {int(full.loc[8, 'first_test_year'])}–{int(full.loc[8, 'last_test_year'])}, "
        f"or {int(excluded.loc[8, 'test_years'])} after excluding 2012. "
        "Points-plus-drought R² versus trend for August, September, and October "
        f"is {full.loc[8, 'r2_vs_trend']:+.3f}, {full.loc[9, 'r2_vs_trend']:+.3f}, "
        f"and {full.loc[10, 'r2_vs_trend']:+.3f}. After removing 2012 and refitting "
        f"the scores are {excluded.loc[8, 'r2_vs_trend']:+.3f}, "
        f"{excluded.loc[9, 'r2_vs_trend']:+.3f}, and {excluded.loc[10, 'r2_vs_trend']:+.3f}.", "",
        f"The points-plus-drought model retains positive R² versus trend at "
        f"{positive_without_2012}/3 dates without 2012. The weather-only August "
        f"score without 2012 is {weather_august.r2_vs_trend:+.3f}. "
        "The small sample and sensitivity to a single exceptional season limit "
        "claims of stable early-season forecast usefulness. "
        "The stronger full-season county-weather result therefore cannot be "
        "assumed to hold for an earlier, available point-weather information set.", "",
        "## Illustrative seasons", "",
        "![2012 and 2019 forecast paths](corn_yield_nowcast_cases.svg)", "",
        "These two named seasons illustrate changing predictions and errors. "
        "They are not an acceptance test or a substitute for the full-year "
        "prediction CSV and the run that excludes 2012.", "",
        "## County archive availability audit", "",
        "County weather is excluded from the forecast features. Older county "
        "observations in this archive were published or rebuilt much later "
        "than their observation dates, including files dated 2022. Their "
        "publication dates are preserved; an assumed month lag is not used "
        "to manufacture earlier historical availability.", "",
        "| Issue | Harvest years audited | Years with any published current-season county days |",
        "|---|---:|---:|",
    ]
    for month, group in issues.groupby("issue_month"):
        lines.append(f"| {month:02d}-01 | {len(group)} | {int((group.county_published_days > 0).sum())} |")
    lines += [
        "", "The county coverage count is an upper-bound availability diagnostic, "
        "not certification of complete county coverage or first-release vintages. "
        "The feature/audit CSV preserves published-day counts and observation "
        "endpoints for every harvest/issue pair.", "",
        "## Limits on interpretation", "",
        "This is a historical simulation under reconstructed release rules, "
        "not a vintage-exact point-in-time backtest or a new untouched holdout. "
        "POWER may use revised reanalysis, USDM serves revised historical maps, "
        "and OWID/FAO yield labels have no original release vintages here. "
        "Earlier-year final yields stand in for the harvest estimates that "
        "would have been available then. The five-state weather belt is equally "
        "weighted while the target is national yield. A yield forecast does "
        "not establish information incremental to market expectations or an "
        "executable futures-return signal. No trading specification is changed.", "",
        "## Reproduce", "", "```bash",
        "PYTHONPATH=src python analysis/corn_yield_nowcast.py", "```", "",
        "Use `--data-root` for an archived data directory and `--output-dir` for "
        "a separate destination. The runner needs no network or licensed prices. "
        "It writes the feature/coverage audit, per-year predictions, result table, "
        "report, and two SVG figures. Inspect the "
        "[feature/coverage audit](corn_yield_nowcast_features.csv), "
        "[per-year predictions](corn_yield_nowcast_predictions.csv), and "
        "[result table](corn_yield_nowcast_results.csv). Input fingerprints:", "",
    ]
    lines.extend(f"- `{name}`: `{digest}`" for name, digest in hashes.items())
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--output-dir", type=Path, default=ROOT / "analysis")
    parser.add_argument("--min-train-years", type=int, default=MIN_TRAIN_YEARS)
    args = parser.parse_args()
    store = ProcessedStore(args.data_root)
    lag = load_sources()["nasa_power"].publication_lag_days
    if lag is None:
        raise SystemExit("the nowcast requires an explicit POWER lag convention")
    issues = build_issue_table(
        store.read("nasa_power_point_daily"), store.read("usdm_county_drought"),
        store.read("nclimgrid_county_daily"), store.read("owid_yields_annual"),
        load_geography(), power_lag_days=lag,
    )
    predictions, results, common_years = evaluate_issues(issues, min_train_years=args.min_train_years)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.output_dir / "corn_yield_nowcast"
    issues.to_csv(f"{stem}_features.csv", index=False, float_format="%.10g", date_format="%Y-%m-%d")
    predictions.to_csv(f"{stem}_predictions.csv", index=False, float_format="%.10g", date_format="%Y-%m-%d")
    results.to_csv(f"{stem}_results.csv", index=False, float_format="%.10g")
    plot_results(results, predictions, args.output_dir)
    Path(f"{stem}_report.md").write_text(render_report(
        results, issues, common_years=common_years, hashes=fingerprints(store),
        power_lag_days=lag, min_train_years=args.min_train_years,
    ))
    print(results.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"Wrote {stem}_{{features.csv,predictions.csv,results.csv,report.md,accuracy.svg,cases.svg}}")


if __name__ == "__main__":
    main()
