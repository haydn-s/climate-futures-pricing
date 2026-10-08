"""Window aggregation: does a wrapping window take the right months, and does an
absent thermometer read as absent?

Two silent failures guarded here. A half-covered window that still returns a
total reads as a mild season rather than a missing one. And a frost count of
zero where tmin was never fetched reads as "no frost" rather than "not looked
at" -- which for coffee inverts the finding.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.features import windows


def daily(start: str, end: str, *, tmax: float = 30.0, precip: float = 5.0,
          tmin: float | None = None) -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="D")
    frame = pd.DataFrame({"date": dates, "tmax_c": tmax, "precip_mm": precip})
    if tmin is not None:
        frame["tmin_c"] = tmin
    return frame


# ---------------------------------------------------------------- the calendar


def test_a_window_inside_one_year_takes_those_months() -> None:
    assert windows.months(start=6, end=9) == [(6, 0), (7, 0), (8, 0), (9, 0)]


def test_a_window_wraps_the_new_year_with_a_negative_offset() -> None:
    """December belongs to the PREVIOUS calendar year in a wrapping window."""
    assert windows.months(start=12, end=2) == [(12, -1), (1, 0), (2, 0)]
    full = windows.months(start=10, end=9)
    assert len(full) == 12
    assert full[0] == (10, -1) and full[-1] == (9, 0)
    assert sum(1 for _, offset in full if offset == -1) == 3


def test_an_impossible_month_is_refused() -> None:
    with pytest.raises(ValueError, match="months must be 1-12"):
        windows.months(start=0, end=5)
    with pytest.raises(ValueError, match="months must be 1-12"):
        windows.months(start=6, end=13)


def test_a_wrapping_window_reads_december_from_the_year_before() -> None:
    """The whole point: asking for 2012's Harmattan must take Dec 2011.

    Hot December 2011 and cold December 2012 make the two distinguishable, so a
    reader that ignored the offset would fail here.
    """
    frame = pd.concat([daily("2011-12-01", "2011-12-31", tmax=40.0),
                       daily("2012-01-01", "2012-02-29", tmax=20.0),
                       daily("2012-12-01", "2012-12-31", tmax=10.0)],
                      ignore_index=True)
    harmattan = windows.months(start=12, end=2)
    features = windows.window(frame, harmattan, 2012, heat_threshold=32.0)
    assert features is not None
    # Dec 2011 at 40 C contributes 8 degree-days a day over 31 days; the 2012
    # months contribute nothing, and Dec 2012 must not be read at all.
    assert features["heat_dd"] == pytest.approx(31 * 8.0)
    assert features["days"] == 31 + 31 + 29


# ----------------------------------------------------------------- the measures


def test_heat_is_degree_days_above_a_per_crop_threshold() -> None:
    frame = daily("2012-06-01", "2012-09-30", tmax=35.0)
    season = windows.months(start=6, end=9)
    cocoa = windows.window(frame, season, 2012, heat_threshold=32.0)
    coffee = windows.window(frame, season, 2012, heat_threshold=30.0)
    assert coffee["heat_dd"] > cocoa["heat_dd"], "a lower threshold accumulates more"
    assert cocoa["heat_dd"] == pytest.approx(122 * 3.0)
    assert cocoa["heat_days"] == 122


def test_a_dry_spell_is_the_longest_run_not_the_count() -> None:
    """Thirty scattered dry days and thirty consecutive ones are different."""
    dates = pd.date_range("2012-06-01", "2012-09-30", freq="D")
    scattered = np.where(np.arange(len(dates)) % 2 == 0, 0.0, 10.0)
    consecutive = np.concatenate([np.zeros(40), np.full(len(dates) - 40, 10.0)])
    season = windows.months(start=6, end=9)
    for precip, expected_spell in ((scattered, 1.0), (consecutive, 40.0)):
        frame = pd.DataFrame({"date": dates, "tmax_c": 30.0, "precip_mm": precip})
        features = windows.window(frame, season, 2012, heat_threshold=32.0)
        assert features["max_dry_spell"] == expected_spell
    # Both have about the same dry-day COUNT, which is the point.
    assert abs(int((scattered < 1).sum()) - 40) < 25


def test_wet_days_are_counted_because_rain_damages_both_ways() -> None:
    season = windows.months(start=6, end=9)
    soaked = windows.window(daily("2012-06-01", "2012-09-30", precip=50.0),
                            season, 2012, heat_threshold=32.0)
    assert soaked["wet_days"] == 122
    assert soaked["dry_days"] == 0
    assert soaked["max_dry_spell"] == 0


# -------------------------------------------------------------------- the frost


def test_frost_is_counted_from_tmin_when_it_is_there() -> None:
    frame = daily("2021-06-01", "2021-08-31", tmax=25.0, tmin=10.0)
    # One cold night in the middle, at the real 2021 Minas Gerais value.
    frame.loc[frame["date"] == pd.Timestamp("2021-07-20"), "tmin_c"] = 4.44
    frame.loc[frame["date"] == pd.Timestamp("2021-07-19"), "tmin_c"] = 7.36
    winter = windows.months(start=6, end=8)
    features = windows.window(frame, winter, 2021, heat_threshold=30.0,
                              frost_threshold=windows.FROST_THRESHOLD_COFFEE)
    # 4.44 is above the 4.0 threshold, so this counts no frost -- and that is
    # the right answer for a screen-height reanalysis: the damage happens at
    # leaf level, which runs colder than what POWER reports.
    assert features["frost_days"] == 0
    assert features["min_tmin"] == pytest.approx(4.44)


def test_a_colder_night_does_cross_the_threshold() -> None:
    frame = daily("2021-06-01", "2021-08-31", tmax=25.0, tmin=10.0)
    frame.loc[frame["date"].isin(pd.to_datetime(["2021-07-19", "2021-07-20"])),
              "tmin_c"] = 2.0
    features = windows.window(frame, windows.months(start=6, end=8), 2021,
                              heat_threshold=30.0, frost_threshold=4.0)
    assert features["frost_days"] == 2
    assert features["min_tmin"] == pytest.approx(2.0)


def test_no_tmin_column_gives_nan_not_zero_frost() -> None:
    """"No frost" and "no thermometer" are opposite claims for coffee.

    NASA POWER carried no T2M_MIN until coffee was added, so every archived file
    older than that change has no tmin -- and a zero frost count on those years
    would read as a run of mild winters.
    """
    frame = daily("2021-06-01", "2021-08-31", tmax=25.0)       # no tmin_c
    features = windows.window(frame, windows.months(start=6, end=8), 2021,
                              heat_threshold=30.0, frost_threshold=4.0)
    assert np.isnan(features["frost_days"])
    assert np.isnan(features["min_tmin"])
    # The rest of the window still works.
    assert features["heat_days"] == 0 and features["rain_mm"] > 0


def test_an_all_nan_tmin_column_is_also_nan_rather_than_no_frost() -> None:
    frame = daily("2021-06-01", "2021-08-31", tmax=25.0, tmin=float("nan"))
    features = windows.window(frame, windows.months(start=6, end=8), 2021,
                              heat_threshold=30.0, frost_threshold=4.0)
    assert np.isnan(features["frost_days"])


def test_frost_is_nan_when_no_threshold_is_asked_for() -> None:
    frame = daily("2021-06-01", "2021-08-31", tmax=25.0, tmin=1.0)
    features = windows.window(frame, windows.months(start=6, end=8), 2021,
                              heat_threshold=30.0)
    assert np.isnan(features["frost_days"]), "a crop with no frost threshold"


# ------------------------------------------------------------------- coverage


def test_a_half_covered_window_is_none_rather_than_a_mild_season() -> None:
    frame = daily("2012-06-01", "2012-07-15")       # six weeks of a four-month window
    assert windows.window(frame, windows.months(start=6, end=9), 2012,
                          heat_threshold=32.0) is None


def test_an_empty_frame_is_none() -> None:
    empty = pd.DataFrame(columns=["date", "tmax_c", "precip_mm"])
    assert windows.window(empty, windows.months(start=6, end=9), 2012,
                          heat_threshold=32.0) is None


def test_the_table_helper_skips_uncovered_years_and_keeps_the_rest() -> None:
    frame = pd.concat([daily(f"{year}-06-01", f"{year}-09-30") for year in (2012, 2014)],
                      ignore_index=True)
    built = windows.table(frame, windows.months(start=6, end=9), range(2012, 2016),
                          heat_threshold=32.0)
    assert sorted(built.index) == [2012, 2014], "2013 and 2015 are absent, not zero"
    assert set(windows.MEASURES) <= set(built.columns)


# ------------------------------------------------------- combining across points


def test_frost_takes_the_coldest_point_not_the_average() -> None:
    """The correction that made coffee measurable.

    Averaging Brazil's three arabica points put every year's minimum above a
    4 C threshold and made frost_days a constant zero over 27 years, while the
    coldest single point fell below 4 C in six of them. Damage from a cold night
    is local: a region is as frost-stricken as its worst location.
    """
    mild = daily("2021-06-01", "2021-08-31", tmax=25.0, tmin=8.0)
    cold = daily("2021-06-01", "2021-08-31", tmax=25.0, tmin=8.0)
    cold.loc[cold["date"] == pd.Timestamp("2021-07-20"), "tmin_c"] = 2.0
    winter = windows.months(start=6, end=8)

    combined = windows.across_points({"mild": mild, "mild2": mild, "cold": cold},
                                     winter, 2021, heat_threshold=30.0,
                                     frost_threshold=4.0)
    assert combined["min_tmin"] == pytest.approx(2.0), "the coldest point wins"
    assert combined["frost_days"] == 1, "and its frost night is not averaged away"
    assert combined["points"] == 3

    # Averaging the frames first is what hides it: the mean of 8, 8 and 2 is 6.
    averaged = pd.concat([mild, mild, cold]).groupby("date", as_index=False).mean(
        numeric_only=True)
    hidden = windows.window(averaged, winter, 2021, heat_threshold=30.0,
                            frost_threshold=4.0)
    assert hidden["frost_days"] == 0, "which is the bug this exists to prevent"


def test_a_dry_spell_also_takes_the_worst_point() -> None:
    wet = daily("2012-06-01", "2012-09-30", precip=10.0)
    dry = daily("2012-06-01", "2012-09-30", precip=10.0)
    dry.loc[dry["date"] < pd.Timestamp("2012-07-01"), "precip_mm"] = 0.0
    combined = windows.across_points({"wet": wet, "dry": dry},
                                     windows.months(start=6, end=9), 2012,
                                     heat_threshold=32.0)
    assert combined["max_dry_spell"] == 30, "a region is as droughted as its worst point"
    # Rain itself is still an average across the region.
    assert combined["rain_mm"] == pytest.approx((122 * 10 + 92 * 10) / 2)


def test_a_region_is_none_unless_every_point_covers_the_window() -> None:
    """Otherwise a region quietly shrinks to whichever points were archived."""
    full = daily("2012-06-01", "2012-09-30")
    partial = daily("2012-06-01", "2012-07-10")
    season = windows.months(start=6, end=9)
    assert windows.across_points({"a": full, "b": partial}, season, 2012,
                                 heat_threshold=32.0) is None
    assert windows.across_points({}, season, 2012, heat_threshold=32.0) is None
    assert windows.across_points({"a": full}, season, 2012,
                                 heat_threshold=32.0) is not None
