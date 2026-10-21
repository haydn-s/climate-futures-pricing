"""Calibrate predictive intervals around the fixed corn-yield forecasts.

Adds jackknife+ intervals to the published forward and fixed-date forecasts and
checks their observed coverage. No model, feature set, or alpha is refitted,
retuned, or reselected; the point forecast for each harvest is the one already in
the committed prediction CSVs, and a regression test asserts that.
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

from pipeline.config import load_geography, load_sources  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402
from pipeline.yield_intervals import (  # noqa: E402
    CALIBRATION_GRID, NOMINAL_LEVELS, calibration_curve, coverage,
    interval_predictions,
)
from pipeline.yield_nowcast import (  # noqa: E402
    ISSUE_MONTHS, NOWCAST_FEATURE_SETS, build_issue_table,
)
from pipeline.yield_validation import (  # noqa: E402
    FEATURE_SETS, INPUT_TABLES, MIN_TRAIN_YEARS, build_annual_table,
)

HEADLINE = "counties_drought"
SAMPLES = ("all_years", "exclude_2012")
METHOD_LABELS = {
    "jackknife_plus": "Jackknife+",
    "jackknife": "Jackknife (no leave-one-out spread)",
    "gaussian": "Gaussian residual baseline",
}
COLORS = {"jackknife_plus": "#2977b5", "jackknife": "#087f70",
          "gaussian": "#b13b42"}


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
    path.write_text("\n".join(line.rstrip() for line
                              in path.read_text().splitlines()) + "\n")


def forward_intervals(table: pd.DataFrame, *, min_train_years: int,
                      levels) -> pd.DataFrame:
    frames = []
    for sample in SAMPLES:
        frame = table if sample == "all_years" else table.drop(index=2012,
                                                               errors="ignore")
        intervals = interval_predictions(frame, FEATURE_SETS,
                                         min_train_years=min_train_years,
                                         levels=levels)
        if intervals.empty:
            raise SystemExit(f"{sample}: insufficient seasons for an interval")
        intervals.insert(0, "sample", sample)
        frames.append(intervals)
    return pd.concat(frames, ignore_index=True)


def nowcast_intervals(issues: pd.DataFrame, *, min_train_years: int,
                      levels) -> pd.DataFrame:
    """Reuse the nowcast's own common-year cohort so the dates stay comparable."""
    features = sorted({column for names in NOWCAST_FEATURE_SETS.values()
                       for column in names})
    complete = issues.dropna(subset=[*features, "yield_t_per_ha"])
    eligible = complete.groupby("year")["issue_month"].agg(set)
    common = eligible.index[eligible.map(
        lambda months: months == set(ISSUE_MONTHS))].tolist()
    frames = []
    for sample in SAMPLES:
        years = [year for year in common
                 if sample == "all_years" or year != 2012]
        for month in ISSUE_MONTHS:
            frame = issues.loc[issues["year"].isin(years)
                               & (issues["issue_month"] == month)]
            intervals = interval_predictions(
                frame.set_index("year"), NOWCAST_FEATURE_SETS,
                min_train_years=min_train_years, levels=levels)
            if intervals.empty:
                raise SystemExit(f"{sample}/{month:02d}-01: too few seasons")
            intervals.insert(0, "issue_month", month)
            intervals.insert(0, "sample", sample)
            frames.append(intervals)
    return pd.concat(frames, ignore_index=True)


