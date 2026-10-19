"""Intervals must leave the published forecasts alone and stay honest about time."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.yield_intervals import (
    NOMINAL_LEVELS, _lower, _upper, calibration_curve, coverage,
    interval_predictions,
)
from pipeline.yield_validation import expanding_predictions

FEATURES = {"weather": ["heat", "rain"], "weather_drought": ["heat", "rain", "d2"]}


def table(years=range(2000, 2026), *, seed=7) -> pd.DataFrame:
    """A trending yield series driven by heat, with rain and drought as noise."""
    rng = np.random.default_rng(seed)
    years = list(years)
    heat = rng.uniform(0, 120, len(years))
    frame = pd.DataFrame({
        "heat": heat,
        "rain": rng.uniform(200, 500, len(years)),
        "d2": rng.uniform(0, 50, len(years)),
    }, index=pd.Index(years, name="year"))
    trend = 9.0 + 0.09 * (frame.index.to_numpy() - 2000)
    frame["yield_t_per_ha"] = trend * (
        1.0 - 0.0015 * heat + rng.normal(0, 0.02, len(years)))
    return frame


def test_point_forecast_matches_the_published_evaluation():
    """The intervals wrap the published forecast; they must not perturb it."""
    frame = table()
    published = expanding_predictions(frame, FEATURES).set_index(
        ["feature_set", "year"])
    intervals = interval_predictions(frame, FEATURES).drop_duplicates(
        ["feature_set", "year"]).set_index(["feature_set", "year"])
    shared = published.index.intersection(intervals.index)
    assert len(shared) > 10
    for key in shared:
        assert intervals.loc[key, "predicted_yield"] == pytest.approx(
            published.loc[key, "predicted_yield"], rel=1e-9)
        assert intervals.loc[key, "trend_yield"] == pytest.approx(
            published.loc[key, "trend_yield"], rel=1e-9)


def test_order_statistics_report_an_unsupported_level_as_infinite():
    values = np.arange(1.0, 6.0)  # n = 5
    # ceil(0.9 * 6) = 6 > 5, so a 90% upper bound is not supported by five points.
    assert _upper(values, 0.90) == float("inf")
    # ceil(0.8 * 6) = 5, the largest value.
    assert _upper(values, 0.80) == 5.0
    # floor(0.2 * 6) = 1, the smallest.
    assert _lower(values, 0.80) == 1.0
    assert _lower(values, 0.95) == float("-inf")


def test_a_higher_level_never_narrows_an_interval():
    intervals = interval_predictions(table(), FEATURES, levels=(0.80, 0.90))
    wide = intervals.loc[intervals["nominal_level"] == 0.90].set_index(
        ["feature_set", "year", "method"])["width"]
    narrow = intervals.loc[intervals["nominal_level"] == 0.80].set_index(
        ["feature_set", "year", "method"])["width"]
    assert (wide.loc[narrow.index] >= narrow - 1e-12).all()


def test_jackknife_plus_is_at_least_as_wide_as_the_gaussian_baseline():
    intervals = interval_predictions(table(), FEATURES, levels=(0.90,))
    pivot = intervals.pivot_table(index=["feature_set", "year"],
                                  columns="method", values="width")
    assert (pivot["jackknife_plus"] >= pivot["gaussian"]).mean() > 0.8


def test_reported_coverage_counts_the_actual_against_the_bounds():
    intervals = interval_predictions(table(), FEATURES)
    inside = ((intervals["lower"] <= intervals["actual_yield"])
              & (intervals["actual_yield"] <= intervals["upper"]))
    assert intervals["covered"].equals(inside)
    summary = coverage(intervals)
    row = summary.iloc[0]
    assert row["covered"] <= row["harvests"]
    assert row["observed_coverage"] == pytest.approx(row["covered"] / row["harvests"])
    assert row["coverage_resolution_pp"] == pytest.approx(100.0 / row["harvests"])


def test_guaranteed_level_is_reported_only_for_jackknife_plus():
    intervals = interval_predictions(table(), FEATURES, levels=(0.90,))
    plus = intervals.loc[intervals["method"] == "jackknife_plus"]
    others = intervals.loc[intervals["method"] != "jackknife_plus"]
    assert plus["guaranteed_level"].to_list() == pytest.approx([0.80] * len(plus))
    assert others["guaranteed_level"].isna().all()


def test_no_fold_uses_its_own_or_any_later_harvest():
    """Corrupting the future must not move an earlier harvest's interval."""
    frame = table()
    baseline = interval_predictions(frame, FEATURES, levels=(0.90,))
    tampered = frame.copy()
    tampered.loc[2020:, "yield_t_per_ha"] *= 3.0
    tampered.loc[2020:, "heat"] = 0.0
    after = interval_predictions(tampered, FEATURES, levels=(0.90,))
    keys = ["feature_set", "year", "method"]
    early = baseline.loc[baseline["year"] < 2020].set_index(keys)
    changed = after.loc[after["year"] < 2020].set_index(keys)
    for column in ("predicted_yield", "lower", "upper", "pit"):
        pd.testing.assert_series_equal(early[column], changed[column])


def test_calibration_curve_is_monotone_and_method_independent():
    intervals = interval_predictions(table(), FEATURES)
    curve = calibration_curve(intervals)
    for _, frame in curve.groupby("feature_set"):
        observed = frame.sort_values("nominal_level")["observed"].to_numpy()
        assert np.all(np.diff(observed) >= -1e-12)
        assert 0.0 <= observed.min() and observed.max() <= 1.0
    # One row per feature set and grid level, regardless of methods or levels.
    assert len(curve) == len(set(curve["feature_set"])) * len(
        set(curve["nominal_level"]))


def test_pit_is_bounded_and_uses_only_the_fold_residuals():
    intervals = interval_predictions(table(), FEATURES, levels=(0.90,))
    assert (intervals["pit"] > 0).all()
    assert (intervals["pit"] <= 1.0).all()
    smallest = intervals["calibration_residuals"].min()
    assert (intervals["pit"] >= 1.0 / (smallest + 1) - 1e-12).all()


def test_trend_only_and_bad_levels_are_rejected():
    with pytest.raises(ValueError, match="trend_only"):
        interval_predictions(table(), {"trend_only": []})
    with pytest.raises(ValueError, match="levels"):
        interval_predictions(table(), FEATURES, levels=(1.0,))
    with pytest.raises(ValueError, match="min_train_years"):
        interval_predictions(table(), FEATURES, min_train_years=1)


def test_a_short_series_yields_no_intervals_rather_than_a_guess():
    assert interval_predictions(table(range(2000, 2008)), FEATURES).empty


def test_levels_default_to_the_reported_pair():
    intervals = interval_predictions(table(), FEATURES)
    assert sorted(set(intervals["nominal_level"])) == sorted(NOMINAL_LEVELS)
