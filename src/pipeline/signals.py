"""Point-in-time datasets for weather-conditioned commodity signals.

This module changes the prediction problem without changing the information set.
The existing panel asks whether published weather predicts raw corn returns. Here
the same as-of features are combined with crop stage and price regime variables,
then scored against both raw and market-neutral targets.

One row is retained per Drought Monitor publication week. That is the cadence at
which the core drought features change and, for the primary five-trading-day
horizon, prevents adjacent labels from overlapping.
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from . import events
from .config import Geography
from .features import panel
from .storage import ProcessedStore

PRICE_SERIES = ("corn", "soybeans", "wheat")
GROWING_MONTHS = tuple(range(4, 11))
BETA_WINDOW = 126
BETA_MIN_PERIODS = 63

WEATHER_FEATURES = (
    "d2_delta1", "d2_delta2", "d2_delta4",
    "d0_delta1", "d0_delta2", "d0_delta4",
    "heat_dd7_z", "heat_dd14_z", "precip30_z", "precip60_z",
    "stage_pre", "stage_pollination", "stage_harvest",
    "pollination_heat", "pollination_drought",
)

MARKET_FEATURES = (
    "corn_mom5", "corn_mom20", "soybeans_mom20", "wheat_mom20",
    "corn_vol20", "ag_vol20", "corn_beta_ag126", "corn_rel_mom20",
)

FORECAST_INPUTS = (
    "fc_610temp", "fc_610temp_rev", "fc_814temp", "fc_814temp_rev",
    "fc_610prcp", "fc_610prcp_rev", "fc_814prcp", "fc_814prcp_rev",
)

FORECAST_FEATURES = (
    *FORECAST_INPUTS,
    "pollination_temp_revision",
)

FEATURE_SETS = {
    "stage_weather": WEATHER_FEATURES,
    "stage_weather_market": (*WEATHER_FEATURES, *MARKET_FEATURES),
    "all_public": (*WEATHER_FEATURES, *MARKET_FEATURES, *FORECAST_FEATURES),
}


@dataclass(frozen=True)
class TargetSpec:
    """A return target and the number of traded legs needed to realise it."""

    name: str
    label: str
    legs: int

    def column(self, horizon: int) -> str:
        return f"target_{self.name}_{horizon}"


TARGETS = (
    TargetSpec("raw_corn", "raw corn", 1),
    TargetSpec("corn_wheat", "corn minus wheat", 2),
    TargetSpec("corn_ag_beta", "corn minus beta-adjusted ag basket", 2),
)


def _rolling_beta(corn: pd.Series, basket: pd.Series, *,
                  window: int = BETA_WINDOW,
                  min_periods: int = BETA_MIN_PERIODS) -> pd.Series:
    """Trailing corn beta to the agricultural basket, using only known returns."""
    covariance = corn.rolling(window, min_periods=min_periods).cov(basket)
    variance = basket.rolling(window, min_periods=min_periods).var()
    return (covariance / variance.replace(0.0, np.nan)).clip(lower=-3.0, upper=3.0)


def add_market_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add price regimes known before the close at which the target begins.

    The target enters at the decision-date close. Even a feature calculated
    from that close would therefore be unavailable when placing the trade.
    Shifting all market features one session keeps their information set
    strictly earlier than the target's entry price.
    """
    result = frame.copy()
    returns: dict[str, pd.Series] = {}
    for series in PRICE_SERIES:
        close = result[f"close_{series}"].astype(float)
        returns[series] = close.pct_change(fill_method=None)
        result[f"{series}_mom5"] = close.pct_change(5, fill_method=None)
        result[f"{series}_mom20"] = close.pct_change(20, fill_method=None)
        result[f"{series}_vol20"] = returns[series].rolling(20, min_periods=15).std() * np.sqrt(252)

    basket_return = pd.concat([returns["soybeans"], returns["wheat"]], axis=1).mean(axis=1)
    result["ag_vol20"] = basket_return.rolling(20, min_periods=15).std() * np.sqrt(252)
    result["corn_beta_ag126"] = _rolling_beta(returns["corn"], basket_return)
    basket_mom20 = (result["soybeans_mom20"] + result["wheat_mom20"]) / 2.0
    result["corn_rel_mom20"] = result["corn_mom20"] - result["corn_beta_ag126"] * basket_mom20
    result.loc[:, MARKET_FEATURES] = result.loc[:, MARKET_FEATURES].shift(1)
    return result