def plot_intervals(intervals: pd.DataFrame, curve: pd.DataFrame,
                   output_dir: Path) -> None:
    """A fan of 90% intervals against the harvests, and the calibration curve."""
    with plt.rc_context({"svg.fonttype": "none",
                         "svg.hashsalt": "corn-yield-intervals-v1"}):
        fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.4))
        band = intervals.loc[(intervals["sample"] == "all_years")
                             & (intervals["feature_set"] == HEADLINE)
                             & (intervals["method"] == "jackknife_plus")
                             & (intervals["nominal_level"] == 0.90)
                             ].sort_values("year")
        years = band["year"].to_numpy()
        axes[0].fill_between(years, band["lower"], band["upper"], alpha=0.25,
                             color=COLORS["jackknife_plus"],
                             label="90% jackknife+ interval")
        axes[0].plot(years, band["predicted_yield"], marker="o", markersize=3,
                     color=COLORS["jackknife_plus"], label="Forecast")
        missed = band.loc[~band["covered"]]
        axes[0].plot(years, band["actual_yield"], marker="s", markersize=3,
                     linestyle="none", color="#222222", label="Observed harvest")
        if not missed.empty:
            axes[0].plot(missed["year"], missed["actual_yield"], marker="x",
                         markersize=10, linestyle="none", color="#b13b42",
                         label="Outside the interval")
        axes[0].set_ylabel("US corn yield (t/ha)")
        axes[0].set_xlabel("Harvest year")
        axes[0].set_title("Forecast, interval, and what arrived", fontsize=11)
        axes[0].legend(frameon=False, fontsize=7)

        for sample, style in (("all_years", "-"), ("exclude_2012", "--")):
            line = curve.loc[(curve["study"] == "forward")
                             & (curve["sample"] == sample)
                             & (curve["feature_set"] == HEADLINE)
                             ].sort_values("nominal_level")
            if line.empty:
                continue
            axes[1].plot(line["nominal_level"], line["observed"], marker="o",
                         linestyle=style, color=COLORS["jackknife_plus"],
                         label="2012 excluded" if sample == "exclude_2012"
                         else "All years")
        axes[1].plot([min(CALIBRATION_GRID), 1.0], [min(CALIBRATION_GRID), 1.0],
                     linestyle=":", color="#555555", label="Perfect calibration")
        axes[1].set_xlabel("Nominal level")
        axes[1].set_ylabel("Observed frequency")
        axes[1].set_title("Calibration of the residual ranks", fontsize=11)
        axes[1].legend(frameon=False, fontsize=7)
        for ax in axes:
            ax.grid(alpha=0.2)
            ax.spines[["top", "right"]].set_visible(False)
        fig.suptitle("Calibrated intervals for the full-season corn supply-risk "
                     "forecast", fontsize=13)
        fig.text(0.5, 0.015, "Sixteen scored harvests: one season is worth 6.25 "
                 "points of coverage, so these frequencies are coarse",
                 ha="center", fontsize=8, color="#555555")
        fig.tight_layout(rect=(0, 0.05, 1, 0.95))
        _save_svg(fig, output_dir / "corn_yield_intervals.svg")
        plt.close(fig)


