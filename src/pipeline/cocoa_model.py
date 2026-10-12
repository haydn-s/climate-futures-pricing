"""Point-in-time cocoa features and execution-aligned LSEG return targets.

This module deliberately starts from dated ICE-US cocoa contracts rather than
the Yahoo continuation.  A decision is made after the final session of each
week, enters the same dated contract at the next available open, and exits five
sessions later.  Weather is joined by NASA POWER publication date, so the model
never receives an observation before it was available.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .signals import TargetSpec

COCOA_TARGET = TargetSpec("cocoa", "ICE-US cocoa", 1)
HEAT_THRESHOLD_C = 32.0
DRY_DAY_MM = 1.0
WET_DAY_MM = 20.0
ROLL_BUSINESS_DAYS = 10
HOLDING_SESSIONS = 5
COMMISSION_USD_PER_SIDE = 2.50
SLIPPAGE_TICKS_PER_SIDE = 1.0
TICK_VALUE_USD = 10.0
CONTRACT_SIZE_TONNES = 10.0

WEATHER_FEATURES = (
    "rain7", "rain30", "rain90",
    "rain30_z", "rain90_z",
    "dry_days30", "dry_days90", "max_dry_spell30", "max_dry_spell90",
    "wet_days30", "wet_days90", "heat_dd32_30", "heat_dd32_90",
    "tmax_mean30", "weather_countries",
)
MARKET_FEATURES = (
    "cocoa_mom5", "cocoa_mom20", "cocoa_mom60", "cocoa_vol20",
    "volume_z20", "open_interest_chg20",
)
SEASONAL_FEATURES = ("month_sin", "month_cos")
FEATURE_SETS = {
    "weather_only": (*WEATHER_FEATURES, *SEASONAL_FEATURES),
    "market_only": (*MARKET_FEATURES, *SEASONAL_FEATURES),
    "weather_market": (*WEATHER_FEATURES, *MARKET_FEATURES, *SEASONAL_FEATURES),
}


class CocoaModelError(ValueError):
    """The cocoa contract or weather panel cannot support a model frame."""


def _longest_run(values: np.ndarray) -> float:
    longest = current = 0
    for value in values:
        current = current + 1 if bool(value) else 0
        longest = max(longest, current)
    return float(longest)


def _prior_year_z(frame: pd.DataFrame, column: str) -> pd.Series:
    """Calendar-day anomaly using strictly earlier years only."""

    work = frame[["date", column]].copy()
    work["month_day"] = work["date"].dt.month * 100 + work["date"].dt.day
    work["year"] = work["date"].dt.year
    work = work.sort_values(["month_day", "year"])
    grouped = work.groupby("month_day", sort=False)[column]
    mean = grouped.transform(lambda values: values.expanding().mean().shift(1))
    std = grouped.transform(lambda values: values.expanding().std().shift(1))
    return ((work[column] - mean) / std.replace(0.0, np.nan)).reindex(frame.index)


def cocoa_weather_features(power: pd.DataFrame) -> pd.DataFrame:
    """Build country-balanced daily cocoa weather known as of publication."""

    required = {"crop", "country", "date", "tmax_c", "precip_mm", "publication_date"}
    missing = sorted(required - set(power.columns))
    if missing:
        raise CocoaModelError("NASA POWER table is missing: " + ", ".join(missing))
    rows = power.loc[power["crop"] == "cocoa"].copy()
    if rows.empty:
        raise CocoaModelError("NASA POWER table contains no cocoa observations")
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows["publication_date"] = pd.to_datetime(rows["publication_date"]).dt.normalize()

    # Average multiple locations inside a country first, then countries. This
    # prevents Côte d'Ivoire and Ghana from receiving twice the spatial weight
    # of countries currently represented by one point.
    countries = (rows.groupby(["country", "date"], as_index=False)
                 .agg(tmax_c=("tmax_c", "mean"), precip_mm=("precip_mm", "mean"),
                      publication_date=("publication_date", "max")))
    countries["dry_share"] = (countries["precip_mm"] < DRY_DAY_MM).astype(float)
    countries["wet_share"] = (countries["precip_mm"] > WET_DAY_MM).astype(float)
    countries["heat_excess"] = (
        countries["tmax_c"] - HEAT_THRESHOLD_C
    ).clip(lower=0.0)
    daily = (countries.groupby("date", as_index=False)
             .agg(tmax_c=("tmax_c", "mean"), precip_mm=("precip_mm", "mean"),
                  dry_share=("dry_share", "mean"), wet_share=("wet_share", "mean"),
                  heat_excess=("heat_excess", "mean"),
                  publication_date=("publication_date", "max"),
                  weather_countries=("country", "nunique"))
             .sort_values("date", ignore_index=True))

    regional_dry = (daily["dry_share"] >= 0.5).astype(float)
    for window in (7, 30, 90):
        daily[f"rain{window}"] = daily["precip_mm"].rolling(
            window, min_periods=window).sum()
    for window in (30, 90):
        daily[f"dry_days{window}"] = daily["dry_share"].rolling(
            window, min_periods=window).sum()
        daily[f"wet_days{window}"] = daily["wet_share"].rolling(
            window, min_periods=window).sum()
        daily[f"heat_dd32_{window}"] = daily["heat_excess"].rolling(
            window, min_periods=window).sum()
        daily[f"max_dry_spell{window}"] = regional_dry.rolling(
            window, min_periods=window).apply(_longest_run, raw=True)
    daily["tmax_mean30"] = daily["tmax_c"].rolling(30, min_periods=30).mean()
    for column in ("rain30", "rain90"):
        daily[f"{column}_z"] = _prior_year_z(daily, column)
    return daily[["date", "publication_date", *WEATHER_FEATURES]]


def cocoa_front_contracts(contracts: pd.DataFrame, *,
                          roll_business_days: int = ROLL_BUSINESS_DAYS) -> pd.DataFrame:
    """Select the nearest cocoa contract that remains outside the roll window."""

    required = {
        "date", "root", "contract", "last_trade_date", "open_cents",
        "settlement_cents", "volume", "open_interest",
    }
    missing = sorted(required - set(contracts.columns))
    if missing:
        raise CocoaModelError("contract panel is missing: " + ", ".join(missing))
    clean = contracts.loc[contracts["root"] == "CC"].copy()
    if clean.empty:
        raise CocoaModelError("contract panel contains no CC rows")
    clean["date"] = pd.to_datetime(clean["date"]).dt.normalize()
    clean["last_trade_date"] = pd.to_datetime(clean["last_trade_date"]).dt.normalize()
    clean["business_days_to_last_trade"] = clean.apply(
        lambda row: int(np.busday_count(row["date"].date(),
                                       row["last_trade_date"].date())), axis=1)

    by_contract = clean.sort_values(["contract", "date"]).groupby("contract", sort=False)
    clean["contract_return"] = by_contract["settlement_cents"].pct_change(fill_method=None)
    by_contract = clean.groupby("contract", sort=False)
    for window in (5, 20, 60):
        clean[f"cocoa_mom{window}"] = by_contract["settlement_cents"].pct_change(
            window, fill_method=None)
    clean["cocoa_vol20"] = by_contract["contract_return"].transform(
        lambda values: values.rolling(20, min_periods=15).std() * np.sqrt(252))
    log_volume = np.log1p(clean["volume"].clip(lower=0))
    clean["volume_z20"] = by_contract[log_volume.name].transform(
        lambda values: (np.log1p(values.clip(lower=0))
                        - np.log1p(values.clip(lower=0)).rolling(20, min_periods=15).mean())
                       / np.log1p(values.clip(lower=0)).rolling(20, min_periods=15).std())
    clean["open_interest_chg20"] = by_contract["open_interest"].pct_change(
        20, fill_method=None)

    eligible = clean.loc[clean["business_days_to_last_trade"] > roll_business_days]
    return (eligible.sort_values(["date", "last_trade_date", "contract"])
            .drop_duplicates("date", keep="first")
            .sort_values("date", ignore_index=True))


def attach_execution_target(front: pd.DataFrame, contracts: pd.DataFrame, *,
                            holding_sessions: int = HOLDING_SESSIONS) -> pd.DataFrame:
    """Attach next-open, same-contract outcomes and row-specific round-trip cost."""

    if holding_sessions < 1:
        raise CocoaModelError("holding_sessions must be at least one")
    panel = contracts.loc[contracts["root"] == "CC"].copy()
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    panel = panel.sort_values(["contract", "date"])
    histories = {name: rows.reset_index(drop=True)
                 for name, rows in panel.groupby("contract", sort=False)}
    rows: list[dict[str, object]] = []
    round_trip_cost_usd = 2 * (
        COMMISSION_USD_PER_SIDE + SLIPPAGE_TICKS_PER_SIDE * TICK_VALUE_USD
    )
    for decision in front.itertuples(index=False):
        history = histories[str(decision.contract)]
        later = history.loc[history["date"] > decision.date]
        if len(later) <= holding_sessions:
            continue
        entry = later.iloc[0]
        exit_row = later.iloc[holding_sessions]
        entry_price = float(entry["open_cents"])
        exit_price = float(exit_row["open_cents"])
        notional = entry_price * CONTRACT_SIZE_TONNES / 100.0
        rows.append({
            "date": decision.date,
            "entry_date": entry["date"],
            "exit_date": exit_row["date"],
            "execution_contract": decision.contract,
            "entry_open_cents": entry_price,
            "exit_open_cents": exit_price,
            "target_cocoa_5": exit_price / entry_price - 1.0,
            "cost_rate": round_trip_cost_usd / notional,
        })
    targets = pd.DataFrame(rows)
    return front.merge(targets, on="date", how="inner", validate="one_to_one")


def weekly_model_frame(contracts: pd.DataFrame, power: pd.DataFrame) -> pd.DataFrame:
    """Return one non-overlapping weekly cocoa decision with all model inputs."""

    front = cocoa_front_contracts(contracts)
    front = attach_execution_target(front, contracts)
    week = front["date"].dt.to_period("W-FRI")
    weekly = front.groupby(week, sort=True, as_index=False).tail(1).copy()

    weather = cocoa_weather_features(power).dropna(subset=["publication_date"])
    left = weekly.sort_values("date")
    right = weather.sort_values("publication_date")
    left["date"] = left["date"].astype("datetime64[us]")
    right["publication_date"] = right["publication_date"].astype("datetime64[us]")
    frame = pd.merge_asof(
        left, right, left_on="date", right_on="publication_date",
        direction="backward", suffixes=("", "_weather"),
    )
    frame["year"] = frame["date"].dt.year
    phase = 2.0 * np.pi * frame["date"].dt.month / 12.0
    frame["month_sin"] = np.sin(phase)
    frame["month_cos"] = np.cos(phase)
    return frame.sort_values("date", ignore_index=True)
