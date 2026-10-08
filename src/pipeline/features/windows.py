"""Weather measures over a named window of a crop year.

Shared by the cocoa and coffee analyses, because the moment each keeps its own
copy one of them loses the coverage guard and starts reporting a half-season as
a mild one.

    OCT_TO_SEP = months(start=10, end=9)          # wraps the new year
    features = window(daily, OCT_TO_SEP, 2012)    # None if under-covered

WHY A WINDOW CAN WRAP. A crop year is not a calendar year for most of the world.
West African cocoa fills pods from June and is harvested from October, so its
Harmattan window runs December to February across a year boundary; Brazilian
coffee flowers in September and is picked the following May. A month therefore
carries an offset: (12, -1) is last December. Corn is the exception rather than
the rule, which is why features.annual -- written for corn -- takes plain month
numbers and this does not.

WHAT IS MEASURED, and why each one is here rather than just a mean:

  rain_mm         total precipitation
  dry_days        days under DRY_DAY_MM, which is drought stress
  max_dry_spell   the longest run of them, because a crop cares about the worst
                  stretch and not the count: thirty scattered dry days and
                  thirty consecutive ones are different weather
  wet_days        days over WET_DAY_MM, which is what spreads fungal disease --
                  rainfall damages in both directions and a total hides that
  heat_dd         degree-days above a per-crop threshold, not a mean, because
                  damage is a threshold effect
  heat_days       days over it
  tmax_mean       the mean, for comparison with the thresholded measures
  frost_days      days whose MINIMUM fell below a per-crop threshold
  min_tmin        the coldest night in the window

FROST NEEDS tmin AND MOST SOURCES DO NOT CARRY IT. NASA POWER only began serving
T2M_MIN into this project when coffee was added, so `frost_days` and `min_tmin`
are NaN for any frame without a tmin_c column rather than silently zero -- a
frost count of zero and "we did not look" are different claims, and for a crop
whose defining event is a frost they are opposite ones.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DRY_DAY_MM = 1.0
WET_DAY_MM = 20.0

# Per-crop, because a shade tree and a grain do not share a stress point.
HEAT_THRESHOLD_COCOA = 32.0
HEAT_THRESHOLD_COFFEE = 30.0
# Arabica is damaged at a few degrees above freezing, before air temperature
# reaches zero, because radiative cooling at leaf level runs colder than the
# screen height a reanalysis reports.
FROST_THRESHOLD_COFFEE = 4.0

MEASURES = ("rain_mm", "dry_days", "max_dry_spell", "wet_days",
            "heat_dd", "heat_days", "tmax_mean", "frost_days", "min_tmin")

# A window is only read when it is nearly fully covered; 28 days a month is the
# floor that lets February through without letting half a season through.
MIN_DAYS_PER_MONTH = 28


def months(*, start: int, end: int) -> list[tuple[int, int]]:
    """Month numbers with a year offset, wrapping the new year when start > end.

    months(start=10, end=9) is October of the previous year through September of
    this one -- a full crop year. months(start=6, end=9) is this June to
    September. The offset is what makes a wrapping window expressible at all.
    """
    if not (1 <= start <= 12 and 1 <= end <= 12):
        raise ValueError(f"months must be 1-12, found start={start} end={end}")
    if start <= end:
        return [(month, 0) for month in range(start, end + 1)]
    return ([(month, -1) for month in range(start, 13)]
            + [(month, 0) for month in range(1, end + 1)])


def window(daily: pd.DataFrame, calendar: list[tuple[int, int]], year: int, *,
           heat_threshold: float, frost_threshold: float | None = None,
           min_days_per_month: int = MIN_DAYS_PER_MONTH) -> dict[str, float] | None:
    """Every measure over one window of one crop year, or None if under-covered.

    None rather than a partial sum: a window the archive only half covers would
    total to something that reads as a mild season instead of a missing one,
    which is the quiet failure this guard exists for.
    """
    if daily.empty:
        return None
    wanted = {(year + offset, month) for month, offset in calendar}
    stamps = daily["date"]
    keep = [(stamp.year, stamp.month) in wanted for stamp in stamps]
    rows = daily.loc[keep]
    if len(rows) < len(calendar) * min_days_per_month:
        return None

    precip = rows["precip_mm"].to_numpy(dtype="float64")
    tmax = rows["tmax_c"].to_numpy(dtype="float64")
    dry = precip < DRY_DAY_MM

    spells, run = [], 0
    for is_dry in dry:
        run = run + 1 if is_dry else 0
        spells.append(run)

    features = {
        "rain_mm": float(np.nansum(precip)),
        "dry_days": float(dry.sum()),
        "max_dry_spell": float(max(spells) if spells else 0),
        "wet_days": float((precip > WET_DAY_MM).sum()),
        "heat_dd": float(np.nansum(np.clip(tmax - heat_threshold, 0.0, None))),
        "heat_days": float((tmax > heat_threshold).sum()),
        "tmax_mean": float(np.nanmean(tmax)),
        "days": float(len(rows)),
    }

    # NaN and not zero where tmin is absent: "no frost" and "no thermometer" are
    # different claims, and for coffee they are opposite ones.
    if "tmin_c" in rows.columns and frost_threshold is not None:
        tmin = rows["tmin_c"].to_numpy(dtype="float64")
        if np.isfinite(tmin).any():
            features["frost_days"] = float(np.nansum(tmin < frost_threshold))
            features["min_tmin"] = float(np.nanmin(tmin))
            return features
    features["frost_days"] = float("nan")
    features["min_tmin"] = float("nan")
    return features


def table(daily: pd.DataFrame, calendar: list[tuple[int, int]], years: range, *,
          heat_threshold: float, frost_threshold: float | None = None) -> pd.DataFrame:
    """One row per crop year, indexed by year, for the years that are covered."""
    rows = {}
    for year in years:
        features = window(daily, calendar, year, heat_threshold=heat_threshold,
                          frost_threshold=frost_threshold)
        if features is not None:
            rows[year] = features
    return pd.DataFrame(rows).T

# How each measure combines across the points of one growing region. FROST IS
# NOT AVERAGED, and this is the correction that made coffee work at all: damage
# from a cold night is local, so the belt is as frost-stricken as its COLDEST
# point, not as its average one. Averaging Brazil's three arabica points first
# put every year's minimum above a 4 C threshold and made frost_days a constant
# zero across 27 years -- while the coldest single point fell below 4 C in six
# of them and ranked 2021, the year of the famous Minas Gerais frost, second
# coldest in the record. The same logic makes a dry spell a maximum: a region is
# as droughted as its worst location.
ACROSS_POINTS = {
    "min_tmin": "min",
    "frost_days": "max",
    "max_dry_spell": "max",
    "heat_dd": "mean",
    "heat_days": "mean",
    "tmax_mean": "mean",
    "rain_mm": "mean",
    "dry_days": "mean",
    "wet_days": "mean",
    "days": "mean",
}


def across_points(frames: dict[str, pd.DataFrame], calendar: list[tuple[int, int]],
                  year: int, *, heat_threshold: float,
                  frost_threshold: float | None = None) -> dict[str, float] | None:
    """One window computed per point, then combined by ACROSS_POINTS.

    `frames` maps a point name to its own daily frame. Returns None unless every
    point covers the window, so a region never silently shrinks to whichever of
    its points happened to be archived.
    """
    if not frames:
        return None
    per_point = {}
    for name, daily in frames.items():
        features = window(daily, calendar, year, heat_threshold=heat_threshold,
                          frost_threshold=frost_threshold)
        if features is None:
            return None
        per_point[name] = features

    combined: dict[str, float] = {}
    for measure, how in ACROSS_POINTS.items():
        values = [features[measure] for features in per_point.values()
                  if measure in features]
        finite = [value for value in values if not np.isnan(value)]
        if not finite:
            combined[measure] = float("nan")
        elif how == "min":
            combined[measure] = float(min(finite))
        elif how == "max":
            combined[measure] = float(max(finite))
        else:
            combined[measure] = float(np.mean(finite))
    combined["points"] = float(len(per_point))
    return combined


def table_across_points(frames: dict[str, pd.DataFrame],
                        calendar: list[tuple[int, int]], years: range, *,
                        heat_threshold: float,
                        frost_threshold: float | None = None) -> pd.DataFrame:
    rows = {}
    for year in years:
        features = across_points(frames, calendar, year, heat_threshold=heat_threshold,
                                 frost_threshold=frost_threshold)
        if features is not None:
            rows[year] = features
    return pd.DataFrame(rows).T
