"""The point-in-time feature panel: what was knowable about the weather on each
trading day, lined up against what corn did next.

    from pipeline.features import panel
    frame = panel.build(processed, geography, crop="corn", peek_days=0)

One row per trading day. Features describe the weather as it had been PUBLISHED
by that day; targets are forward returns. `peek_days` is the experiment, not a
convenience -- see below.

WHY peek_days EXISTS. The preliminary result this project rests on is that corn
returns line up with drought increases published one to three weeks LATER: the
market moves before the data lands. A model fed only published data is therefore
predicting something already in the price, and near-zero skill is the EXPECTED
outcome rather than a bug. So the information set is the experimental variable.
`peek_days=0` is the honest, tradeable baseline. `peek_days=7` lets a feature use
data published up to a week after the observation date -- untradeable by
construction, and the point: the skill it buys measures how much of the signal
arrives too late to trade, which is "how early does the market know" stated as a
number. Sweep it; never report a positive peek as a result.

WHICH SOURCES CAN BE TRADED, which is a publication-lag question and not a data
quality one:

  usdm        2 days, weekly       tradeable intraseason
  nasa_power  3-5 days, daily      tradeable intraseason
  nclimgrid   ~1 MONTH, monthly    NOT tradeable intraseason

nClimGrid finalises a month on about the 7th of the NEXT month, so July's heat --
the thing that makes or breaks a US corn crop -- is public in August, long after
the price has moved. It is the better measurement (true county averages against
NASA POWER's one representative point per state) and so it belongs to the yield
validation, not to the price model. `build` carries both and labels each feature
with its source, so a model can select on `tradeable_only` rather than on a
reader remembering which is which. This is the one trap in here that produces a
beautiful backtest instead of an error.

Two further honesties:

* Anomalies are z-scored against the same day-of-year in STRICTLY EARLIER
  calendar years, so no observation is standardised using its own future. Early
  years therefore have no anomaly (NaN) until enough prior years exist; they are
  not dropped here, because whether to drop them is the model's decision.
* Points and counties are EQUALLY weighted, which is the limitation the README
  already names. `weights` is threaded through for the USDA county production
  shares that land once NASS_API_KEY is configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import Geography
from ..storage import ProcessedStore

# Schlenker & Roberts (2009): US corn yields fall sharply above about 29 C, which
# is why heat enters as degree-days ABOVE a threshold rather than as a mean.
HEAT_THRESHOLD_C = 29.0

# Trailing accumulation windows, in days.
HEAT_WINDOWS = (7, 14, 30)
PRECIP_WINDOWS = (30, 60)

# Drought changes over 1, 2 and 4 weekly maps -- the horizons the preliminary
# lead-lag test found prices moving ahead of.
DROUGHT_DELTAS = (1, 2, 4)

# Forward return horizons, in trading days (about one and two weeks).
DEFAULT_HORIZONS = (5, 10)

# Publication lag of the nClimGrid monthly product, in days; see the module
# docstring. Carried as a constant so the untradeable features can be labelled
# without re-reading config.
TRADEABLE_SOURCES = ("usdm", "nasa_power")


@dataclass(frozen=True)
class PanelSpec:
    """What a panel was built from, so a result can be reproduced from the frame."""

    crop: str
    peek_days: int
    horizons: tuple[int, ...]
    price_series: tuple[str, ...]
    feature_sources: dict[str, str] = field(default_factory=dict)
    # Features the schema declares but no archived data could fill: present as
    # all-NaN columns rather than absent, so a partially backfilled store and a
    # complete one produce the SAME column set. A model that silently trains on
    # whichever features happened to exist is not reproducible, and the quiet
    # version of this bug reads as a modelling result.
    unavailable: tuple[str, ...] = ()

    @property
    def tradeable_features(self) -> tuple[str, ...]:
        return tuple(name for name, source in self.feature_sources.items()
                     if source in TRADEABLE_SOURCES)

    @property
    def untradeable_features(self) -> tuple[str, ...]:
        return tuple(name for name, source in self.feature_sources.items()
                     if source not in TRADEABLE_SOURCES)


# ------------------------------------------------------------------ aggregation


def _prior_year_z(frame: pd.DataFrame, column: str, date_column: str = "date") -> pd.Series:
    """Z-score each value against the same day-of-year in strictly earlier years.

    Standardising against the whole sample would leak the future into every
    observation, which is the one thing this project cannot afford.

    The calendar key is (month, day) and NOT day-of-year. Day-of-year drifts by a
    day after February in a leap year, so July 31 is doy 213 in 2012 against 212
    in 2011: a doy key compares a date to the wrong calendar day, and in a series
    that does not span the whole year it finds NO prior observation at all and
    returns NaN where data plainly exists. February 29 is then matched only
    against other February 29s, which is fewer observations but the right ones.
    """
    work = frame[[date_column, column]].copy()
    work["month_day"] = (work[date_column].dt.month * 100 + work[date_column].dt.day)
    work["year"] = work[date_column].dt.year
    work = work.sort_values(["month_day", "year"])
    grouped = work.groupby("month_day", sort=False)[column]
    mean = grouped.transform(lambda s: s.expanding().mean().shift(1))
    std = grouped.transform(lambda s: s.expanding().std().shift(1))
    z = (work[column] - mean) / std.replace(0.0, np.nan)
    return z.reindex(frame.index)


def belt_weather(power: pd.DataFrame, crop: str) -> pd.DataFrame:
    """Equal-weighted daily tmax and precip across a crop's points, with the date
    the BELT average became knowable -- the last point to publish, not the first.
    """
    rows = power.loc[power["crop"] == crop]
    if rows.empty:
        return pd.DataFrame(columns=["date", "tmax_c", "precip_mm", "publication_date", "points"])
    daily = (rows.groupby("date", as_index=False)
             .agg(tmax_c=("tmax_c", "mean"),
                  precip_mm=("precip_mm", "mean"),
                  publication_date=("publication_date", "max"),
                  points=("point", "nunique"))
             .sort_values("date", ignore_index=True))

    # TIME-BASED WINDOWS, not positional ones. A plain .rolling(7) counts rows,
    # so a gap in the series (an unfetched year, a source that skips a day)
    # silently reaches across it and sums seven ARBITRARY days as if they were
    # seven consecutive ones. "7D" is bounded by the calendar instead, and
    # min_periods still demands a full window, so a gap yields NaN rather than a
    # plausible wrong number.
    daily["heat_excess_c"] = (daily["tmax_c"] - HEAT_THRESHOLD_C).clip(lower=0.0)
    for window in HEAT_WINDOWS:
        daily[f"heat_dd{window}"] = (
            daily.rolling(f"{window}D", on="date", min_periods=window)["heat_excess_c"].sum())
    for window in PRECIP_WINDOWS:
        daily[f"precip{window}"] = (
            daily.rolling(f"{window}D", on="date", min_periods=window)["precip_mm"].sum())
        daily[f"precip{window}_z"] = _prior_year_z(daily, f"precip{window}")
    for window in HEAT_WINDOWS:
        daily[f"heat_dd{window}_z"] = _prior_year_z(daily, f"heat_dd{window}")

    # An accumulation ending on day d is knowable only once day d is published,
    # and the lag is monotone, so the window's own last publication date is the
    # binding one. Taken as a rolling max rather than assumed.
    daily["publication_date"] = daily["publication_date"].cummax()
    return daily


def belt_drought(usdm: pd.DataFrame, states: tuple[str, ...] | None = None) -> pd.DataFrame:
    """Equal-weighted drought coverage across the belt, one row per weekly map.

    Counties are averaged within a state and states across the belt, so a state
    with many small counties does not outvote one with few large ones. d2 is
    "severe drought or worse" under statisticsType=1, which is what the
    preliminary analysis reads.
    """
    rows = usdm if states is None else usdm.loc[usdm["state"].isin(states)]
    if rows.empty:
        return pd.DataFrame(columns=["map_date", "publication_date", "d0", "d1", "d2", "d3", "d4"])
    severity = ["d0", "d1", "d2", "d3", "d4"]
    by_state = rows.groupby(["state", "map_date"], as_index=False)[severity].mean()
    belt = (by_state.groupby("map_date", as_index=False)[severity].mean()
            .sort_values("map_date", ignore_index=True))
    publication = (rows.groupby("map_date", as_index=False)["publication_date"].max()
                   .sort_values("map_date", ignore_index=True))
    belt = belt.merge(publication, on="map_date", how="left")
    belt["states"] = rows.groupby("map_date")["state"].nunique().to_numpy()

    for lag in DROUGHT_DELTAS:
        belt[f"d2_delta{lag}"] = belt["d2"].diff(lag)
        belt[f"d0_delta{lag}"] = belt["d0"].diff(lag)
    belt["publication_date"] = belt["publication_date"].cummax()
    return belt


def belt_counties(nclimgrid: pd.DataFrame, states: tuple[str, ...] | None = None) -> pd.DataFrame:
    """County-average heat for the belt: the better measurement, a month late.

    Equal-weighted within state then across states, matching belt_drought so the
    two aggregates are comparable. See the module docstring on why these features
    are labelled untradeable rather than left out.
    """
    rows = nclimgrid if states is None else nclimgrid.loc[nclimgrid["state"].isin(states)]
    if rows.empty:
        return pd.DataFrame(columns=["date", "publication_date", "cty_tmax_c", "cty_prcp_mm"])
    by_state = (rows.groupby(["state", "date"], as_index=False)
                .agg(tmax_c=("tmax_c", "mean"), prcp_mm=("prcp_mm", "mean"),
                     publication_date=("publication_date", "max")))
    belt = (by_state.groupby("date", as_index=False)
            .agg(cty_tmax_c=("tmax_c", "mean"), cty_prcp_mm=("prcp_mm", "mean"),
                 publication_date=("publication_date", "max"))
            .sort_values("date", ignore_index=True))
    belt["cty_heat_excess_c"] = (belt["cty_tmax_c"] - HEAT_THRESHOLD_C).clip(lower=0.0)
    for window in HEAT_WINDOWS:
        belt[f"cty_heat_dd{window}"] = (
            belt.rolling(f"{window}D", on="date", min_periods=window)["cty_heat_excess_c"].sum())
    belt["publication_date"] = belt["publication_date"].cummax()
    return belt


# ------------------------------------------------------------------- the panel


def _as_of_join(grid: pd.DataFrame, timeline: pd.DataFrame, columns: list[str],
                peek_days: int) -> pd.DataFrame:
    """Attach each observation the most recent row published by its cutoff.

    The vectorised form of pipeline.calendar.as_of over many as-of dates: same
    rule (published on or before the cutoff, missing publication dates dropped),
    one pass instead of one filter per row. Kept consistent with as_of
    deliberately -- tests/test_features.py checks the two agree.
    """
    if timeline.empty:
        return pd.DataFrame(index=grid.index, columns=columns, dtype="float64")
    right = timeline.loc[timeline["publication_date"].notna(), ["publication_date", *columns]]
    right = right.sort_values("publication_date", ignore_index=True)
    left = grid[["cutoff"]].reset_index(names="_grid_index").sort_values("cutoff")
    # BOTH KEYS MUST CARRY THE SAME DATETIME RESOLUTION. Parquet round-trips these
    # columns as datetime64[ms] while `date + Timedelta` produces [us], and pandas
    # 3.0 raises MergeError on the mismatch rather than coercing -- an error here
    # is the good outcome, since a silent coercion is a silent off-by-one on
    # exactly the comparison this project measures.
    unit = "datetime64[us]"
    left["cutoff"] = left["cutoff"].astype(unit)
    right["publication_date"] = right["publication_date"].astype(unit)
    merged = pd.merge_asof(left, right, left_on="cutoff", right_on="publication_date",
                           direction="backward")
    return (merged.set_index("_grid_index")[columns]
            .reindex(grid.index))


def build(processed: ProcessedStore, geography: Geography, *, crop: str = "corn",
          peek_days: int = 0, horizons: tuple[int, ...] = DEFAULT_HORIZONS,
          price_series: tuple[str, ...] = ("corn", "corn_fund"),
          sensitive_months_only: bool = False) -> tuple[pd.DataFrame, PanelSpec]:
    """One row per trading day: published weather features, forward returns.

    `peek_days` shifts every feature cutoff forward, letting features use data
    published after the observation date. Zero is the tradeable baseline; a
    positive value is the measurement described in the module docstring and is
    never a result on its own.
    """
    if peek_days < 0:
        raise ValueError(f"peek_days must be >= 0, found {peek_days}")
    crop_spec = geography.crop(crop)
    states = tuple(state.postal for state in crop_spec.states) or None

    prices = processed.read("yahoo_prices_daily")
    available = set(prices["series"].unique())
    missing = [series for series in price_series if series not in available]
    if missing:
        raise SystemExit(
            f"price series {missing} absent from yahoo_prices_daily "
            f"(present: {', '.join(sorted(available))}); fetch and clean yahoo_prices first")

    # The grid is the primary contract's trading days: the dates on which a
    # position could actually have been taken.
    primary = price_series[0]
    grid = (prices.loc[prices["series"] == primary, ["date", "close"]]
            .dropna(subset=["close"])
            .sort_values("date", ignore_index=True)
            .rename(columns={"close": f"close_{primary}"}))
    grid["cutoff"] = grid["date"] + pd.Timedelta(days=peek_days)

    feature_sources: dict[str, str] = {}
    unavailable: list[str] = []

    def attach(timeline: pd.DataFrame, columns: list[str], source: str) -> None:
        """Join a feature block, declaring every requested column either way.

        A column its timeline cannot fill is created as all-NaN and recorded in
        `spec.unavailable`, so the panel's schema does not depend on how far the
        backfill has got. See PanelSpec.unavailable.
        """
        present = [column for column in columns if column in timeline.columns]
        joined = _as_of_join(grid, timeline, present, peek_days)
        for column in columns:
            if column in present and joined[column].notna().any():
                grid[column] = joined[column].to_numpy()
            else:
                grid[column] = np.nan
                unavailable.append(column)
            feature_sources[column] = source

    drought = belt_drought(processed.read("usdm_county_drought"), states)
    attach(drought, ["d0", "d2", "d3",
                     *[f"d2_delta{lag}" for lag in DROUGHT_DELTAS],
                     *[f"d0_delta{lag}" for lag in DROUGHT_DELTAS]], "usdm")

    weather = belt_weather(processed.read("nasa_power_point_daily"), crop)
    attach(weather, [*[f"heat_dd{w}" for w in HEAT_WINDOWS],
                     *[f"heat_dd{w}_z" for w in HEAT_WINDOWS],
                     *[f"precip{w}" for w in PRECIP_WINDOWS],
                     *[f"precip{w}_z" for w in PRECIP_WINDOWS]], "nasa_power")

    counties = (belt_counties(processed.read("nclimgrid_county_daily"), states)
                if "nclimgrid_county_daily" in processed.names() else pd.DataFrame())
    attach(counties, ["cty_tmax_c", *[f"cty_heat_dd{w}" for w in HEAT_WINDOWS]], "nclimgrid")

    # Targets. Forward returns are the only thing here allowed to see the future.
    for series in price_series:
        closes = (prices.loc[prices["series"] == series, ["date", "close"]]
                  .dropna(subset=["close"]).sort_values("date", ignore_index=True))
        aligned = grid[["date"]].merge(closes, on="date", how="left")["close"]
        grid[f"close_{series}"] = aligned.to_numpy()
        for horizon in horizons:
            grid[f"fwd{horizon}_{series}"] = (aligned.shift(-horizon) / aligned - 1.0).to_numpy()

    grid["year"] = grid["date"].dt.year
    grid["week"] = grid["date"].dt.isocalendar().week.astype("int64")
    grid["month"] = grid["date"].dt.month
    if sensitive_months_only:
        window = crop_spec.sensitive_months
        months = (range(window.start, window.end + 1) if window.start <= window.end
                  else [*range(window.start, 13), *range(1, window.end + 1)])
        grid = grid.loc[grid["month"].isin(list(months))].reset_index(drop=True)

    spec = PanelSpec(crop=crop, peek_days=peek_days, horizons=tuple(horizons),
                     price_series=tuple(price_series), feature_sources=feature_sources,
                     unavailable=tuple(unavailable))
    return grid.drop(columns=["cutoff"]), spec
