"""What the corn supply-risk estimate is worth to someone provisioning against it.

A price-free newsvendor decision: a buyer sets a buffer against a yield
shortfall, pays an asymmetric penalty for under-provisioning, and is scored
against what the harvest delivered. No futures price enters, so this cannot
restate the project's rejected trading signal as a profit. The question is only
whether the conditional distribution beats the free climatological one.
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
from pipeline.yield_decisions import (  # noqa: E402
    CVAR_LEVEL, PENALTY_RATIOS, bootstrap_difference, decide, score_decisions,
    severity_strata, spread_diagnostic,
)
from pipeline.yield_intervals import fold_residuals  # noqa: E402
from pipeline.yield_nowcast import (  # noqa: E402
    ISSUE_MONTHS, NOWCAST_FEATURE_SETS, build_issue_table,
)
from pipeline.yield_validation import (  # noqa: E402
    FEATURE_SETS, INPUT_TABLES, MIN_TRAIN_YEARS, build_annual_table,
)

# The decision a buyer can actually take, and the bound better data would give.
FEASIBLE_ISSUE = 8
HEADLINE_FEATURE_SET = "points_drought"
INFEASIBLE = {"counties_drought": FEATURE_SETS["counties_drought"]}
SAMPLES = ("all_years", "exclude_2012")
STRATEGY_LABELS = {
    "no_buffer": "No buffer",
    "climatology": "Climatology (no weather data)",
    "climatology_scaled": "Climatology widened to the model's spread",
    "model": "Conditional model distribution",
    "model_point_only": "Model centre, climatology spread",
}
COLORS = {"climatology": "#555555", "climatology_scaled": "#b58900",
          "model": "#2977b5", "model_point_only": "#087f70",
          "no_buffer": "#b13b42"}


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


def nowcast_cohort(issues: pd.DataFrame) -> list[int]:
    """The nowcast's own common-year cohort, so the issue dates stay comparable."""
    features = sorted({column for names in NOWCAST_FEATURE_SETS.values()
                       for column in names})
    eligible = issues.dropna(subset=[*features, "yield_t_per_ha"]).groupby(
        "year")["issue_month"].agg(set)
    return eligible.index[eligible.map(
        lambda months: months == set(ISSUE_MONTHS))].tolist()


def collect(issues: pd.DataFrame, annual: pd.DataFrame, *,
            min_train_years: int, penalty_ratios) -> dict[str, pd.DataFrame]:
    """Score every information set and sample, keeping feasibility explicit."""
    common = nowcast_cohort(issues)
    decisions, spreads = [], []
    for sample in SAMPLES:
        years = [year for year in common
                 if sample == "all_years" or year != 2012]
        for month in ISSUE_MONTHS:
            frame = issues.loc[issues["year"].isin(years)
                               & (issues["issue_month"] == month)]
            folds = list(fold_residuals(frame.set_index("year"),
                                        NOWCAST_FEATURE_SETS,
                                        min_train_years=min_train_years))
            if not folds:
                raise SystemExit(f"{sample}/{month:02d}-01: too few seasons")
            frames = [decide(folds, penalty_ratios=penalty_ratios),
                      spread_diagnostic(folds)]
            for target, built in zip((decisions, spreads), frames):
                built.insert(0, "issue_month", month)
                built.insert(0, "information_set", "nowcast")
                built.insert(0, "feasible", True)
                built.insert(0, "sample", sample)
                target.append(built)
        # The full-season county set is not published in time for an August
        # decision; it is scored only as a bound on what better data would buy.
        table = annual if sample == "all_years" else annual.drop(index=2012,
                                                                 errors="ignore")
        folds = list(fold_residuals(table, INFEASIBLE,
                                    min_train_years=min_train_years))
        frames = [decide(folds, penalty_ratios=penalty_ratios),
                  spread_diagnostic(folds)]
        for target, built in zip((decisions, spreads), frames):
            built.insert(0, "issue_month", pd.NA)
            built.insert(0, "information_set", "full_season")
            built.insert(0, "feasible", False)
            built.insert(0, "sample", sample)
            target.append(built)

    keys = ("sample", "information_set", "issue_month", "feature_set")
    everything = pd.concat(decisions, ignore_index=True)
    return {
        "decisions": everything,
        "spreads": pd.concat(spreads, ignore_index=True),
        "scores": score_decisions(everything, group_columns=keys),
        "bootstrap": bootstrap_difference(everything, group_columns=keys),
        "strata": severity_strata(everything, group_columns=keys),
    }


