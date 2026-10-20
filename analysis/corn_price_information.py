"""Why the corn weather signal does not trade: already priced, and undetectable.

Two tests the return backtests cannot perform on themselves. First an
information horse race on the physical harvest, asking whether the published
nowcast still explains the yield once the contemporaneous new-crop futures price
is in the model. Then a power and cost budget, comparing the edge a strategy
needs to pay its costs with the smallest edge this sample could have detected.
Neither fits or refits a model; the forecasts raced here are the committed ones.
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

from pipeline.price_information import (  # noqa: E402
    ALPHA, COST_BPS, POWER, continuous_signal, december_contract_signal,
    horse_race, power_budget, return_moments,
)
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

HEADLINE_FEATURE_SET = "points_drought"
FEASIBLE_ISSUE = 8
# The dated new-crop contract is the right instrument; the continuous series is
# the sample extension, and the two are reported separately rather than pooled.
PRIMARY_SOURCE = "december_contract"
SOURCE_LABELS = {"december_contract": "New-crop December contract (dated)",
                 "continuous_front": "Continuous front month"}
INPUT_TABLES = ("yahoo_prices_daily", "futures_contracts")
PREDICTIONS = "corn_yield_nowcast_predictions.csv"


def fingerprints(store: ProcessedStore, extra: dict[str, Path]) -> dict[str, str]:
    paths = {name: store.path(name) for name in INPUT_TABLES}
    paths.update(extra)
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


def plot_information(race: pd.DataFrame, budget: pd.DataFrame,
                     output_dir: Path) -> None:
    """Shared versus incremental explanation, and the detectability gap."""
    with plt.rc_context({"svg.fonttype": "none",
                         "svg.hashsalt": "corn-price-information-v1"}):
        fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.4))
        frame = race.loc[(race["price_source"] == PRIMARY_SOURCE)
                         & (race["issue_month"] == FEASIBLE_ISSUE)
                         & (race["feature_set"] == HEADLINE_FEATURE_SET)]
        labels, position = [], 0
        for sample in ("all_years", "exclude_2012"):
            row = frame.loc[frame["sample"] == sample]
            if row.empty:
                continue
            row = row.iloc[0]
            axes[0].bar(position, row["price_r2"], color="#555555",
                        label="Price alone" if position == 0 else None)
            axes[0].bar(position, row["incremental_r2"],
                        bottom=row["price_r2"], color="#2977b5",
                        label="Nowcast adds" if position == 0 else None)
            axes[0].plot([position], [row["nowcast_r2"]], marker="D",
                         color="#b13b42", markersize=7, linestyle="none",
                         label="Nowcast alone" if position == 0 else None)
            labels.append("All years" if sample == "all_years"
                          else "2012 excluded")
            position += 1
        axes[0].set_xticks(range(len(labels)))
        axes[0].set_xticklabels(labels, fontsize=9)
        axes[0].set_ylabel("Share of harvest deviation explained (R²)")
        axes[0].set_title("Who knew the harvest: price or weather model?",
                          fontsize=11)
        axes[0].legend(frameon=False, fontsize=7)

        horizon = budget.loc[budget["horizon_days"] == 5].sort_values("cost_bps")
        if not horizon.empty:
            axes[1].bar([str(int(cost)) for cost in horizon["cost_bps"]],
                        horizon["break_even_r2"], color="#087f70",
                        label="R² needed to beat costs")
            axes[1].axhline(float(horizon["detectable_r2"].iloc[0]),
                            color="#b13b42", linestyle="--",
                            label="Smallest R² this sample could detect")
            axes[1].set_yscale("log")
            axes[1].set_xlabel("Round-trip cost (basis points)")
            axes[1].set_ylabel("Five-day return R² (log scale)")
            axes[1].set_title("Profitable but invisible", fontsize=11)
            axes[1].legend(frameon=False, fontsize=7)
        for ax in axes:
            ax.grid(axis="y", alpha=0.2)
            ax.spines[["top", "right"]].set_visible(False)
        fig.suptitle("Why the climate signal does not become a trade",
                     fontsize=13)
        fig.text(0.5, 0.015, "Left: the harvest regressed on both. Right: a "
                 "null result cannot rule out an edge below the dashed line.",
                 ha="center", fontsize=8, color="#555555")
        fig.tight_layout(rect=(0, 0.05, 1, 0.95))
        _save_svg(fig, output_dir / "corn_price_information.svg")
        plt.close(fig)


def _race_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| Sample | Issue | Price source | Harvests | Nowcast R² | Price R² | "
        "Joint R² | Nowcast adds | Drop-one range | Nowcast β kept | "
        "β excludes 0 |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for row in frame.itertuples(index=False):
        kept = ("—" if pd.isna(row.nowcast_beta_retained)
                else f"{row.nowcast_beta_retained:.0%}")
        lines.append(
            f"| {row.sample} | {int(row.issue_month):02d}-01 | "
            f"{SOURCE_LABELS.get(row.price_source, row.price_source)} | "
            f"{row.harvests} | {row.nowcast_r2:+.3f} | {row.price_r2:+.3f} | "
            f"{row.joint_r2:+.3f} | {row.incremental_r2:+.3f} | "
            f"{row.incremental_r2_min_drop_one:+.3f} to "
            f"{row.incremental_r2_max_drop_one:+.3f} | {kept} | "
            f"{'yes' if row.nowcast_beta_excludes_zero else 'no'} |")
    return lines


def _budget_table(budget: pd.DataFrame) -> list[str]:
    lines = [
        "| Horizon | Independent draws | Cost | Break-even R² | "
        "Detectable R² | Draws needed | Shortfall | Blind? |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in budget.itertuples(index=False):
        lines.append(
            f"| {row.horizon_days}-day | {row.independent_observations} | "
            f"{row.cost_bps:.0f} bps | {row.break_even_r2:.5f} | "
            f"{row.detectable_r2:.5f} | {row.observations_needed:,.0f} | "
            f"{row.observation_shortfall:.1f}× | "
            f"{'**yes**' if row.blind_to_profitable_signal else 'no'} |")
    return lines


def render_report(race: pd.DataFrame, moments: pd.DataFrame,
                  budget: pd.DataFrame, *, hashes: dict[str, str]) -> str:
    primary = race.loc[(race["price_source"] == PRIMARY_SOURCE)
                       & (race["issue_month"] == FEASIBLE_ISSUE)
                       & (race["feature_set"] == HEADLINE_FEATURE_SET)]
    full = primary.loc[primary["sample"] == "all_years"]
    excluded = primary.loc[primary["sample"] == "exclude_2012"]
    secondary = race.loc[(race["price_source"] == "continuous_front")
                         & (race["issue_month"] == FEASIBLE_ISSUE)
                         & (race["feature_set"] == HEADLINE_FEATURE_SET)
                         & (race["sample"] == "all_years")]
    five = budget.loc[budget["horizon_days"] == 5]
    blind = five.loc[five["blind_to_profitable_signal"]]
    lines = [
        "# Why the climate signal does not become a trade", "",
        "Every return test in this repository reports approximately zero or "
        "negative out-of-sample skill. That result is consistent with several "
        "different worlds, and the backtests cannot tell them apart on their "
        "own. Two of them are separated here: the information was already in "
        "the price, and the sample was too small to see an edge worth having.", "",
        "## Test one: was the weather already in the price?", "",
        "The race is run on the **harvest**, not on returns. Regressing returns "
        "on a signal asks whether the signal predicts the price, which every "
        "backtest here already answered. Regressing the realised yield "
        "deviation on the published nowcast, on the contemporaneous new-crop "
        "price change, and on both together asks who knew what — the question "
        "that separates an efficient market from a useless model.", "",
        "The price signal is the log change in the new-crop December contract "
        "from April 1 to the issue date: how much the market marked that "
        "harvest up while it was being grown. The dated contract is the right "
        "instrument, and the continuous front month is reported alongside it "
        "only because it reaches further back. Nothing is refit: the forecasts "
        "are read from the committed nowcast prediction file.", "",
        *_race_table(race.loc[
            (race["issue_month"] == FEASIBLE_ISSUE)
            & (race["feature_set"] == HEADLINE_FEATURE_SET)].sort_values(
                ["price_source", "sample"])), "",
        "![Information and power](corn_price_information.svg)", "",
    ]
    if not full.empty and not excluded.empty:
        full, excluded = full.iloc[0], excluded.iloc[0]
        lines += [
            "**Largely, yes.** Across all years the nowcast explains "
            f"{full.nowcast_r2:.3f} of the harvest deviation and the December "
            f"price {full.price_r2:.3f}; together they reach {full.joint_r2:.3f}, "
            "so they are mostly explaining the same thing. What the nowcast "
            f"adds on top of the price is {full.incremental_r2:+.3f}, its "
            f"coefficient keeps {full.nowcast_beta_retained:.0%} of its "
            "standalone value once the price is included, and the whole-harvest "
            "bootstrap on that coefficient "
            + ("excludes" if full.nowcast_beta_excludes_zero else "does not exclude")
            + " zero.", "",
            "Removing 2012 and refitting leaves the nowcast explaining "
            f"{excluded.nowcast_r2:+.3f} against the price's "
            f"{excluded.price_r2:+.3f}, with an incremental contribution of "
            f"{excluded.incremental_r2:+.3f}. On the thirteen remaining harvests "
            "the weather model adds essentially nothing to what the futures "
            "price already said, and the price retains the larger share of what "
            "little is explained.", "",
        ]
    if not secondary.empty:
        secondary = secondary.iloc[0]
        lines += [
            "The two price sources disagree, and the disagreement is "
            "interpretable rather than troubling. On the continuous front month "
            f"the nowcast's incremental contribution is {secondary.incremental_r2:+.3f} "
            "and its coefficient does survive the bootstrap. But in August the "
            "front month is a mixture of old and new crop carrying roll "
            "artefacts, so it is a worse statement of new-crop expectations and "
            "leaves more room for the weather model to look informative. The "
            "cleaner instrument is the one that subsumes the signal, which is "
            "the direction an efficiency argument predicts.", "",
        ]
    lines += [
        "## Test two: could this sample have seen a tradeable edge?", "",
        "A strategy needs only a small edge to beat costs, and a small edge "
        "needs a large sample to separate from zero. Those two thresholds are "
        "different quantities and are computed separately here. Break-even "
        "assumes a position taking the sign of the forecast on jointly normal "
        "draws, where expected gross profit per trade is "
        "`sd × sqrt(2/π) × correlation`. Detection uses the conventional "
        f"{ALPHA:.0%} two-sided test at {POWER:.0%} power. Only non-overlapping "
        "returns are counted, because overlapping windows are not independent "
        "observations.", "",
        "### Growing-season return dispersion", "",
        "| Horizon | SD | Overlapping | Independent | Window |",
        "|---|---:|---:|---:|---|",
    ]
    for row in moments.itertuples(index=False):
        lines.append(
            f"| {row.horizon_days}-day | {row.return_sd:.4f} | "
            f"{row.overlapping_observations} | {row.independent_observations} | "
            f"{row.first_date} to {row.last_date} |")
    lines += [
        "", "### What pays, against what could be measured", "",
        *_budget_table(budget.sort_values(["horizon_days", "cost_bps"])), "",
    ]
    if not blind.empty:
        worst = blind.loc[blind["cost_bps"].idxmin()]
        lines += [
            f"At five days and {worst.cost_bps:.0f} basis points a sign rule "
            f"breaks even at an R² of {worst.break_even_r2:.5f}, while the "
            f"{worst.independent_observations} independent growing-season "
            "returns in this archive can only distinguish an R² of "
            f"{worst.detectable_r2:.5f} from zero — a factor of "
            f"{worst.detectable_r2 / worst.break_even_r2:.0f}. Detecting the "
            f"break-even edge would need about "
            f"{worst.observations_needed:,.0f} independent observations, "
            f"{worst.observation_shortfall:.0f} times what exists.", "",
            "**So there is a band of signal strengths that would be profitable "
            "and invisible**, and a null result cannot rule them out. Every "
            "cost level marked *yes* above sits in that band.", "",
        ]
    lines += [
        "## What this does and does not excuse", "",
        "The power budget is a statement about the experiment's resolution, not "
        "a defence of its result, and it would be a misreading to take it as "
        "one. The backtests in this repository did not merely fail to reach "
        "significance. The locked USDA Crop Progress model lost 52.7% after "
        "costs, the sealed GEFS holdout lost 7.3% with −0.98 Sharpe, and the "
        "frozen signal's next-open replay returned −7.1%. A true edge too small "
        "to detect produces results scattered near zero; it does not produce "
        "losses of that size. Those outcomes are evidence against a positive "
        "edge in their own right, and they are consistent with the execution "
        "attribution's finding that real per-contract costs removed far more "
        "than the ten-basis-point bridge assumed.", "",
        "Read together, the two tests give a specific answer to why the climate "
        "signal does not translate into profitable trades. The information is "
        "largely public and already impounded in the new-crop price by the time "
        "a weather model can compute it, so there is little left to trade. "
        "Whatever remains is smaller than this sample can measure at realistic "
        "costs, so no amount of further modelling on this archive could "
        "establish it. And the executed attempts lost money rather than "
        "hovering near zero, which is the strongest of the three signs.", "",
        "## Limits", "",
        "The horse race has fourteen harvests on the dated contract and "
        "sixteen on the continuous series, so every R² in it is a small-sample "
        "quantity and the all-years figures are visibly carried by 2012 — the "
        "drop-one columns show by how much. A four-month price change is one "
        "summary of market expectations among many; options-implied "
        "distributions and the old-crop/new-crop spread are not tested. The "
        "break-even arithmetic assumes normality and an every-period sign rule, "
        "while the project's models hold sometimes, which trades breadth "
        "against cost. The power calculation treats non-overlapping returns as "
        "independent, which ignores remaining seasonality and volatility "
        "clustering and so is optimistic about the sample. No model is refit, no "
        "trading specification is changed, and no sealed holdout is opened.", "",
        "## Reproduce", "",
        "```bash", "PYTHONPATH=src python analysis/corn_price_information.py",
        "```", "",
        "Requires the nowcast evaluation to have been run first. Use "
        "`--data-root`, `--output-dir`, and `--cost-bps` to vary the "
        "assumptions. Inspect the "
        "[horse race](corn_price_information_race.csv), "
        "[price signals](corn_price_information_signals.csv), "
        "[return moments](corn_price_information_moments.csv), and "
        "[power budget](corn_price_information_budget.csv). Input "
        "fingerprints:", "",
    ]
    lines.extend(f"- `{name}`: `{value}`" for name, value in hashes.items())
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--input-dir", type=Path, default=ROOT / "analysis")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "analysis")
    parser.add_argument("--cost-bps", type=float, nargs="+",
                        default=list(COST_BPS))
    args = parser.parse_args()
    store = ProcessedStore(args.data_root)

    predictions_path = args.input_dir / PREDICTIONS
    if not predictions_path.exists():
        raise SystemExit(f"missing {predictions_path}; run the nowcast first")
    predictions = pd.read_csv(predictions_path)
    predictions = predictions.loc[predictions["feature_set"] != "trend_only"]

    signals = pd.concat([
        december_contract_signal(store.read("futures_contracts")),
        continuous_signal(store.read("yahoo_prices_daily")),
    ], ignore_index=True)
    race = horse_race(predictions, signals)
    if race.empty:
        raise SystemExit("no overlapping harvests between forecasts and prices")
    moments = return_moments(store.read("yahoo_prices_daily"))
    budget = power_budget(moments, cost_bps=tuple(args.cost_bps))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.output_dir / "corn_price_information"
    for name, frame in (("race", race), ("signals", signals),
                        ("moments", moments), ("budget", budget)):
        frame.to_csv(f"{stem}_{name}.csv", index=False, float_format="%.10g",
                     date_format="%Y-%m-%d")
    plot_information(race, budget, args.output_dir)
    Path(f"{stem}_report.md").write_text(render_report(
        race, moments, budget,
        hashes=fingerprints(store, {PREDICTIONS: predictions_path})))

    shown = race.loc[(race["issue_month"] == FEASIBLE_ISSUE)
                     & (race["feature_set"] == HEADLINE_FEATURE_SET)]
    print(shown[["sample", "price_source", "harvests", "nowcast_r2", "price_r2",
                 "joint_r2", "incremental_r2", "nowcast_beta_retained",
                 "nowcast_beta_excludes_zero"]].to_string(
        index=False, float_format=lambda value: f"{value:.3f}"))
    print()
    print(budget.loc[budget["horizon_days"] == 5, [
        "cost_bps", "break_even_r2", "detectable_r2", "observations_needed",
        "blind_to_profitable_signal"]].to_string(
        index=False, float_format=lambda value: f"{value:.5f}"))
    print(f"Wrote {stem}_{{race,signals,moments,budget}}.csv, "
          f"{stem}_report.md, {stem}.svg")


if __name__ == "__main__":
    main()
