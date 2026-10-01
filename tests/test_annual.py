"""Annual features: is the trend fitted without seeing the year it scores?

With 26 observations, leaking one year's harvest into the trend used to judge it
is enough to invent a result on its own. That is the test that matters here. The
rest guard against a partly covered year quietly looking like a mild one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.features import annual


def daily(year: int, tmax: float, precip: float = 2.0,
          months: range = range(4, 11)) -> pd.DataFrame:
    dates = [d for d in pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
             if d.month in months]
    return pd.DataFrame({"date": dates, "tmax_c": tmax, "precip_mm": precip})


# ------------------------------------------------------------------- detrending


def test_the_trend_can_be_fitted_without_the_year_it_scores() -> None:
    """A trend fitted on all years has already seen the harvest it judges."""
    years = np.arange(2000, 2011)
    # A clean trend with one collapsed year in the middle.
    values = pd.Series(10.0 + 0.5 * (years - 2000), index=years)
    values.loc[2005] = values.loc[2005] * 0.70

    leaky = annual.detrend_pct(values)
    honest = annual.detrend_pct(values, fit_years=years[years != 2005])
    # The shortfall is understated when the collapse helped define the trend.
    assert honest.loc[2005] < leaky.loc[2005] < 0
    assert honest.loc[2005] == pytest.approx(-0.30, abs=0.01)


def test_a_deviation_is_relative_to_the_trend_not_the_mean() -> None:
    years = np.arange(2000, 2010)
    values = pd.Series(100.0 + 10.0 * (years - 2000), index=years)
    deviations = annual.detrend_pct(values)
    # A perfect trend leaves no deviation anywhere, which a mean would not.
    assert deviations.abs().max() == pytest.approx(0.0, abs=1e-9)


def test_too_few_years_and_too_few_fit_years_both_yield_nan() -> None:
    short = pd.Series([1.0, 2.0], index=[2000, 2001])
    assert annual.detrend_pct(short).isna().all()
    enough = pd.Series(np.arange(1.0, 11.0), index=np.arange(2000, 2010))
    assert annual.detrend_pct(enough, fit_years=np.array([2000, 2001])).isna().all()


def test_a_non_positive_trend_is_refused_rather_than_inverting_every_sign() -> None:
    years = np.arange(2000, 2010)
    falling = pd.Series(np.linspace(50.0, -50.0, len(years)), index=years)
    with pytest.raises(ValueError, match="non-positive"):
        annual.detrend_pct(falling)


# ------------------------------------------------------------------ aggregation


def test_heat_is_accumulated_above_the_threshold_within_the_season() -> None:
    frame = annual.annual_weather(daily(2012, tmax=39.0))
    # 10 C over the threshold on every day of a 214-day April-October season.
    assert frame.loc[2012, "days"] == 214
    assert frame.loc[2012, "heat_dd_season"] == pytest.approx(2140.0)
    # July alone: 31 days.
    assert frame.loc[2012, "heat_dd_july"] == pytest.approx(310.0)
    assert frame.loc[2012, "tmax_july"] == pytest.approx(39.0)


def test_a_cool_season_accumulates_no_heat_rather_than_negative_heat() -> None:
    frame = annual.annual_weather(daily(2009, tmax=20.0))
    assert frame.loc[2009, "heat_dd_season"] == 0.0
    assert frame.loc[2009, "heat_dd_july"] == 0.0


def test_a_partly_covered_year_is_visible_in_the_day_count() -> None:
    """Half a season sums to half the heat, which reads as a mild year.

    The guard is the day count travelling with the aggregate, so a caller can
    drop the year instead of believing it.
    """
    partial = daily(2026, tmax=39.0, months=range(4, 7))   # April-June only
    frame = annual.annual_weather(partial)
    assert frame.loc[2026, "days"] == 91
    # NaN and not 0.0: a missing July is unknown, while zero degree-days would
    # read as a COOL July and drag the fitted heat-yield relationship towards
    # nothing. The distinction is the whole reason the day count is carried.
    assert np.isnan(frame.loc[2026, "heat_dd_july"]), "an absent July must not read as mild"
    assert frame.loc[2026, "heat_dd_season"] > 0.0, "April to June was observed"


def test_the_label_prefixes_every_column_so_two_sources_can_sit_together() -> None:
    counties = annual.annual_weather(daily(2012, tmax=35.0), label="cty_")
    points = annual.annual_weather(daily(2012, tmax=37.0))
    joined = counties.join(points)
    assert "cty_heat_dd_july" in joined.columns
    assert "heat_dd_july" in joined.columns
    assert joined.loc[2012, "cty_heat_dd_july"] < joined.loc[2012, "heat_dd_july"]


def test_drought_summarises_the_worst_week_as_well_as_the_average() -> None:
    maps = pd.to_datetime(["2012-05-01", "2012-07-03", "2012-08-07", "2012-12-04"])
    belt = pd.DataFrame({"map_date": maps, "d2": [0.0, 40.0, 80.0, 90.0],
                         "d0": [10.0, 60.0, 95.0, 99.0]})
    frame = annual.annual_drought(belt)
    # December is outside April-October and must not set the peak.
    assert frame.loc[2012, "peak_d2"] == 80.0
    assert frame.loc[2012, "mean_d2"] == pytest.approx(40.0)
    assert frame.loc[2012, "maps"] == 3


def test_empty_inputs_return_empty_frames_rather_than_raising() -> None:
    assert annual.annual_weather(pd.DataFrame(columns=["date", "tmax_c"])).empty
    assert annual.annual_drought(pd.DataFrame(columns=["map_date", "d2"])).empty


# ----------------------------------------------------------------------- yields


def test_the_yield_entity_is_matched_on_a_prefix() -> None:
    """Entity spelling is not stable across the yield datasets."""
    yields = pd.DataFrame({
        "crop": ["corn", "corn", "cocoa"],
        "entity": ["United States of America", "Canada", "Ghana"],
        "year": [2012, 2012, 2012],
        "yield_t_per_ha": [7.7, 9.0, 0.4],
    })
    series = annual.corn_yield(yields)
    assert series.loc[2012] == pytest.approx(7.7)
    assert len(series) == 1


def test_a_missing_entity_names_itself() -> None:
    yields = pd.DataFrame({"crop": ["corn"], "entity": ["Canada"],
                           "year": [2012], "yield_t_per_ha": [9.0]})
    with pytest.raises(SystemExit, match="United States"):
        annual.corn_yield(yields)
