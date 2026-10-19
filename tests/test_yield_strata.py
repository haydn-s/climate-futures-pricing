"""The decomposition must agree with the published scores and refuse bad input."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.yield_strata import (
    MIN_STRATUM_YEARS, assign_strata, influence_summary, per_year_errors,
    score_by_group, score_influence, score_strata,
)
from pipeline.yield_validation import score_predictions

GROUP = ("sample", "feature_set")


def predictions(years=range(2010, 2026), *, sample="all_years") -> pd.DataFrame:
    """Two feature sets over the same harvests: one skilful, one the baseline."""
    rows = []
    rng = np.random.default_rng(11)
    for year in years:
        trend = 9.0 + 0.1 * (year - 2010)
        actual = trend * (1.0 + float(rng.normal(0, 0.06)))
        for name, share in (("trend_only", 0.0), ("counties_drought", 0.7)):
            predicted = trend * (1.0 + share * (actual / trend - 1.0))
            rows.append({
                "sample": sample, "feature_set": name, "year": year,
                "train_start": 2000, "train_end": year - 1,
                "train_years": year - 2000, "actual_yield": actual,
                "trend_yield": trend, "predicted_yield": predicted,
                "actual_anomaly": actual / trend - 1.0,
                "predicted_anomaly": share * (actual / trend - 1.0),
            })
    return pd.DataFrame(rows)


def annual_table(years=range(2010, 2026)) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    return pd.DataFrame({
        "peak_d2": rng.uniform(0, 40, len(list(years))),
        "cty_heat_dd_july": rng.uniform(0, 90, len(list(years))),
    }, index=pd.Index(list(years), name="year"))


def test_score_by_group_reproduces_the_published_metric():
    frame = predictions()
    mine = score_by_group(frame, group_columns=GROUP).set_index("feature_set")
    theirs = score_predictions(frame).set_index("feature_set")
    for name in theirs.index:
        assert mine.loc[name, "r2_vs_trend"] == pytest.approx(
            theirs.loc[name, "r2_vs_trend"], abs=1e-12)
        assert mine.loc[name, "mae_t_per_ha"] == pytest.approx(
            theirs.loc[name, "mae_t_per_ha"], abs=1e-12)


def test_per_year_errors_shares_sum_to_one_within_a_group():
    per_year = per_year_errors(predictions(), group_columns=GROUP)
    totals = per_year.groupby("feature_set")["sse_share"].sum()
    assert totals.loc["counties_drought"] == pytest.approx(1.0)
    # The gap is signed so that positive always means the model did better.
    row = per_year.loc[per_year["feature_set"] == "counties_drought"].iloc[0]
    assert row["abs_error_gap"] == pytest.approx(
        abs(row["trend_error"]) - abs(row["error"]))


def test_influence_is_a_rescore_not_a_refit():
    frame = predictions()
    influence = score_influence(frame, group_columns=GROUP)
    # The baseline is definitionally zero, so it is excluded rather than scored.
    assert set(influence["feature_set"]) == {"counties_drought"}
    assert (influence["retained_years"] == influence["test_years"] - 1).all()
    published = score_predictions(frame).set_index("feature_set")
    assert influence["baseline_r2_vs_trend"].unique() == pytest.approx(
        [published.loc["counties_drought", "r2_vs_trend"]])
    # Dropping a year must reproduce scoring that year's cohort directly.
    dropped = int(influence["dropped_year"].iloc[0])
    retained = frame.loc[frame["year"] != dropped]
    expected = score_predictions(retained).set_index("feature_set")
    assert influence["r2_vs_trend_without_year"].iloc[0] == pytest.approx(
        expected.loc["counties_drought", "r2_vs_trend"])


def test_influence_summary_counts_sign_flips():
    influence = score_influence(predictions(), group_columns=GROUP)
    summary = influence_summary(influence, group_columns=GROUP).iloc[0]
    assert summary["r2_min_without_one_year"] <= summary["r2_vs_trend"] \
        or summary["r2_max_without_one_year"] >= summary["r2_vs_trend"]
    assert summary["r2_range"] == pytest.approx(
        summary["r2_max_without_one_year"] - summary["r2_min_without_one_year"])
    assert summary["sign_flips"] == 0


def test_influence_summary_flags_a_score_that_depends_on_one_year():
    """A single season carrying the whole score must show up as a sign flip."""
    frame = predictions()
    model = frame["feature_set"] == "counties_drought"
    # Make every season but one a miss, and that one a large, accurate call.
    frame.loc[model, "predicted_yield"] = frame.loc[model, "trend_yield"] * 1.05
    carrying = model & (frame["year"] == 2012)
    frame.loc[carrying, "actual_yield"] = frame.loc[carrying, "trend_yield"] * 0.6
    frame.loc[carrying, "predicted_yield"] = frame.loc[carrying, "trend_yield"] * 0.6
    frame.loc[frame["year"] == 2012, "actual_yield"] = \
        frame.loc[carrying, "actual_yield"].iloc[0]
    summary = influence_summary(
        score_influence(frame, group_columns=GROUP), group_columns=GROUP).iloc[0]
    assert summary["most_load_bearing_year"] == 2012
    assert summary["r2_vs_trend"] > 0 > summary["r2_without_that_year"]
    assert summary["sign_flips"] >= 1


def test_strata_skip_thin_buckets_and_keep_the_denominator_visible():
    labelled = assign_strata(predictions(), annual_table(), group_columns=GROUP)
    strata = score_strata(labelled, group_columns=GROUP)
    assert not strata.empty
    assert (strata["test_years"] >= MIN_STRATUM_YEARS).all()
    assert "trend_mae_t_per_ha" in strata.columns
    assert set(strata["feature_set"]) == {"counties_drought"}
    # Every scored bucket names the seasons it contains, for auditing.
    assert strata["years"].str.count(";").add(1).equals(strata["test_years"])


def test_strata_omit_weather_dimensions_without_an_annual_table():
    labelled = assign_strata(predictions(), None, group_columns=GROUP)
    assert "drought_level" not in labelled.columns
    strata = score_strata(labelled, group_columns=GROUP)
    assert set(strata["dimension"]).isdisjoint({"drought_level", "heat_level"})
    assert "severity" in set(strata["dimension"])


def test_a_short_cohort_produces_no_terciles_rather_than_a_guess():
    labelled = assign_strata(predictions(range(2010, 2017)), None,
                             group_columns=GROUP)
    assert labelled["severity"].isna().all()


def test_grouping_by_issue_month_keeps_the_dates_separate():
    frames = []
    for month in (8, 9):
        frame = predictions()
        frame["issue_month"] = month
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True)
    group = ("sample", "issue_month", "feature_set")
    summary = influence_summary(
        score_influence(combined, group_columns=group), group_columns=group)
    assert sorted(summary["issue_month"]) == [8, 9]


def test_duplicate_group_years_are_rejected():
    frame = pd.concat([predictions(), predictions()], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        per_year_errors(frame, group_columns=GROUP)


def test_missing_columns_and_bad_grouping_are_rejected():
    with pytest.raises(ValueError, match="feature_set"):
        per_year_errors(predictions(), group_columns=("sample",))
    with pytest.raises(ValueError, match="missing columns"):
        per_year_errors(predictions().drop(columns=["trend_yield"]),
                        group_columns=GROUP)
    with pytest.raises(ValueError, match="empty"):
        per_year_errors(predictions().iloc[:0], group_columns=GROUP)
