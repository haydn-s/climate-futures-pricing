"""The event study: does it align on the right day, and count each episode once?

Both failure modes here are silent and both inflate the result. Misaligning by
one day moves a same-day reaction into the "before" window and manufactures a
lead; failing to de-cluster turns one drought into forty independent
confirmations of it. Neither raises, so both are tested directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline import events


def timeline(map_dates: list[str], deltas: list[float]) -> pd.DataFrame:
    """A belt drought timeline: Tuesday maps, published the Thursday after."""
    stamps = pd.to_datetime(map_dates)
    return pd.DataFrame({
        "map_date": stamps,
        "publication_date": stamps + pd.Timedelta(days=2),
        "d2": np.cumsum(deltas),
        "d2_delta1": deltas,
    })


# -------------------------------------------------------------------- alignment


def test_publication_day_counts_as_day_zero() -> None:
    """The map is released at 08:30 ET, before the grain close.

    So the publication day's own close can reflect it and must be day 0. Pushing
    it to the next day would reclassify a same-day reaction as a lead, which is
    the error that flatters this project's hypothesis.
    """
    trading = pd.Series(pd.to_datetime(
        ["2012-07-02", "2012-07-03", "2012-07-05", "2012-07-06"]))
    # A Thursday release that IS a trading day.
    assert events.trading_positions(pd.Series([pd.Timestamp("2012-07-05")]), trading)[0] == 2


def test_a_release_on_a_market_holiday_moves_forward_not_back() -> None:
    """July 4 is not a trading day; the figure is first tradeable on the 5th."""
    trading = pd.Series(pd.to_datetime(["2012-07-02", "2012-07-03", "2012-07-05"]))
    position = events.trading_positions(pd.Series([pd.Timestamp("2012-07-04")]), trading)[0]
    assert position == 2
    assert trading.iloc[position] == pd.Timestamp("2012-07-05")


def test_a_publication_past_the_price_series_is_flagged_not_clamped() -> None:
    """Clamping to the last row would stack phantom events on the final day."""
    trading = pd.Series(pd.to_datetime(["2012-07-02", "2012-07-03"]))
    assert events.trading_positions(pd.Series([pd.Timestamp("2026-01-01")]), trading)[0] == -1


# ------------------------------------------------------------------ de-clustering


def test_one_drought_counts_once() -> None:
    """Five consecutive weekly jumps are one episode, not five events.

    Their 41-day windows would overlap almost entirely, so counting them
    separately multiplies a single 2012 into several "independent" observations.
    """
    weekly = [f"2012-06-{day:02d}" for day in (5, 12, 19, 26)] + ["2012-07-03"]
    chosen = events.select_events(timeline(weekly, [20.0, 25.0, 30.0, 22.0, 18.0]),
                                  quantile=0.01, min_spacing_days=28)
    assert len(chosen) == 1
    # The largest jump in the cluster is the one kept.
    assert chosen["magnitude"].iloc[0] == 30.0
    assert chosen["publication_date"].iloc[0] == pd.Timestamp("2012-06-21")


def test_episodes_further_apart_than_the_spacing_both_survive() -> None:
    # Equal magnitudes, so the quantile cut-off cannot be what decides this: on
    # two unequal values an interpolated cut-off sits above the lower one and
    # would drop it, testing the threshold instead of the spacing.
    far = timeline(["2012-06-05", "2012-08-07"], [25.0, 25.0])
    chosen = events.select_events(far, quantile=0.01, min_spacing_days=28)
    assert len(chosen) == 2
    assert chosen["publication_date"].is_monotonic_increasing


def test_only_increases_are_events_and_the_threshold_is_a_quantile() -> None:
    """A drought easing is not a drought shock."""
    mixed = timeline(["2012-05-01", "2012-07-03", "2012-09-04"], [-30.0, 25.0, 1.0])
    chosen = events.select_events(mixed, quantile=0.5, min_spacing_days=1)
    assert (chosen["magnitude"] > 0).all()
    assert chosen["magnitude"].tolist() == [25.0]


def test_a_timeline_with_no_increase_yields_no_events_rather_than_raising() -> None:
    assert events.select_events(timeline(["2012-05-01"], [-5.0])).empty
    assert events.select_events(timeline([], [])).empty


def test_an_impossible_quantile_is_refused() -> None:
    with pytest.raises(ValueError, match="quantile must be in"):
        events.select_events(timeline(["2012-05-01"], [5.0]), quantile=1.0)


# ----------------------------------------------------------------------- windows


def test_an_event_without_a_full_window_is_nan_not_truncated() -> None:
    """A short window silently averaged against full ones biases the mean."""
    values = np.arange(10, dtype="float64")
    matrix = events.event_matrix(values, np.array([1, 5, 9]), pre=2, post=2)
    assert np.isnan(matrix[0]).all(), "position 1 has no room for pre=2"
    assert np.isnan(matrix[2]).all(), "position 9 has no room for post=2"
    assert matrix[1].tolist() == [3.0, 4.0, 5.0, 6.0, 7.0]


def test_a_window_is_anchored_at_its_first_day() -> None:
    matrix = np.array([[0.01, 0.02, -0.01, 0.03, 0.01]])
    path = events.cumulate(matrix, pre=2)
    assert path[0, 0] == pytest.approx(0.01)
    assert path[0, -1] == pytest.approx(0.06)
    assert np.all(np.diff(path[0]) == pytest.approx([0.02, -0.01, 0.03, 0.01]))


def test_window_sum_offsets_are_signed_and_relative_to_publication() -> None:
    # Offsets -2..+2; day 0 sits at column 2.
    matrix = np.array([[1.0, 2.0, 100.0, 4.0, 8.0]])
    assert events.window_sum(matrix, -2, -1, pre=2)[0] == pytest.approx(3.0)
    assert events.window_sum(matrix, 0, 0, pre=2)[0] == pytest.approx(100.0)
    assert events.window_sum(matrix, 1, 2, pre=2)[0] == pytest.approx(12.0)
    with pytest.raises(ValueError, match="falls outside"):
        events.window_sum(matrix, -3, 0, pre=2)


# ------------------------------------------------------------------- adjustments


def test_the_seasonal_mean_excludes_the_events_own_year() -> None:
    """Including it would subtract part of the move being measured.

    Two years, same ISO week. Leave-one-out means each year is adjusted by the
    OTHER year's mean, so a year above the other ends up positive -- not zero,
    which is what a naive same-week demeaning would give.
    """
    dates = pd.to_datetime(["2011-07-05", "2011-07-06", "2012-07-03", "2012-07-04"])
    returns = pd.Series([0.01, 0.01, 0.05, 0.05])
    adjusted = events.deseasonalise(returns, pd.Series(dates))
    assert adjusted.iloc[2] == pytest.approx(0.04)
    assert adjusted.iloc[0] == pytest.approx(-0.04)
    assert not np.allclose(adjusted.to_numpy(), 0.0), "own year was included"


def test_a_week_appearing_in_one_year_only_is_left_alone() -> None:
    """With no other year to compare against, the honest adjustment is none."""
    dates = pd.Series(pd.to_datetime(["2012-07-03", "2012-07-04"]))
    returns = pd.Series([0.02, 0.03])
    assert events.deseasonalise(returns, dates).tolist() == pytest.approx([0.02, 0.03])


# ---------------------------------------------------------------------- placebo


def test_the_placebo_draws_the_same_number_of_events_from_eligible_days() -> None:
    rng_values = np.random.default_rng(1).normal(0, 0.01, 500)
    eligible = np.arange(50, 450)
    distribution = events.placebo_distribution(
        rng_values, eligible, n_events=8, start=-10, end=-1, draws=200, seed=3)
    assert distribution.shape == (200,)
    # Noise with zero mean: the placebo must straddle zero rather than drift.
    assert abs(float(np.mean(distribution))) < 0.01


def test_the_placebo_is_reproducible_and_empty_when_it_cannot_draw() -> None:
    values = np.random.default_rng(2).normal(0, 0.01, 300)
    eligible = np.arange(30, 270)
    first = events.placebo_distribution(values, eligible, 5, start=-5, end=-1,
                                        draws=50, seed=7)
    again = events.placebo_distribution(values, eligible, 5, start=-5, end=-1,
                                        draws=50, seed=7)
    assert np.array_equal(first, again)
    # More events than eligible days: no distribution rather than a wrong one.
    assert events.placebo_distribution(values, np.arange(30, 33), 99,
                                       start=-5, end=-1).size == 0


def test_the_quantile_locates_the_observation_in_the_distribution() -> None:
    distribution = np.arange(100, dtype="float64")
    assert events.quantile_of(50.0, distribution) == pytest.approx(0.51)
    assert events.quantile_of(-1.0, distribution) == 0.0
    assert events.quantile_of(200.0, distribution) == 1.0
    assert np.isnan(events.quantile_of(1.0, np.array([])))


# -------------------------------------------------------------- the confound


def test_forward_change_measures_how_much_further_the_drought_went() -> None:
    """The event study's confound, made measurable.

    d2 runs 10, 30, 45, 40: an event at the 30 is followed by 45 two maps on,
    so the drought kept worsening by 15 points after it was published.
    """
    weekly = timeline(["2012-06-05", "2012-06-12", "2012-06-19", "2012-06-26"],
                      [10.0, 20.0, 15.0, -5.0])
    assert weekly["d2"].tolist() == [10.0, 30.0, 45.0, 40.0]
    chosen = weekly.iloc[[1]]
    assert events.forward_change(chosen, weekly, maps_ahead=2).iloc[0] == pytest.approx(10.0)
    # One map ahead instead: 45 - 30.
    assert events.forward_change(chosen, weekly, maps_ahead=1).iloc[0] == pytest.approx(15.0)


def test_a_drought_that_peaked_reports_a_negative_forward_change() -> None:
    weekly = timeline(["2012-07-03", "2012-07-10", "2012-07-17"], [60.0, -10.0, -20.0])
    chosen = weekly.iloc[[0]]
    assert events.forward_change(chosen, weekly, maps_ahead=2).iloc[0] == pytest.approx(-30.0)


def test_running_off_the_end_of_the_timeline_is_nan_not_zero() -> None:
    """Zero would read as "the drought stopped", which is a different claim."""
    weekly = timeline(["2012-07-03", "2012-07-10"], [60.0, 5.0])
    changes = events.forward_change(weekly.iloc[[1]], weekly, maps_ahead=2)
    assert np.isnan(changes.iloc[0])


def test_forward_change_on_empty_inputs_returns_empty() -> None:
    weekly = timeline(["2012-07-03"], [10.0])
    assert events.forward_change(weekly.iloc[[]], weekly).empty
    assert events.forward_change(weekly, timeline([], [])).empty


def test_an_event_with_no_window_sums_to_nan_not_zero() -> None:
    """np.nansum returns 0.0 for an all-NaN row, which is a 0% return.

    Averaged in beside real events that would dilute every event-study mean
    toward zero, understating the effect while the row count still looks right.
    """
    values = np.arange(10, dtype="float64")
    matrix = events.event_matrix(values, np.array([1, 5]), pre=2, post=2)
    assert np.isnan(matrix[0]).all(), "position 1 has no room"
    summed = events.window_sum(matrix, -2, -1, pre=2)
    assert np.isnan(summed[0]), "a missing window must not read as a flat return"
    assert summed[1] == pytest.approx(7.0)
    # And the mean over real events is unaffected by the absent one.
    assert np.nanmean(summed) == pytest.approx(7.0)
