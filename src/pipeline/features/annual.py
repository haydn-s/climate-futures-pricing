"""Annual growing-season weather, and yield measured against its own trend.

The price work is daily and point-in-time; this is neither. It answers the first
research question -- can free climate data, mapped to where crops grow, measure a
supply shock? -- by lining one number per crop-year up against the harvest that
followed. Publication lag is irrelevant here: the harvest is known long after
every weather observation is final, so there is nothing to withhold and
`calendar.as_of` has no job. That is why these features are built separately from
features.panel rather than aggregated out of it.

TWO MEASUREMENTS OF THE SAME BELT, which is the comparison this module exists
for. nClimGrid gives true county averages for all 473 counties in the five
states; NASA POWER gives one representative point per state, which is what the
preliminary analysis used. The county product is a month late and so useless for
trading, but for an annual harvest a month is nothing. If the county average does
not explain yields better than five points, the 827 MB download bought nothing
and the README's "five equally weighted points" limitation is not the binding
one.

Detrending is where a small-sample result gets accidentally manufactured. Corn
yields rise by roughly a bushel a year for reasons that have nothing to do with
weather, so the target must be a deviation from trend -- and the trend has to be
fitted on TRAINING years only inside a cross-validation fold. A trend fitted on
all 26 years, then used to score a held-out year, has already seen that year's
harvest. `detrend_pct` therefore takes the years to fit on explicitly instead of
defaulting to all of them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .panel import HEAT_THRESHOLD_C

# Pollination decides a US corn crop, and July is when it happens.
PEAK_MONTH = 7
SUMMER_MONTHS = (6, 7, 8)


def _season(frame: pd.DataFrame, months: tuple[int, ...] | range) -> pd.DataFrame:
    return frame.loc[frame["date"].dt.month.isin(list(months))]


def annual_weather(daily: pd.DataFrame, *, tmax_column: str = "tmax_c",
                   precip_column: str | None = "precip_mm",
                   season: tuple[int, ...] | range = range(4, 11),
                   label: str = "") -> pd.DataFrame:
    """One row per year: heat above the threshold, peak-month heat, rainfall.

    `daily` is a belt-level daily frame -- the output of panel.belt_weather or
    panel.belt_counties, already aggregated across points or counties.
    """
    if daily.empty:
        return pd.DataFrame(columns=["year"]).set_index("year")
    work = daily.copy()
    work["year"] = work["date"].dt.year
    work["excess"] = (work[tmax_column] - HEAT_THRESHOLD_C).clip(lower=0.0)

    in_season = _season(work, season)
    peak = _season(work, (PEAK_MONTH,))
    rows = pd.DataFrame({
        f"{label}heat_dd_season": in_season.groupby("year")["excess"].sum(),
        f"{label}heat_dd_july": peak.groupby("year")["excess"].sum(),
        f"{label}tmax_july": peak.groupby("year")[tmax_column].mean(),
    })
    if precip_column is not None and precip_column in work.columns:
        summer = _season(work, SUMMER_MONTHS)
        rows[f"{label}precip_summer"] = summer.groupby("year")[precip_column].sum()
    # A year the archive only partly covers would otherwise contribute a
    # half-season sum that looks like a mild year rather than a missing one.
    rows[f"{label}days"] = in_season.groupby("year").size()
    return rows


def annual_drought(belt: pd.DataFrame, *,
                   season: tuple[int, ...] | range = range(4, 11)) -> pd.DataFrame:
    """Peak and mean severe-drought coverage within each growing season.

    The peak matters more than the average: a crop killed in August is not
    rescued by a wet April, so the worst week is the better summary.
    """
    if belt.empty:
        return pd.DataFrame(columns=["year"]).set_index("year")
    work = belt.copy()
    work["year"] = work["map_date"].dt.year
    in_season = work.loc[work["map_date"].dt.month.isin(list(season))]
    return pd.DataFrame({
        "peak_d2": in_season.groupby("year")["d2"].max(),
        "mean_d2": in_season.groupby("year")["d2"].mean(),
        "peak_d0": in_season.groupby("year")["d0"].max(),
        "maps": in_season.groupby("year").size(),
    })


def detrend_pct(values: pd.Series, *, fit_years: np.ndarray | None = None) -> pd.Series:
    """Deviation from a linear trend, as a fraction of the trend.

    `fit_years` restricts the regression to those index values, so a
    cross-validation fold can fit the trend without seeing its held-out year.
    Passing None fits on everything, which is right for a descriptive table and
    wrong for anything scored out of sample.
    """
    series = values.dropna()
    if len(series) < 3:
        return pd.Series(np.nan, index=values.index, dtype="float64")
    years = series.index.to_numpy(dtype="float64")
    if fit_years is None:
        mask = np.ones(len(series), dtype=bool)
    else:
        mask = np.isin(series.index.to_numpy(), np.asarray(fit_years))
    if mask.sum() < 3:
        return pd.Series(np.nan, index=values.index, dtype="float64")
    coefficients = np.polyfit(years[mask], series.to_numpy(dtype="float64")[mask], 1)
    trend = np.polyval(coefficients, years)
    # A trend extrapolated to zero or negative would invert the sign of every
    # deviation; for crop yields it never happens, but it must not pass silently.
    if np.any(trend <= 0):
        raise ValueError("the fitted yield trend is non-positive; a percentage "
                         "deviation from it would be meaningless")
    return pd.Series((series.to_numpy() - trend) / trend,
                     index=series.index).reindex(values.index)


def corn_yield(yields: pd.DataFrame, *, entity_prefix: str = "United States") -> pd.Series:
    """Annual US maize yield, indexed by year.

    Entity spelling is not stable across the yield datasets, so this matches on a
    prefix exactly as the preliminary analysis does.
    """
    rows = yields.loc[(yields["crop"] == "corn")
                      & yields["entity"].str.startswith(entity_prefix)]
    if rows.empty:
        raise SystemExit(f"no corn yields for an entity starting {entity_prefix!r}")
    return (rows.set_index("year")["yield_t_per_ha"]
            .sort_index()
            .astype("float64"))