def _feasible(frame: pd.DataFrame, sample: str) -> pd.DataFrame:
    return frame.loc[(frame["sample"] == sample)
                     & (frame["information_set"] == "nowcast")
                     & (frame["issue_month"] == FEASIBLE_ISSUE)
                     & (frame["feature_set"] == HEADLINE_FEATURE_SET)]


def plot_decisions(scores: pd.DataFrame, decisions: pd.DataFrame,
                   output_dir: Path) -> None:
    """Cost by strategy in both samples, and the buffer against the harvest."""
    with plt.rc_context({"svg.fonttype": "none",
                         "svg.hashsalt": "corn-yield-decisions-v1"}):
        fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.4))
        order = ["no_buffer", "climatology", "climatology_scaled",
                 "model_point_only", "model"]
        width = 0.38
        for offset, (sample, label) in enumerate(
                [("all_years", "All years"),
                 ("exclude_2012", "2012 excluded")]):
            frame = _feasible(scores, sample)
            frame = frame.loc[frame["penalty_ratio"] == 4.0].set_index("strategy")
            values = [frame.loc[name, "total_cost"] if name in frame.index else 0.0
                      for name in order]
            axes[0].bar([index + offset * width for index in range(len(order))],
                        values, width=width, label=label,
                        color="#2977b5" if sample == "all_years" else "#9ecae1")
        axes[0].set_xticks([index + width / 2 for index in range(len(order))])
        axes[0].set_xticklabels([STRATEGY_LABELS[name].replace(" (no weather data)", "")
                                 for name in order], rotation=25, ha="right",
                                fontsize=7)
        axes[0].set_ylabel("Total provisioning cost (holding-cost units)")
        axes[0].set_title("Cost of each rule at a 4:1 penalty", fontsize=11)
        axes[0].legend(frameon=False, fontsize=8)

        band = _feasible(decisions, "all_years")
        band = band.loc[band["penalty_ratio"] == 4.0]
        realised = band.drop_duplicates("year").sort_values("year")
        axes[1].plot(realised["year"], realised["actual_shortfall"], marker="s",
                     markersize=4, color="#222222", linestyle="none",
                     label="Realised shortfall")
        for strategy in ("climatology", "model"):
            line = band.loc[band["strategy"] == strategy].sort_values("year")
            axes[1].step(line["year"], line["buffer"], where="mid",
                         color=COLORS[strategy],
                         label=STRATEGY_LABELS[strategy].replace(
                             " (no weather data)", ""))
        axes[1].set_ylabel("Shortfall below trend (t/ha)")
        axes[1].set_xlabel("Harvest year")
        axes[1].set_title("Buffer chosen, against what arrived", fontsize=11)
        axes[1].legend(frameon=False, fontsize=8)
        for ax in axes:
            ax.grid(axis="y", alpha=0.2)
            ax.spines[["top", "right"]].set_visible(False)
        fig.suptitle("Provisioning against a corn shortfall with an August 1 "
                     "estimate", fontsize=13)
        fig.text(0.5, 0.015, "No futures price enters this decision; the loss "
                 "depends on the harvest alone", ha="center", fontsize=8,
                 color="#555555")
        fig.tight_layout(rect=(0, 0.05, 1, 0.95))
        _save_svg(fig, output_dir / "corn_yield_decisions.svg")
        plt.close(fig)