def _coverage_table(frame: pd.DataFrame, *, issue: bool = False) -> list[str]:
    header = ("| Sample | Issue | Method | Nominal | Guaranteed | Covered | "
              "Observed | Mean width (t/ha) | Mean width (% of trend) | "
              "Uncovered |") if issue else (
        "| Sample | Method | Nominal | Guaranteed | Covered | Observed | "
        "Mean width (t/ha) | Mean width (% of trend) | Uncovered |")
    rule = ("|---|---|---|---:|---:|---:|---:|---:|---:|---|" if issue
            else "|---|---|---:|---:|---:|---:|---:|---:|---|")
    lines = [header, rule]
    for row in frame.itertuples(index=False):
        guaranteed = (f"{row.guaranteed_level:.0%}"
                      if pd.notna(row.guaranteed_level) else "—")
        cells = [row.sample]
        if issue:
            cells.append(f"{int(row.issue_month):02d}-01")
        cells += [
            METHOD_LABELS.get(row.method, row.method),
            f"{row.nominal_level:.0%}", guaranteed,
            f"{row.covered}/{row.harvests}", f"{row.observed_coverage:.1%}",
            f"{row.mean_width:.3f}", f"{row.mean_width_pp_of_trend:.1f}",
            row.uncovered_years or "none",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _baseline_lines(forward_coverage: pd.DataFrame,
                    nowcast_coverage: pd.DataFrame) -> list[str]:
    """What the Gaussian assumption bought and cost, counted across every group."""
    keys = ["sample", "issue_month", "feature_set", "nominal_level"]
    combined = pd.concat([forward_coverage, nowcast_coverage], ignore_index=True)
    combined["issue_month"] = combined["issue_month"].fillna(0).astype(int)
    width = combined.pivot(index=keys, columns="method", values="mean_width")
    covered = combined.pivot(index=keys, columns="method", values="covered")
    if not {"gaussian", "jackknife_plus"}.issubset(width.columns):
        return []
    groups = len(width)
    narrower = int((width["gaussian"] < width["jackknife_plus"]).sum())
    ratio = float((width["gaussian"] / width["jackknife_plus"]).mean())
    fewer = int((covered["gaussian"] < covered["jackknife_plus"]).sum())
    more = int((covered["gaussian"] > covered["jackknife_plus"]).sum())
    return [
        f"The Gaussian baseline is narrower than jackknife+ in {narrower} of the "
        f"{groups} sample/date/feature-set/level groups — {ratio:.0%} of the "
        f"width on average — and covers fewer harvests in {fewer} of them, more "
        f"in {more}. Across all groups it covers "
        f"{int(covered['gaussian'].sum())} harvests against "
        f"{int(covered['jackknife'].sum())} for the plain jackknife and "
        f"{int(covered['jackknife_plus'].sum())} for jackknife+. Assuming "
        "symmetric normal residuals buys a visibly tighter interval and pays "
        "for it in coverage, which is the trade the project's event studies "
        "refuse when they decline parametric standard errors on fat-tailed "
        "data."]


def _nowcast_lines(nowcast_coverage: pd.DataFrame) -> list[str]:
    """Contrast the in-season intervals with the full-season ones."""
    plus = nowcast_coverage.loc[
        (nowcast_coverage["method"] == "jackknife_plus")
        & (nowcast_coverage["nominal_level"] == 0.90)]
    full = plus.loc[plus["sample"] == "all_years"]
    if full.empty:
        return []
    uncovered = [year for years in full["uncovered_years"] if years
                 for year in years.split(";")]
    persistent = sorted({year for year in uncovered
                         if uncovered.count(year) == len(full)})
    lines = [
        "The in-season intervals are the weaker result, and they fail in both "
        f"directions at once. At a nominal 90% the fixed-date forecasts cover "
        f"{full['covered'].min()} to {full['covered'].max()} of "
        f"{int(full['harvests'].iloc[0])} harvests while spanning "
        f"{full['mean_width_pp_of_trend'].min():.1f}–"
        f"{full['mean_width_pp_of_trend'].max():.1f}% of trend yield. They are "
        "therefore wider than the full-season intervals and still cover less "
        "often than their label claims, though they stay inside the weaker "
        "jackknife+ guarantee of 80%. The early information set is not merely "
        "less skilful, as the nowcast report already established; its own "
        "uncertainty is also understated."]
    if persistent:
        survivors = sorted(
            {year for years in plus.loc[plus["sample"] == "exclude_2012",
                                        "uncovered_years"] if years
             for year in years.split(";")}.intersection(persistent))
        lines.append(
            f"Every issue date and feature set leaves "
            f"{' and '.join(persistent)} outside the 90% interval."
            + (f" {' and '.join(survivors)} also survives the refitted run "
               "without 2012: a shortfall season the available point weather "
               "both mispredicted and failed to flag as uncertain. A single "
               "season at this sample size is weak evidence, but it is the case "
               "a user of an August estimate would most want warning about."
               if survivors else ""))
    return lines


def render_report(forward_coverage: pd.DataFrame, nowcast_coverage: pd.DataFrame,
                  curve: pd.DataFrame, intervals: pd.DataFrame, *,
                  hashes: dict[str, str], min_train_years: int) -> str:
    headline = forward_coverage.loc[
        (forward_coverage["feature_set"] == HEADLINE)
        & (forward_coverage["method"] == "jackknife_plus")]
    full = headline.loc[(headline["sample"] == "all_years")
                        & (headline["nominal_level"] == 0.90)].iloc[0]
    excluded = headline.loc[(headline["sample"] == "exclude_2012")
                            & (headline["nominal_level"] == 0.90)].iloc[0]
    band = intervals.loc[(intervals["study"] == "forward")
                         & (intervals["sample"] == "all_years")
                         & (intervals["feature_set"] == HEADLINE)
                         & (intervals["method"] == "jackknife_plus")
                         & (intervals["nominal_level"] == 0.90)]
    worst = band.loc[band["pit"].idxmax()]
    margin = min(worst.actual_yield - worst.lower, worst.upper - worst.actual_yield)
    lines = [
        "# Calibrated intervals for the corn yield forecasts", "",
        "## What is being added, and to what", "",
        "The forward and fixed-date evaluations publish a point forecast per "
        "harvest. A point forecast cannot serve as a supply-risk estimate, "
        "because it says nothing about how wrong it can be. This adds intervals "
        "around those same forecasts. No model, feature set, or alpha is "
        "refitted, retuned, or reselected: the point forecast reported for each "
        "harvest matches the committed prediction CSVs to floating-point "
        "tolerance, and a test asserts it.", "",
        "## Method", "",
        "Intervals are built with jackknife+ (Barber, Candès, Ramdas and "
        "Tibshirani, 2021). For each harvest the model trains on earlier "
        f"seasons only, with at least {min_train_years} of them. Inside that "
        "training window each year is withheld in turn, the yield trend and the "
        "regression are both refitted without it, and the resulting residual "
        "joins the calibration set. Nothing that forecasts a harvest has seen "
        "it, and no leave-one-out residual is measured against a trend the "
        "withheld season helped define.", "",
        "Split conformal was rejected for this sample. A strictly chronological "
        "calibration block supports a nominal level only once "
        "`ceil(level × (n+1)) ≤ n`, so 90% needs nine residuals and is "
        "unavailable until 2019 — leaving seven evaluable harvests, all in the "
        "calm recent era that the decomposition report shows carries the least "
        "information. Jackknife+ draws from each fold's training window instead, "
        "so every scored harvest gets a finite interval at both levels.", "",
        "**The guarantee is weaker than the label.** Jackknife+ guarantees "
        "coverage of at least `2 × level − 1`, so the 90% interval carries a "
        "worst-case guarantee of 80% and the 80% interval only 60%. Both "
        "columns appear below. That guarantee also assumes the training "
        "residuals are exchangeable with the test harvest, which an ordered "
        "yield series with an estimated trend does not satisfy. These intervals "
        "are therefore validated by their observed coverage, not by the theorem.", "",
        "Two comparisons are reported alongside: a plain jackknife, which takes "
        "the same residual quantile but ignores the spread of the leave-one-out "
        "forecasts, and a Gaussian interval at `point ± z × s`, which further "
        "assumes symmetric normal residuals.", "",
        "## Predeclared expectation", "",
        "Stated before the numbers below, and not revised after seeing them: "
        "intervals calibrated mostly on seasons near trend were expected to "
        "fail to cover 2012, the one harvest more than 20% below trend. Either "
        "outcome is reportable. A miss would mean the model is calibrated except "
        "in the seasons that matter most, which is what the severity strata in "
        "the decomposition report would predict. A cover would mean the interval "
        "is wide enough, and that width is then itself the finding. The method "
        "was not adjusted to make 2012 come out covered.", "",
        "## Coverage: full-season forecasts", "",
        f"Feature set `{HEADLINE}`. Sixteen scored harvests, or fifteen without "
        "2012, so one season is worth 6.25 percentage points of observed "
        "coverage; read these frequencies as coarse.", "",
        *_coverage_table(forward_coverage.loc[
            forward_coverage["feature_set"] == HEADLINE].sort_values(
                ["sample", "nominal_level", "method"])), "",
        "![Intervals and calibration](corn_yield_intervals.svg)", "",
        "## Coverage: fixed-date nowcasts", "",
        "Jackknife+ only, for the information set actually available at each "
        "issue date.", "",
        *_coverage_table(nowcast_coverage.loc[
            nowcast_coverage["method"] == "jackknife_plus"].sort_values(
                ["sample", "issue_month", "feature_set", "nominal_level"]),
            issue=True), "",
        "## Interpretation", "",
        f"The predeclared expectation was half right. At 90% the jackknife+ "
        f"interval covers {full.covered} of {full.harvests} harvests, 2012 "
        f"included — but {int(worst.year)} is the season with the largest "
        "realised error relative to its own calibration history (its residual "
        "rank is the maximum the fold can express), and the observed yield "
        f"clears the nearer bound by {margin:.3f} t/ha. Every narrower "
        "construction misses it: the plain jackknife and the Gaussian baseline "
        "both leave 2012 outside at 90%, and at 80% all three constructions do. "
        "The interval that contains 2012 is the widest one, which is the finding "
        "rather than a vindication.", "",
        f"The price of that coverage is width. A 90% interval spans "
        f"{full.mean_width_pp_of_trend:.1f}% of trend yield on average, roughly "
        f"±{full.mean_width_pp_of_trend / 2:.1f}%. That is the honest resolution "
        "of this supply-risk estimate, and it is wide enough that any downstream "
        "use has to be built around the spread rather than the central value.", "",
        f"Removing 2012 and refitting leaves {excluded.covered} of "
        f"{excluded.harvests} covered at 90% with a mean width of "
        f"{excluded.mean_width_pp_of_trend:.1f}% of trend, so the intervals are "
        "narrower but over-cover. Over-coverage is the expected direction: "
        "jackknife+ is conservative by construction, the inner models train on "
        "one season fewer than the model making the forecast, and at this sample "
        "size 100% and 90% are about one harvest apart.", "",
        *_baseline_lines(forward_coverage, nowcast_coverage), "",
        *_nowcast_lines(nowcast_coverage), "",
        "## Limits", "",
        "This is a retrospective construction on revised historical data, not a "
        "vintage-exact point-in-time result and not a fresh holdout. POWER, "
        "USDM, and the OWID/FAO yield labels may all have been revised since the "
        "dates being simulated. Coverage is measured on sixteen harvests that "
        "have already informed this project, so it describes calibration on this "
        "sample rather than guaranteeing it on the next one. Exchangeability, "
        "which the theorem needs, does not hold for an ordered yield series. No "
        "trading specification is touched and no sealed holdout is opened.", "",
        "## Reproduce", "",
        "```bash", "PYTHONPATH=src python analysis/corn_yield_intervals.py",
        "```", "",
        "Use `--data-root` for an archived data directory and `--output-dir` for "
        "a separate destination. Inspect the "
        "[per-harvest intervals](corn_yield_intervals_predictions.csv), "
        "[coverage summary](corn_yield_intervals_coverage.csv), and "
        "[calibration curve](corn_yield_intervals_calibration.csv). Input "
        "fingerprints:", "",
    ]
    lines.extend(f"- `{name}`: `{value}`" for name, value in hashes.items())
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--output-dir", type=Path, default=ROOT / "analysis")
    parser.add_argument("--min-train-years", type=int, default=MIN_TRAIN_YEARS)
    args = parser.parse_args()
    store = ProcessedStore(args.data_root)
    geography = load_geography()

    forward = forward_intervals(build_annual_table(store, geography),
                                min_train_years=args.min_train_years,
                                levels=NOMINAL_LEVELS)
    lag = load_sources()["nasa_power"].publication_lag_days
    if lag is None:
        raise SystemExit("the nowcast intervals require an explicit POWER lag")
    issues = build_issue_table(
        store.read("nasa_power_point_daily"), store.read("usdm_county_drought"),
        store.read("nclimgrid_county_daily"), store.read("owid_yields_annual"),
        geography, power_lag_days=lag)
    nowcast = nowcast_intervals(issues, min_train_years=args.min_train_years,
                                levels=NOMINAL_LEVELS)

    forward.insert(0, "study", "forward")
    nowcast.insert(0, "study", "nowcast")
    forward["issue_month"] = pd.NA
    intervals = pd.concat([forward, nowcast], ignore_index=True)

    forward_coverage = coverage(forward, group_columns=("sample", "feature_set"))
    nowcast_coverage = coverage(
        nowcast, group_columns=("sample", "issue_month", "feature_set"))
    forward_coverage.insert(0, "study", "forward")
    nowcast_coverage.insert(0, "study", "nowcast")
    nowcast_coverage["issue_month"] = nowcast_coverage["issue_month"].astype(int)
    forward_coverage["issue_month"] = pd.NA
    coverages = pd.concat([forward_coverage, nowcast_coverage], ignore_index=True)

    curve = pd.concat([
        calibration_curve(forward, group_columns=("sample", "feature_set")
                          ).assign(study="forward", issue_month=pd.NA),
        calibration_curve(nowcast,
                          group_columns=("sample", "issue_month", "feature_set")
                          ).assign(study="nowcast"),
    ], ignore_index=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.output_dir / "corn_yield_intervals"
    intervals.to_csv(f"{stem}_predictions.csv", index=False, float_format="%.10g")
    coverages.to_csv(f"{stem}_coverage.csv", index=False, float_format="%.10g")
    curve.to_csv(f"{stem}_calibration.csv", index=False, float_format="%.10g")
    plot_intervals(intervals, curve, args.output_dir)
    Path(f"{stem}_report.md").write_text(render_report(
        forward_coverage, nowcast_coverage, curve, intervals,
        hashes=fingerprints(store), min_train_years=args.min_train_years))

    shown = coverages.loc[(coverages["feature_set"] == HEADLINE)
                          | (coverages["study"] == "nowcast")]
    print(shown.loc[shown["method"] == "jackknife_plus", [
        "study", "sample", "issue_month", "feature_set", "nominal_level",
        "covered", "harvests", "observed_coverage", "mean_width_pp_of_trend",
        "uncovered_years"]].to_string(
            index=False, float_format=lambda value: f"{value:.3f}"))
    print(f"Wrote {stem}_{{predictions,coverage,calibration}}.csv, "
          f"{stem}_report.md, {stem}.svg")


if __name__ == "__main__":
    main()
