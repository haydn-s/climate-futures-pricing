"""Derive every number the final deck quotes from the committed analysis outputs.

The mid-semester deck hardcoded its figures inside the builder, which let the
slides drift from the reports they summarised. This reads the committed CSVs
instead, so a slide can only ever say what the repository says, and rebuilding
the deck after rerunning an analysis updates the numbers rather than silently
contradicting them.

Every extraction is accompanied by an assertion about what it found. A changed
feature set, a renamed column, or a result that moved will stop the build with a
named error rather than quietly emitting a deck full of wrong numbers. That is
the whole point of the file: it is a tripwire, not a convenience.

    python deck_data.py          # writes deck_data.json beside this file
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ANALYSIS = HERE.parents[1] / "analysis"
HEADLINE_NOWCAST = "points_drought"
FEASIBLE_ISSUE = 8


class DeckDataError(RuntimeError):
    """A committed analysis file did not contain what the deck expects."""


def _read(name: str) -> pd.DataFrame:
    path = ANALYSIS / name
    if not path.exists():
        raise DeckDataError(f"missing {path}; run the analysis before the deck")
    frame = pd.read_csv(path)
    if frame.empty:
        raise DeckDataError(f"{name} is empty")
    return frame


def _one(frame: pd.DataFrame, where: dict, label: str) -> pd.Series:
    """Select exactly one row, or say precisely which filter failed."""
    mask = pd.Series(True, index=frame.index)
    for column, value in where.items():
        if column not in frame.columns:
            raise DeckDataError(f"{label}: no column {column!r}")
        mask &= frame[column] == value
    rows = frame.loc[mask]
    if len(rows) != 1:
        raise DeckDataError(
            f"{label}: expected one row for {where}, found {len(rows)}")
    return rows.iloc[0]


def supply_risk() -> dict:
    """The physical result, forward-validated, with and without 2012."""
    results = _read("corn_yield_forward_validation_results.csv")
    full = _one(results, {"sample": "all_years",
                          "feature_set": "counties_drought"}, "forward")
    without = _one(results, {"sample": "exclude_2012",
                             "feature_set": "counties_drought"}, "forward")
    if not full["r2_vs_trend"] > without["r2_vs_trend"] > 0:
        raise DeckDataError(
            "the full-season result is no longer positive in both samples; "
            "the deck's claim must be rewritten before it can be rebuilt")
    summary = _read("corn_yield_strata_influence_summary.csv")
    influence = _one(summary, {"study": "forward", "sample": "all_years",
                               "feature_set": "counties_drought"}, "influence")
    influence_ex = _one(summary, {"study": "forward", "sample": "exclude_2012",
                                  "feature_set": "counties_drought"},
                        "influence")
    if influence["sign_flips"] or influence_ex["sign_flips"]:
        raise DeckDataError(
            "a single withheld harvest now flips the sign of the full-season "
            "score; slide 'measurement' overstates the result")
    strata = _read("corn_yield_strata_buckets.csv")
    severity = {
        bucket: _one(strata, {"study": "forward", "sample": "all_years",
                              "feature_set": "counties_drought",
                              "dimension": "severity", "bucket": bucket},
                     "severity")
        for bucket in ("high", "low")
    }
    severity_ex = _one(strata, {"study": "forward", "sample": "exclude_2012",
                                "feature_set": "counties_drought",
                                "dimension": "severity", "bucket": "low"},
                       "severity")
    return {
        "r2_all_years": float(full["r2_vs_trend"]),
        "r2_excluding_2012": float(without["r2_vs_trend"]),
        "test_years": int(full["test_years"]),
        "mae_all_years": float(full["mae_t_per_ha"]),
        "trend_mae_all_years": float(full["trend_mae_t_per_ha"]),
        "influence_low": float(influence["r2_min_without_one_year"]),
        "influence_high": float(influence["r2_max_without_one_year"]),
        "influence_low_ex2012": float(influence_ex["r2_min_without_one_year"]),
        "influence_high_ex2012": float(influence_ex["r2_max_without_one_year"]),
        "most_load_bearing_year": int(influence["most_load_bearing_year"]),
        "severity_high_r2": float(severity["high"]["r2_vs_trend"]),
        "severity_low_r2": float(severity["low"]["r2_vs_trend"]),
        "severity_low_r2_ex2012": float(severity_ex["r2_vs_trend"]),
    }


def uncertainty() -> dict:
    """Coverage and width of the calibrated intervals."""
    coverage = _read("corn_yield_intervals_coverage.csv")
    full = _one(coverage, {"study": "forward", "sample": "all_years",
                           "feature_set": "counties_drought",
                           "method": "jackknife_plus", "nominal_level": 0.9},
                "coverage")
    nowcast = _one(coverage, {"study": "nowcast", "sample": "all_years",
                              "issue_month": FEASIBLE_ISSUE,
                              "feature_set": HEADLINE_NOWCAST,
                              "method": "jackknife_plus",
                              "nominal_level": 0.9}, "coverage")
    intervals = _read("corn_yield_intervals_predictions.csv")
    band = intervals.loc[(intervals["study"] == "forward")
                         & (intervals["sample"] == "all_years")
                         & (intervals["feature_set"] == "counties_drought")
                         & (intervals["method"] == "jackknife_plus")
                         & (intervals["nominal_level"] == 0.9)]
    worst = band.loc[band["pit"].idxmax()]
    margin = min(worst["actual_yield"] - worst["lower"],
                 worst["upper"] - worst["actual_yield"])
    if margin < 0:
        raise DeckDataError(
            f"{int(worst['year'])} now falls outside its 90% interval; the "
            "deck's coverage claim must be rewritten")
    return {
        "nominal_level": 0.90,
        "guaranteed_level": float(full["guaranteed_level"]),
        "covered": int(full["covered"]), "harvests": int(full["harvests"]),
        "width_pp_of_trend": float(full["mean_width_pp_of_trend"]),
        "half_width_pp": float(full["mean_width_pp_of_trend"]) / 2.0,
        "tightest_year": int(worst["year"]),
        "tightest_margin_t_per_ha": float(margin),
        "nowcast_covered": int(nowcast["covered"]),
        "nowcast_harvests": int(nowcast["harvests"]),
        "nowcast_width_pp_of_trend": float(nowcast["mean_width_pp_of_trend"]),
        "nowcast_uncovered": str(nowcast["uncovered_years"]),
    }


def provisioning() -> dict:
    """The price-free decision: what the estimate saves a buyer, and whether."""
    scores = _read("corn_yield_decisions_scores.csv")
    boot = _read("corn_yield_decisions_bootstrap.csv")
    where = {"information_set": "nowcast", "issue_month": FEASIBLE_ISSUE,
             "feature_set": HEADLINE_NOWCAST, "penalty_ratio": 4.0}
    full = _one(scores, {**where, "sample": "all_years", "strategy": "model"},
                "decisions")
    without = _one(scores, {**where, "sample": "exclude_2012",
                            "strategy": "model"}, "decisions")
    boot_full = _one(boot, {**where, "sample": "all_years"}, "bootstrap")
    boot_ex = _one(boot, {**where, "sample": "exclude_2012"}, "bootstrap")
    spreads = _read("corn_yield_decisions_spreads.csv")
    ratio = spreads.loc[(spreads["sample"] == "all_years")
                        & (spreads["information_set"] == "nowcast")
                        & (spreads["issue_month"] == FEASIBLE_ISSUE)
                        & (spreads["feature_set"] == HEADLINE_NOWCAST),
                        "spread_ratio"].mean()
    if ratio >= 1.0:
        raise DeckDataError(
            "the model is now the wider distribution, so the saving may be "
            "conservatism; the deck's conservatism claim must be rewritten")
    if boot_ex["bootstrap_p025"] > 0:
        raise DeckDataError(
            "the saving without 2012 now excludes zero; the deck understates "
            "the result and should be rewritten")
    return {
        "penalty_ratio": 4,
        "saving_all_years": float(full["cost_reduction"]),
        "saving_excluding_2012": float(without["cost_reduction"]),
        "spread_ratio": float(ratio),
        "bootstrap_p025_all_years": float(boot_full["bootstrap_p025"]),
        "bootstrap_p025_excluding_2012": float(boot_ex["bootstrap_p025"]),
        "probability_positive_excluding_2012": float(
            boot_ex["probability_positive"]),
    }


def efficiency() -> dict:
    """The horse race and the power budget."""
    race = _read("corn_price_information_race.csv")
    where = {"price_source": "december_contract", "issue_month": FEASIBLE_ISSUE,
             "feature_set": HEADLINE_NOWCAST}
    full = _one(race, {**where, "sample": "all_years"}, "race")
    without = _one(race, {**where, "sample": "exclude_2012"}, "race")
    budget = _read("corn_price_information_budget.csv")
    five = budget.loc[budget["horizon_days"] == 5]
    if five.empty:
        raise DeckDataError("no five-day row in the power budget")
    cheap = _one(five, {"cost_bps": 10.0}, "budget")
    if not bool(cheap["blind_to_profitable_signal"]):
        raise DeckDataError(
            "the sample can now detect a profitable ten-basis-point signal; "
            "the deck's power claim must be rewritten")
    return {
        "nowcast_r2": float(full["nowcast_r2"]),
        "price_r2": float(full["price_r2"]),
        "joint_r2": float(full["joint_r2"]),
        "incremental_r2": float(full["incremental_r2"]),
        "incremental_r2_excluding_2012": float(without["incremental_r2"]),
        "beta_retained": float(full["nowcast_beta_retained"]),
        "beta_excludes_zero": bool(full["nowcast_beta_excludes_zero"]),
        "harvests": int(full["harvests"]),
        "break_even_r2": float(cheap["break_even_r2"]),
        "detectable_r2": float(cheap["detectable_r2"]),
        "detection_ratio": float(cheap["detectable_r2"] / cheap["break_even_r2"]),
        "observations_needed": int(cheap["observations_needed"]),
        "observations_available": int(cheap["independent_observations"]),
    }


def trading_nulls() -> dict:
    """The three executed failures, read from their own locked result files."""
    gefs = _read("gefs_corn_holdout_results.csv")
    gefs_row = _one(gefs, {"specification":
                           "all_gefs_plus_market_shallow_boosting"}, "gefs")
    usda = _read("nass_corn_progress_validation_results.csv")
    headline = usda.loc[usda["is_headline"].astype(bool)]
    if len(headline) != 1:
        raise DeckDataError(
            f"expected one headline USDA candidate, found {len(headline)}")
    headline = headline.iloc[0]
    if gefs_row["total_net"] > 0 or headline["total_net"] > 0:
        raise DeckDataError(
            "a locked trading result is now positive; the deck's null claim "
            "must be rewritten")
    return {
        "gefs_holdout_net": float(gefs_row["total_net"]),
        "gefs_holdout_sharpe": float(gefs_row["sharpe"]),
        "gefs_holdout_trades": int(gefs_row["trades"]),
        "usda_net": float(headline["total_net"]),
        "usda_sharpe": float(headline["sharpe"]),
        "usda_trades": int(headline["trades"]),
        "usda_positive_years": int(headline["positive_years"]),
        "usda_years": int(headline["years"]),
    }


def main() -> None:
    payload = {
        "supply_risk": supply_risk(),
        "uncertainty": uncertainty(),
        "provisioning": provisioning(),
        "efficiency": efficiency(),
        "trading_nulls": trading_nulls(),
    }
    out = HERE / "deck_data.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out.relative_to(HERE.parents[1])}")
    for section, values in payload.items():
        print(f"  {section}: {len(values)} values")


if __name__ == "__main__":
    main()
