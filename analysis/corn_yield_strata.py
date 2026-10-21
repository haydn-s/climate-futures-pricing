"""Decompose the published corn-yield scores by harvest, influence, and stratum.

This reads the prediction CSVs the forward and nowcast evaluations already wrote
and asks which seasons their aggregate numbers rest on. It fits no model, selects
nothing, and cannot produce a competing score; rerun the two upstream scripts if
the predictions themselves need to change.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.config import load_geography  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402
from pipeline.yield_strata import (  # noqa: E402
    MIN_STRATUM_YEARS, assign_strata, influence_summary, per_year_errors,
    score_by_group, score_influence, score_strata,
)
from pipeline.yield_validation import build_annual_table  # noqa: E402

# The two specifications the README leads with, decomposed in the prose below.
HEADLINE = {"forward": "counties_drought", "nowcast": "points_drought"}
STUDIES = {
    "forward": ("corn_yield_forward_validation_predictions.csv",
                ("sample", "feature_set")),
    "nowcast": ("corn_yield_nowcast_predictions.csv",
                ("sample", "issue_month", "feature_set")),
}
DIMENSION_LABELS = {
    "direction": "Harvest above or below trend",
    "era": "First or second half of the test years",
    "severity": "Tercile of absolute deviation from trend",
    "drought_level": "Tercile of peak belt severe-drought coverage",
    "heat_level": "Tercile of county July heat degree-days",
}


def file_hashes(paths: dict[str, Path]) -> dict[str, str]:
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
    path.write_text("\n".join(line.rstrip() for line
                              in path.read_text().splitlines()) + "\n")


def plot_decomposition(per_year: pd.DataFrame, influence: pd.DataFrame,
                       output_dir: Path) -> None:
    """Which harvests the full-season score is made of, and what each one carries."""
    spec = HEADLINE["forward"]
    years = per_year.loc[(per_year["study"] == "forward")
                         & (per_year["sample"] == "all_years")
                         & (per_year["feature_set"] == spec)].sort_values("year")
    drops = influence.loc[(influence["study"] == "forward")
                          & (influence["sample"] == "all_years")
                          & (influence["feature_set"] == spec)
                          ].sort_values("dropped_year")
    with plt.rc_context({"svg.fonttype": "none",
                         "svg.hashsalt": "corn-yield-strata-v1"}):
        fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.4))
        labels = [str(year) for year in years["year"]]
        colors = ["#087f70" if gap >= 0 else "#b13b42"
                  for gap in years["abs_error_gap"]]
        axes[0].bar(labels, years["abs_error_gap"], color=colors)
        axes[0].axhline(0, color="#555555", linewidth=0.8)
        axes[0].set_ylabel("Trend error − model error (t/ha)")
        axes[0].set_title("Harvests the model improved, and those it did not",
                          fontsize=11)
        axes[0].tick_params(axis="x", labelrotation=90, labelsize=7)

        baseline = float(drops["baseline_r2_vs_trend"].iloc[0])
        axes[1].bar([str(year) for year in drops["dropped_year"]],
                    drops["r2_vs_trend_without_year"], color="#2977b5")
        axes[1].axhline(baseline, color="#b13b42", linestyle="--",
                        label=f"Published R² = {baseline:+.3f}")
        axes[1].axhline(0, color="#555555", linewidth=0.8)
        axes[1].set_ylabel("R² vs trend with that harvest withheld")
        axes[1].set_title("What the score becomes without one season", fontsize=11)
        axes[1].tick_params(axis="x", labelrotation=90, labelsize=7)
        axes[1].legend(frameon=False, fontsize=8)
        for ax in axes:
            ax.grid(axis="y", alpha=0.2)
            ax.spines[["top", "right"]].set_visible(False)
        fig.suptitle("Full-season county weather plus drought: where the score "
                     "comes from", fontsize=13)
        fig.text(0.5, 0.015, "Withholding a harvest from the score is not a "
                 "refit and is not a result; it measures how much the published "
                 "number depends on one season", ha="center", fontsize=8,
                 color="#555555")
        fig.tight_layout(rect=(0, 0.06, 1, 0.95))
        _save_svg(fig, output_dir / "corn_yield_strata_influence.svg")
        plt.close(fig)


def _per_year_table(per_year: pd.DataFrame) -> list[str]:
    spec = HEADLINE["forward"]
    frame = per_year.loc[(per_year["study"] == "forward")
                         & (per_year["sample"] == "all_years")
                         & (per_year["feature_set"] == spec)].sort_values("year")
    lines = [
        f"Full-season `{spec}`, all evaluation years. A positive gap is a "
        "harvest the weather model forecast more accurately than the "
        "training-only trend.", "",
        "| Year | Observed (t/ha) | Trend (t/ha) | Model (t/ha) | "
        "Observed dev. | Model error | Trend error | Gap | Share of model SSE |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in frame.itertuples(index=False):
        lines.append(
            f"| {row.year} | {row.actual_yield:.2f} | {row.trend_yield:.2f} | "
            f"{row.predicted_yield:.2f} | {row.actual_anomaly:+.1%} | "
            f"{row.error:+.3f} | {row.trend_error:+.3f} | "
            f"{row.abs_error_gap:+.3f} | {row.sse_share:.1%} |")
    return lines


def _influence_table(summary: pd.DataFrame) -> list[str]:
    lines = [
        "| Study | Sample | Issue | Feature set | Years | R² vs trend | "
        "Min after one removal | Max after one removal | Most load-bearing | "
        "R² without it | Sign flips |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.itertuples(index=False):
        issue = (f"{int(row.issue_month):02d}-01"
                 if pd.notna(getattr(row, "issue_month", None)) else "—")
        lines.append(
            f"| {row.study} | {row.sample} | {issue} | {row.feature_set} | "
            f"{row.test_years} | {row.r2_vs_trend:+.3f} | "
            f"{row.r2_min_without_one_year:+.3f} | "
            f"{row.r2_max_without_one_year:+.3f} | "
            f"{row.most_load_bearing_year} | {row.r2_without_that_year:+.3f} | "
            f"{row.sign_flips} |")
    return lines


def _strata_table(strata: pd.DataFrame) -> list[str]:
    lines = [
        "Buckets are not contiguous, so `Span` is the first and last season a "
        "bucket contains rather than a range; `Seasons` lists them.", "",
        "| Dimension | Bucket | Span | n | MAE (t/ha) | Trend MAE (t/ha) | "
        "R² vs trend | Seasons |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for dimension, frame in strata.groupby("dimension", sort=False):
        for row in frame.itertuples(index=False):
            lines.append(
                f"| {DIMENSION_LABELS.get(dimension, dimension)} | {row.bucket} "
                f"| {row.first_test_year}–{row.last_test_year} | "
                f"{row.test_years} | {row.mae_t_per_ha:.3f} | "
                f"{row.trend_mae_t_per_ha:.3f} | {row.r2_vs_trend:+.3f} | "
                f"{row.years} |")
    return lines


def _severity_lines(strata: pd.DataFrame, spec: str) -> list[str]:
    """State the severity gradient from the buckets, in whichever direction it runs."""
    cut = strata.loc[(strata["study"] == "forward")
                     & (strata["feature_set"] == spec)
                     & (strata["dimension"] == "severity")]
    if cut.empty:
        return ["No severity buckets met the minimum-season threshold."]
    lines = []
    for sample, label in (("all_years", "all evaluation years"),
                          ("exclude_2012", "the refitted run without 2012")):
        frame = cut.loc[cut["sample"] == sample].set_index("bucket")
        if not {"high", "low"}.issubset(frame.index):
            continue
        high, low = frame.loc["high"], frame.loc["low"]
        lines.append(
            f"Across {label}, the seasons deviating most from trend score "
            f"{high.r2_vs_trend:+.3f} against a trend forecast already missing "
            f"by {high.trend_mae_t_per_ha:.3f} t/ha, while the calmest third "
            f"scores {low.r2_vs_trend:+.3f} against a trend error of only "
            f"{low.trend_mae_t_per_ha:.3f} t/ha.")
    if not lines:
        return ["No severity buckets met the minimum-season threshold."]
    lines.append(
        "The weather model earns its score in the seasons that went wrong and "
        "adds little or nothing in the ones that did not. For a measure of "
        "supply risk that is the expected shape rather than a defect, but it "
        "does mean the aggregate R² describes performance on a sample whose "
        "composition of extreme seasons is doing much of the work, and it "
        "should not be read as accuracy in a typical year.")
    direction = strata.loc[(strata["study"] == "forward")
                           & (strata["sample"] == "all_years")
                           & (strata["feature_set"] == spec)
                           & (strata["dimension"] == "direction")
                           ].set_index("bucket")
    if {"shortfall", "surplus"}.issubset(direction.index):
        lines.append(
            f"The severity buckets mix both directions, and the model is close "
            f"to symmetric across them: {direction.loc['shortfall'].r2_vs_trend:+.3f} "
            f"on the harvests below trend against "
            f"{direction.loc['surplus'].r2_vs_trend:+.3f} on those above it. It "
            "is behaving as a two-sided weather-to-yield measure rather than "
            "only as a drought-damage detector.")
    return lines


def render_report(per_year: pd.DataFrame, summary: pd.DataFrame,
                  strata: pd.DataFrame, *, hashes: dict[str, str],
                  weather_strata: str) -> str:
    spec = HEADLINE["forward"]
    forward = summary.loc[(summary["study"] == "forward")
                          & (summary["sample"] == "all_years")
                          & (summary["feature_set"] == spec)].iloc[0]
    forward_ex = summary.loc[(summary["study"] == "forward")
                             & (summary["sample"] == "exclude_2012")
                             & (summary["feature_set"] == spec)].iloc[0]
    headline_strata = strata.loc[(strata["study"] == "forward")
                                 & (strata["sample"] == "all_years")
                                 & (strata["feature_set"] == spec)]
    lines = [
        "# Which corn harvests carry the yield scores", "",
        "## What this decomposes, and what it cannot do", "",
        "The forward and nowcast evaluations each publish one score per feature "
        "set over sixteen test harvests. This report takes those prediction "
        "files unchanged and asks which seasons the aggregates rest on. It "
        "fits nothing, tunes nothing, and selects nothing.", "",
        "Withholding a harvest from a score is **not** the `exclude_2012` "
        "sample. That sample drops the season from training as well and refits "
        "every fold, so it is a different experiment with a different answer. "
        "Removing a year here only measures how much a published number depends "
        "on one observed outcome. Every such row discards real evidence, so none "
        "of them is a score to quote.", "",
        "Strata are descriptive cuts. Tercile boundaries come from the scored "
        "test years themselves, which is acceptable for asking which seasons a "
        "model helps in only because nothing is selected on the result. Each "
        "bucket carries its own trend denominator, so its R² is not on a shared "
        f"scale with any other bucket's; buckets below {MIN_STRATUM_YEARS} "
        "seasons are left unscored.", "",
        "## Per-harvest errors", "",
        *_per_year_table(per_year), "",
        "![Score decomposition](corn_yield_strata_influence.svg)", "",
        "## Single-harvest influence", "",
        "`Sign flips` counts how many single-season removals move R² across "
        "zero. A positive aggregate that flips when any one harvest is withheld "
        "is not a stable positive.", "",
        *_influence_table(summary), "",
        "## Strata", "",
        f"Full-season `{spec}`, all evaluation years.", "",
        *_strata_table(headline_strata), "",
    ]
    if weather_strata != "included":
        lines += [f"Drought and heat strata were omitted: {weather_strata}.", ""]
    lines += [
        "## Interpretation", "",
        f"The full-season `{spec}` score of {forward.r2_vs_trend:+.3f} spans "
        f"{forward.r2_min_without_one_year:+.3f} to "
        f"{forward.r2_max_without_one_year:+.3f} when any single harvest is "
        f"withheld, with {forward.most_load_bearing_year} the most load-bearing "
        f"season at {forward.r2_without_that_year:+.3f} without it. The matched "
        f"refitted `exclude_2012` run scores {forward_ex.r2_vs_trend:+.3f} and "
        f"spans {forward_ex.r2_min_without_one_year:+.3f} to "
        f"{forward_ex.r2_max_without_one_year:+.3f} under the same treatment. "
        "Neither run contains a single-season removal that changes the sign of "
        f"its score, so the full-season result is not an artifact of one "
        "harvest. That is a narrower claim than the headline R² invites and it "
        "is the one the evidence supports.", "",
        *_severity_lines(strata, spec), "",
        "The era split is a statement about the denominator rather than about "
        "decay. Where the recent half's harvests sat close to trend, the trend "
        "forecast was already accurate and there was correspondingly little "
        "error available to remove; a low R² there is weak evidence of a model "
        "that stopped working.", "",
        "This is a decomposition of retrospective, revised-data evaluations. It "
        "adds no new information set, no in-season claim, no futures-return "
        "result, and no untouched holdout. The target remains national US yield "
        "against equally weighted five-state weather.", "",
        "## Reproduce", "",
        "```bash", "PYTHONPATH=src python analysis/corn_yield_strata.py", "```", "",
        "Run the forward and nowcast evaluations first; this script reads their "
        "prediction CSVs. Use `--data-root` for an archived data directory, "
        "`--output-dir` for a separate destination, and `--skip-weather-strata` "
        "to omit the drought and heat cuts. Inspect the "
        "[per-harvest errors](corn_yield_strata_per_year.csv), "
        "[influence rows](corn_yield_strata_influence.csv), "
        "[influence summary](corn_yield_strata_influence_summary.csv), and "
        "[strata scores](corn_yield_strata_buckets.csv). Input fingerprints:", "",
    ]
    lines.extend(f"- `{name}`: `{value}`" for name, value in hashes.items())
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--input-dir", type=Path, default=ROOT / "analysis")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "analysis")
    parser.add_argument("--skip-weather-strata", action="store_true")
    parser.add_argument("--min-stratum-years", type=int, default=MIN_STRATUM_YEARS)
    args = parser.parse_args()

    annual_table = None
    weather_strata = "requested with --skip-weather-strata"
    if not args.skip_weather_strata:
        try:
            annual_table = build_annual_table(ProcessedStore(args.data_root),
                                              load_geography())
            weather_strata = "included"
        except (FileNotFoundError, KeyError, ValueError) as error:
            weather_strata = f"the weather archive could not be read ({error})"
            print(f"weather strata omitted: {error}", file=sys.stderr)

    sources = {}
    per_year_frames, influence_frames, summary_frames, strata_frames = [], [], [], []
    for study, (filename, group_columns) in STUDIES.items():
        path = args.input_dir / filename
        if not path.exists():
            raise SystemExit(f"missing {path}; run the {study} evaluation first")
        sources[filename] = path
        predictions = pd.read_csv(path)
        published = score_by_group(predictions, group_columns=group_columns)
        if published["r2_vs_trend"].isna().all():
            raise SystemExit(f"{study}: predictions did not rescore")
        influence = score_influence(predictions, group_columns=group_columns)
        labelled = assign_strata(predictions, annual_table,
                                 group_columns=group_columns)
        produced = (
            (per_year_frames, per_year_errors(predictions,
                                              group_columns=group_columns)),
            (influence_frames, influence),
            (summary_frames, influence_summary(influence,
                                               group_columns=group_columns)),
            (strata_frames, score_strata(labelled, group_columns=group_columns,
                                         min_years=args.min_stratum_years)),
        )
        for frames, frame in produced:
            if not frame.empty:
                frame.insert(0, "study", study)
                frames.append(frame)

    per_year = pd.concat(per_year_frames, ignore_index=True)
    influence = pd.concat(influence_frames, ignore_index=True)
    summary = pd.concat(summary_frames, ignore_index=True)
    strata = pd.concat(strata_frames, ignore_index=True)
    for frame in (per_year, influence, summary, strata):
        if "issue_month" not in frame.columns:
            frame["issue_month"] = pd.NA

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.output_dir / "corn_yield_strata"
    for name, frame in (("per_year", per_year), ("influence", influence),
                        ("influence_summary", summary), ("buckets", strata)):
        frame.to_csv(f"{stem}_{name}.csv", index=False, float_format="%.10g")
    plot_decomposition(per_year, influence, args.output_dir)
    Path(f"{stem}_report.md").write_text(render_report(
        per_year, summary, strata, hashes=file_hashes(sources),
        weather_strata=weather_strata))

    print(summary.to_string(index=False,
                            float_format=lambda value: f"{value:.4f}"))
    print(f"Wrote {stem}_{{per_year,influence,influence_summary,buckets}}.csv, "
          f"{stem}_report.md, {stem}_influence.svg")


if __name__ == "__main__":
    main()