def lag_forecasts_with_unknown_release_time(frame: pd.DataFrame) -> pd.DataFrame:
    """Conservatively delay dated CPC forecasts until the next session.

    The source records issuance dates but not release times. A one-session lag
    prevents a forecast published after the grain close from informing a trade
    entered at that same close.
    """
    result = frame.copy()
    columns = [column for column in FORECAST_INPUTS if column in result.columns]
    result.loc[:, columns] = result.loc[:, columns].shift(1)
    return result


def add_stage_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Encode corn's pre-pollination, critical, and harvest stages."""
    result = frame.copy()
    result["stage_pre"] = result["month"].isin((4, 5, 6)).astype(float)
    result["stage_pollination"] = result["month"].isin((7, 8)).astype(float)
    result["stage_harvest"] = result["month"].isin((9, 10)).astype(float)
    result["pollination_heat"] = result["stage_pollination"] * result["heat_dd7_z"]
    result["pollination_drought"] = result["stage_pollination"] * result["d2_delta1"]
    result["pollination_temp_revision"] = (
        result["stage_pollination"] * result["fc_610temp_rev"])
    return result


def add_targets(frame: pd.DataFrame, horizons: tuple[int, ...]) -> pd.DataFrame:
    """Add raw, simple-spread, and rolling-beta market-neutral targets."""
    result = frame.copy()
    for horizon in horizons:
        corn = result[f"fwd{horizon}_corn"]
        soybeans = result[f"fwd{horizon}_soybeans"]
        wheat = result[f"fwd{horizon}_wheat"]
        basket = (soybeans + wheat) / 2.0
        result[f"target_raw_corn_{horizon}"] = corn
        result[f"target_corn_wheat_{horizon}"] = corn - wheat
        result[f"target_corn_ag_beta_{horizon}"] = corn - result["corn_beta_ag126"] * basket
    return result


def weekly_decisions(frame: pd.DataFrame, publications: pd.Series, *,
                     horizon: int) -> pd.DataFrame:
    """Align decisions to publications and thin longer horizons to non-overlap."""
    positions = events.trading_positions(publications, frame["date"])
    positions = np.unique(positions[positions >= 0])
    positions = np.asarray([
        position for position in positions
        if frame.iloc[position]["month"] in GROWING_MONTHS
    ], dtype="int64")

    # Space observations by actual trading rows, not calendar weeks. Holiday-
    # shortened weeks can otherwise make even five-session targets overlap.
    selected: list[int] = []
    next_allowed = 0
    for position in positions:
        if position >= next_allowed:
            selected.append(int(position))
            next_allowed = int(position) + horizon
    return frame.iloc[selected].reset_index(drop=True)


def build(processed: ProcessedStore, geography: Geography, *,
          horizons: tuple[int, ...] = (5,)) -> tuple[dict[int, pd.DataFrame], dict[str, tuple[str, ...]]]:
    """Build weekly signal frames and declare the feature sets they support."""
    daily, spec = panel.build(
        processed, geography, crop="corn", peek_days=0, horizons=horizons,
        price_series=PRICE_SERIES, sensitive_months_only=False)
    daily = add_market_features(daily)
    daily = lag_forecasts_with_unknown_release_time(daily)
    daily = add_stage_features(daily)
    daily = add_targets(daily, horizons)

    states = tuple(state.postal for state in geography.crop("corn").states)
    drought = panel.belt_drought(processed.read("usdm_county_drought"), states)
    publications = drought["publication_date"].dropna().drop_duplicates().sort_values()
    frames = {horizon: weekly_decisions(daily, publications, horizon=horizon)
              for horizon in horizons}

    available: dict[str, tuple[str, ...]] = {}
    for name, columns in FEATURE_SETS.items():
        usable = tuple(column for column in columns
                       if column in daily.columns and column not in spec.unavailable)
        available[name] = usable
    return frames, available