def _cost_table(scores: pd.DataFrame) -> list[str]:
    lines = [
        "| Sample | Penalty | Strategy | Total cost | Mean | "
        f"CVaR ({CVAR_LEVEL:.0%}) | Mean buffer | Years short | vs climatology |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in scores.itertuples(index=False):
        reduction = ("—" if pd.isna(row.cost_reduction)
                     else f"{row.cost_reduction:+.1%}")
        lines.append(
            f"| {row.sample} | {row.penalty_ratio:.0f}:1 | "
            f"{STRATEGY_LABELS.get(row.strategy, row.strategy)} | "
            f"{row.total_cost:.2f} | {row.mean_cost:.3f} | {row.cvar_cost:.3f} | "
            f"{row.mean_buffer:.3f} | {row.years_short} | {reduction} |")
    return lines


def _verdict_lines(boot: pd.DataFrame, boot_ex: pd.DataFrame,
                   strata: pd.DataFrame) -> list[str]:
    """State what the resampling supports, and where the saving comes from."""
    lines = []
    positive = boot.loc[boot["bootstrap_p025"] > 0, "penalty_ratio"].tolist()
    straddle = boot.loc[boot["bootstrap_p025"] <= 0, "penalty_ratio"].tolist()
    if positive or straddle:
        claim = []
        if positive:
            claim.append(
                "excludes zero only at the "
                + ", ".join(f"{ratio:.0f}:1" for ratio in positive)
                + " asymmetry")
        if straddle:
            claim.append(
                "straddles zero at "
                + ", ".join(f"{ratio:.0f}:1" for ratio in straddle))
        lines.append(
            "Read against the resampling, the all-years saving "
            + " and ".join(claim)
            + ". The one asymmetry where it is cleanly positive is the most "
              "risk-averse one, which is coherent rather than convenient: 2012 "
              "is precisely the season a buyer with an 8:1 penalty is afraid of, "
              "so that is where a tail-sensitive loss should find value.")
    if not boot_ex.empty:
        worst = boot_ex.loc[boot_ex["probability_positive"].idxmin()]
        best = boot_ex.loc[boot_ex["probability_positive"].idxmax()]
        lines.append(
            "Without 2012 every bootstrap interval straddles zero, with the "
            f"probability of a saving running from {worst.probability_positive:.0%} "
            f"at {worst.penalty_ratio:.0f}:1 to {best.probability_positive:.0%} at "
            f"{best.penalty_ratio:.0f}:1 and the harvests improved barely "
            "outnumbering those made worse. On fifteen seasons that is not a "
            "detectable advantage, and it should not be quoted as one.")
    regime = strata.loc[(strata["information_set"] == "nowcast")
                        & (strata["issue_month"] == FEASIBLE_ISSUE)
                        & (strata["feature_set"] == HEADLINE_FEATURE_SET)
                        & (strata["sample"] == "exclude_2012")
                        & (strata["penalty_ratio"] == 4.0)
                        & (strata["strategy"] == "model")].set_index("regime")
    if {"shortfall", "at_or_above_trend"}.issubset(regime.index):
        short = float(regime.loc["shortfall", "total_cost_vs_baseline"])
        calm = float(regime.loc["at_or_above_trend", "total_cost_vs_baseline"])
        lines.append(
            "The regime split shows the shape of it. In the refitted run "
            f"without 2012, at a 4:1 penalty, the model saves {short:+.2f} cost "
            f"units across the seasons that did fall short and loses "
            f"{abs(calm):.2f} across those at or above trend, where its buffer "
            "was provisioned against a shortfall that never came. It behaves "
            "like an insurance instrument: it pays in bad years and charges a "
            f"premium in good ones, netting {short + calm:+.2f} over the fifteen "
            "seasons. That net is positive, and it is also inside the bootstrap "
            "interval above — a point estimate worth reporting, not an effect "
            "this sample can establish.")
    return lines


def render_report(bundle: dict[str, pd.DataFrame], *, hashes: dict[str, str],
                  min_train_years: int, penalty_ratios) -> str:
    scores, boot, spreads = bundle["scores"], bundle["bootstrap"], bundle["spreads"]
    feasible = _feasible(scores, "all_years")
    feasible_ex = _feasible(scores, "exclude_2012")

    def saving(frame: pd.DataFrame, ratio: float) -> float:
        row = frame.loc[(frame["penalty_ratio"] == ratio)
                        & (frame["strategy"] == "model")]
        return float(row["cost_reduction"].iloc[0]) if len(row) else float("nan")

    def scaled_saving(frame: pd.DataFrame, ratio: float) -> float:
        subset = frame.loc[frame["penalty_ratio"] == ratio].set_index("strategy")
        if not {"model", "climatology_scaled"}.issubset(subset.index):
            return float("nan")
        return 1.0 - (subset.loc["model", "total_cost"]
                      / subset.loc["climatology_scaled", "total_cost"])

    ratio_mean = float(_feasible(spreads, "all_years")["spread_ratio"].mean())
    ratio_mean_ex = float(_feasible(spreads, "exclude_2012")["spread_ratio"].mean())
    boot_feasible = _feasible(boot, "all_years")
    boot_ex = _feasible(boot, "exclude_2012")
    lines = [
        "# Using the corn supply-risk estimate to provision against a shortfall", "",
        "## Who this is for", "",
        "The project's two headline findings only conflict for a speculator. "
        "Public weather measures a corn supply shock, and the same information "
        "produces no futures-return edge. A grain buyer, feed mill, ethanol "
        "plant, or agricultural lender already holds that exposure whether or "
        "not they forecast it, and what a better forecast buys them is a lower "
        "chance of being caught short — not a trading profit. That benefit "
        "survives in a perfectly efficient market, so it is the use this "
        "project's evidence can actually support.", "",
        "## The decision, and why no price appears in it", "",
        "A demonstration that let its user size a futures position from the "
        "model would be reimporting the directional signal that failed every "
        "validation here, and reporting that failure as a profit. **No price "
        "enters this analysis.** The user provisions a physical buffer against "
        "a shortfall in the crop they must handle; the loss depends on the "
        "harvest alone.", "",
        "Each August the user commits a buffer against the shortfall below that "
        "fold's own trend forecast. Provisioning less than the shortfall costs "
        "`penalty` per tonne per hectare short; provisioning more costs one unit "
        "per tonne carried. Under that loss the optimal buffer is the "
        "`penalty / (penalty + 1)` quantile of the predictive shortfall "
        "distribution, so a better distribution shows up directly as a better "
        "buffer. **The asymmetry is a stated preference, not an estimate**, and "
        "results are reported at "
        + ", ".join(f"{ratio:.0f}:1" for ratio in penalty_ratios)
        + ". Costs are in units of the holding cost; converting to currency "
          "multiplies every strategy by the same constant and changes no "
          "ranking.", "",
        f"Every buffer uses only earlier harvests, with at least "
        f"{min_train_years} of them, and the predictive distributions come from "
        "the same chronological folds as the calibrated intervals.", "",
        "## The strategies, and the control that matters", "",
        "`climatology` is what the user can do with no weather data at all: the "
        "training years' own deviations from trend. `model` uses the conditional "
        "conformal distribution. `model_point_only` keeps the model's centre but "
        "borrows climatology's spread, separating knowing where the distribution "
        "sits from knowing how wide it is.", "",
        "`climatology_scaled` is the control this design needs. An asymmetric "
        "loss rewards a wider predictive distribution for its own sake, because "
        "over-provisioning is the cheap error, so a model that is merely more "
        "uncertain than climatology would post a saving that has nothing to do "
        "with information. This strategy hands the no-weather user the model's "
        "uncertainty without the model's information. A saving that is really "
        "just conservatism appears here too.", "",
        "## Predeclared expectation", "",
        "Stated before the numbers and not revised after them: the decomposition "
        "report found the model's skill concentrated in the seasons furthest "
        "from trend, and the interval report found its coverage of 2012 marginal "
        "and its August calibration understated. The saving was therefore "
        "expected to concentrate in 2012 and to shrink substantially in the "
        "refitted run without it. A saving that survived 2012's removal would be "
        "the stronger result; one that did not would mean the estimate is not "
        "yet decision-grade outside catastrophic seasons.", "",
        "## Results: the August 1 decision", "",
        f"Information set `{HEADLINE_FEATURE_SET}` at August 1 — the only one of "
        "these a buyer could actually use. Sixteen harvests, or fifteen without "
        "2012.", "",
        *_cost_table(pd.concat([feasible, feasible_ex]).sort_values(
            ["sample", "penalty_ratio", "strategy"])), "",
        "![Decision costs and buffers](corn_yield_decisions.svg)", "",
        "## Is the saving just a wider buffer?", "",
        f"No. The model's residual spread averages {ratio_mean:.2f} times the "
        f"climatological spread across all years and {ratio_mean_ex:.2f} without "
        "2012, so it is the *less* uncertain of the two and cannot be buying its "
        "advantage through conservatism. Widening climatology to match the "
        "model's spread does help a little, but the model still beats that "
        "control by "
        + ", ".join(f"{scaled_saving(feasible, ratio):+.1%} at "
                    f"{ratio:.0f}:1" for ratio in penalty_ratios)
        + " across all years, falling to "
        + ", ".join(f"{scaled_saving(feasible_ex, ratio):+.1%}"
                    for ratio in penalty_ratios)
        + " respectively once 2012 is removed.", "",
        "## Uncertainty on the saving", "",
        "Whole harvests are resampled, because a crop year is the independent "
        "observation and sixteen of them are too few for a point estimate to "
        "stand alone. A positive value is a saving against climatology.", "",
        "| Sample | Penalty | Mean saving per harvest | 2.5% | Median | 97.5% | "
        "P(saving > 0) | Harvests better | Harvests worse |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in pd.concat([boot_feasible, boot_ex]).sort_values(
            ["sample", "penalty_ratio"]).itertuples(index=False):
        lines.append(
            f"| {row.sample} | {row.penalty_ratio:.0f}:1 | "
            f"{row.mean_saving_per_harvest:+.3f} | {row.bootstrap_p025:+.3f} | "
            f"{row.bootstrap_median:+.3f} | {row.bootstrap_p975:+.3f} | "
            f"{row.probability_positive:.1%} | {row.harvests_improved} | "
            f"{row.harvests_worsened} |")
    lines += [
        "", "## Interpretation", "",
        "The predeclared expectation held. Across all years the conditional "
        "distribution cuts provisioning cost against the no-weather baseline by "
        + ", ".join(f"{saving(feasible, ratio):.0%} at {ratio:.0f}:1"
                    for ratio in penalty_ratios)
        + ". Once 2012 is removed from training and evaluation and every fold is "
          "refitted, the same comparison falls to "
        + ", ".join(f"{saving(feasible_ex, ratio):.0%}"
                    for ratio in penalty_ratios)
        + " respectively.", "",
        "So the decision value behaves like the R², the interval coverage, and "
        "the severity strata before it: most of it is 2012. What remains after "
        "2012 is removed is a double-digit point estimate rather than nothing, "
        "which is a better outcome than those earlier sections showed — but as "
        "the resampling below makes clear, fifteen harvests cannot separate it "
        "from zero.", "",
        *_verdict_lines(boot_feasible, boot_ex, bundle["strata"]), "",
        "The practical statement a commodity analyst can take from this project "
        "is therefore narrow and, within its limits, usable. An August estimate "
        "built from free public weather is worth having mainly as insurance "
        "against a 2012: it sizes a buffer better than climatology when the "
        "crop is genuinely failing, and adds little when it is not. It is not a "
        "source of edge against the futures market, and the supporting interval "
        "is wide enough — around ±8.7% of trend yield for the full season, more "
        "in season — that any provisioning decision has to be built around the "
        "spread rather than the central value.", "",
        "The full-season rows in the committed CSVs are a bound rather than a "
        "decision. County weather is not published in time to inform an August "
        "commitment, as the nowcast report's availability audit established, so "
        "that information set measures what better-timed data would be worth "
        "and not what this archive can deliver.", "",
        "## Limits", "",
        "The loss function is a stated preference. A real buyer's penalty "
        "asymmetry, storage economics, basis exposure, and contractual "
        "obligations are all absent, and only the penalty ratio is varied here. "
        "The quantity is national yield per hectare rather than any particular "
        "firm's volume, so the costs are per-unit and notional. Harvests are "
        "revised historical values, not the vintages a decision would have faced "
        "at the time, and all sixteen have already informed this project, so "
        "this is a retrospective demonstration rather than a fresh holdout. No "
        "trading specification is touched and no sealed holdout is opened.", "",
        "## Reproduce", "",
        "```bash", "PYTHONPATH=src python analysis/corn_yield_decisions.py",
        "```", "",
        "Use `--data-root` for an archived data directory, `--output-dir` for a "
        "separate destination, and `--penalty-ratios` to change the asymmetries. "
        "Inspect the [per-harvest decisions](corn_yield_decisions_decisions.csv), "
        "[strategy scores](corn_yield_decisions_scores.csv), "
        "[bootstrap](corn_yield_decisions_bootstrap.csv), "
        "[regime strata](corn_yield_decisions_strata.csv), and "
        "[spread diagnostic](corn_yield_decisions_spreads.csv). Input "
        "fingerprints:", "",
    ]
    lines.extend(f"- `{name}`: `{value}`" for name, value in hashes.items())
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--output-dir", type=Path, default=ROOT / "analysis")
    parser.add_argument("--min-train-years", type=int, default=MIN_TRAIN_YEARS)
    parser.add_argument("--penalty-ratios", type=float, nargs="+",
                        default=list(PENALTY_RATIOS))
    args = parser.parse_args()
    store = ProcessedStore(args.data_root)
    geography = load_geography()
    lag = load_sources()["nasa_power"].publication_lag_days
    if lag is None:
        raise SystemExit("the August decision requires an explicit POWER lag")

    bundle = collect(
        build_issue_table(
            store.read("nasa_power_point_daily"),
            store.read("usdm_county_drought"),
            store.read("nclimgrid_county_daily"),
            store.read("owid_yields_annual"), geography, power_lag_days=lag),
        build_annual_table(store, geography),
        min_train_years=args.min_train_years,
        penalty_ratios=tuple(args.penalty_ratios))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.output_dir / "corn_yield_decisions"
    for name, frame in bundle.items():
        frame.to_csv(f"{stem}_{name}.csv", index=False, float_format="%.10g")
    plot_decisions(bundle["scores"], bundle["decisions"], args.output_dir)
    Path(f"{stem}_report.md").write_text(render_report(
        bundle, hashes=fingerprints(store),
        min_train_years=args.min_train_years,
        penalty_ratios=tuple(args.penalty_ratios)))

    shown = pd.concat([_feasible(bundle["scores"], sample)
                       for sample in SAMPLES])
    print(shown[["sample", "penalty_ratio", "strategy", "total_cost",
                 "cvar_cost", "years_short", "cost_reduction"]].to_string(
        index=False, float_format=lambda value: f"{value:.3f}"))
    print(f"Wrote {stem}_{{decisions,spreads,scores,bootstrap,strata}}.csv, "
          f"{stem}_report.md, {stem}.svg")


if __name__ == "__main__":
    main()
