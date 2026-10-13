"""Brazil-weather features and execution-aligned ICE Coffee C targets.

The fitted comparison uses non-overlapping 20-session decisions. Frost is kept
as a separate event study because the dated record contains too few independent
Brazilian freezes to support a fitted model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .signals import TargetSpec

COFFEE_OUTRIGHT_TARGET = TargetSpec("coffee_outright", "Coffee C outright", 1)
COFFEE_CURVE_TARGET = TargetSpec("coffee_curve", "Coffee C nearby minus deferred", 2)

ROLL_BUSINESS_DAYS = 25
HOLDING_SESSIONS = 20
FROST_THRESHOLD_C = 4.0
HEAT_THRESHOLD_C = 30.0
DRY_DAY_MM = 1.0
COMMISSION_USD_PER_SIDE = 2.50
SLIPPAGE_TICKS_PER_SIDE = 1.0
TICK_VALUE_USD = 18.75
CONTRACT_SIZE_POUNDS = 37_500.0

WEATHER_FEATURES = (
    "min_tmin7", "min_tmin30", "frost_days30",
    "rain30", "rain90", "rain90_z",
    "dry_days30", "dry_days90", "max_dry_spell30", "max_dry_spell90",
    "heat_dd30_30", "heat_dd30_90", "tmax_mean30", "brazil_points",
    "winter_min_tmin7", "winter_frost_days30",
    "flowering_rain90_z", "cherry_dry_spell30",
)
MARKET_FEATURES = (
    "coffee_mom5", "coffee_mom20", "coffee_mom60", "coffee_vol20",
    "volume_z20", "open_interest_chg20",
    "curve_slope", "curve_mom20", "curve_vol20",
)
SEASONAL_FEATURES = (
    "month_sin", "month_cos", "winter", "flowering", "cherry_fill",
)
FEATURE_SETS = {
    "market_only": (*MARKET_FEATURES, *SEASONAL_FEATURES),
    "weather_market": (*WEATHER_FEATURES, *MARKET_FEATURES, *SEASONAL_FEATURES),
}


class CoffeeModelError(ValueError):
    """The Coffee C contract or Brazil-weather panel is unsuitable."""


def _longest_run(values: np.ndarray) -> float:
    longest = current = 0
    for value in values:
        current = current + 1 if bool(value) else 0
        longest = max(longest, current)
    return float(longest)


def _prior_year_z(frame: pd.DataFrame, column: str) -> pd.Series:
    work = frame[["date", column]].copy()
    work["month_day"] = work["date"].dt.month * 100 + work["date"].dt.day
    work["year"] = work["date"].dt.year
    work = work.sort_values(["month_day", "year"])
    grouped = work.groupby("month_day", sort=False)[column]
    mean = grouped.transform(lambda values: values.expanding().mean().shift(1))
    std = grouped.transform(lambda values: values.expanding().std().shift(1))
    return ((work[column] - mean) / std.replace(0.0, np.nan)).reindex(frame.index)


def brazil_weather_features(power: pd.DataFrame) -> pd.DataFrame:
    """Build daily Brazil-arabica features known by publication date."""

    required = {
        "crop", "country", "point", "date", "tmin_c", "tmax_c", "precip_mm",
        "publication_date",
    }
    missing = sorted(required - set(power.columns))
    if missing:
        raise CoffeeModelError("NASA POWER table is missing: " + ", ".join(missing))
    rows = power.loc[
        (power["crop"] == "coffee") & (power["country"] == "Brazil")
    ].copy()
    if rows.empty:
        raise CoffeeModelError("NASA POWER table contains no Brazil coffee rows")
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows["publication_date"] = pd.to_datetime(rows["publication_date"]).dt.normalize()
    for column in ("tmin_c", "tmax_c", "precip_mm"):
        rows[column] = pd.to_numeric(rows[column], errors="coerce")

    daily = (rows.groupby("date", as_index=False)
             .agg(tmin_c=("tmin_c", "min"), tmax_c=("tmax_c", "mean"),
                  precip_mm=("precip_mm", "mean"),
                  publication_date=("publication_date", "max"),
                  brazil_points=("point", "nunique"))
             .sort_values("date", ignore_index=True))
    daily["frost"] = (daily["tmin_c"] < FROST_THRESHOLD_C).astype(float)
    daily["dry"] = (daily["precip_mm"] < DRY_DAY_MM).astype(float)
    daily["heat_excess"] = (daily["tmax_c"] - HEAT_THRESHOLD_C).clip(lower=0.0)

    for window in (30, 90):
        daily[f"rain{window}"] = daily["precip_mm"].rolling(
            window, min_periods=window).sum()
        daily[f"dry_days{window}"] = daily["dry"].rolling(
            window, min_periods=window).sum()
        daily[f"max_dry_spell{window}"] = daily["dry"].rolling(
            window, min_periods=window).apply(_longest_run, raw=True)
        daily[f"heat_dd30_{window}"] = daily["heat_excess"].rolling(
            window, min_periods=window).sum()
    daily["min_tmin7"] = daily["tmin_c"].rolling(7, min_periods=7).min()
    daily["min_tmin30"] = daily["tmin_c"].rolling(30, min_periods=30).min()
    daily["frost_days30"] = daily["frost"].rolling(30, min_periods=30).sum()
    daily["tmax_mean30"] = daily["tmax_c"].rolling(30, min_periods=30).mean()
    daily["rain90_z"] = _prior_year_z(daily, "rain90")

    month = daily["date"].dt.month
    daily["winter"] = month.isin((6, 7, 8)).astype(float)
    daily["flowering"] = month.isin((9, 10)).astype(float)
    daily["cherry_fill"] = month.isin((11, 12, 1, 2)).astype(float)
    daily["winter_min_tmin7"] = daily["winter"] * daily["min_tmin7"]
    daily["winter_frost_days30"] = daily["winter"] * daily["frost_days30"]
    daily["flowering_rain90_z"] = daily["flowering"] * daily["rain90_z"]
    daily["cherry_dry_spell30"] = daily["cherry_fill"] * daily["max_dry_spell30"]
    return daily[[
        "date", "publication_date", "tmin_c", "frost",
        *WEATHER_FEATURES, "winter", "flowering", "cherry_fill",
    ]]


def coffee_contract_curve(
    contracts: pd.DataFrame,
    *,
    roll_business_days: int = ROLL_BUSINESS_DAYS,
) -> pd.DataFrame:
    """Return the two nearest eligible Coffee C contracts on each session."""

    required = {
        "date", "root", "contract", "last_trade_date", "open_cents",
        "settlement_cents", "volume", "open_interest",
    }
    missing = sorted(required - set(contracts.columns))
    if missing:
        raise CoffeeModelError("contract panel is missing: " + ", ".join(missing))
    clean = contracts.loc[contracts["root"] == "KC"].copy()
    if clean.empty:
        raise CoffeeModelError("contract panel contains no KC rows")
    clean["date"] = pd.to_datetime(clean["date"]).dt.normalize()
    clean["last_trade_date"] = pd.to_datetime(clean["last_trade_date"]).dt.normalize()
    clean["business_days_to_last_trade"] = clean.apply(
        lambda row: int(np.busday_count(
            row["date"].date(), row["last_trade_date"].date()
        )), axis=1,
    )
    clean = clean.sort_values(["contract", "date"], ignore_index=True)
    grouped = clean.groupby("contract", sort=False)
    clean["contract_return"] = grouped["settlement_cents"].pct_change(fill_method=None)
    for window in (5, 20, 60):
        clean[f"coffee_mom{window}"] = grouped["settlement_cents"].pct_change(
            window, fill_method=None)
    clean["coffee_vol20"] = grouped["contract_return"].transform(
        lambda values: values.rolling(20, min_periods=15).std() * np.sqrt(252)
    )
    clean["volume_z20"] = grouped["volume"].transform(
        lambda values: (
            np.log1p(values.clip(lower=0))
            - np.log1p(values.clip(lower=0)).rolling(20, min_periods=15).mean()
        ) / np.log1p(values.clip(lower=0)).rolling(20, min_periods=15).std()
    )
    clean["open_interest_chg20"] = grouped["open_interest"].pct_change(
        20, fill_method=None)

    eligible = clean.loc[
        clean["business_days_to_last_trade"] > roll_business_days
    ].sort_values(["date", "last_trade_date", "contract"])
    eligible["curve_rank"] = eligible.groupby("date").cumcount()
    near = eligible.loc[eligible["curve_rank"] == 0].copy()
    far = eligible.loc[eligible["curve_rank"] == 1, [
        "date", "contract", "last_trade_date", "open_cents", "settlement_cents",
    ]].copy()
    far = far.rename(columns={
        "contract": "far_contract",
        "last_trade_date": "far_last_trade_date",
        "open_cents": "far_open_cents",
        "settlement_cents": "far_settlement_cents",
    })
    near = near.rename(columns={
        "contract": "near_contract",
        "last_trade_date": "near_last_trade_date",
        "open_cents": "near_open_cents",
        "settlement_cents": "near_settlement_cents",
    })
    curve = near.merge(far, on="date", how="inner", validate="one_to_one")
    curve["curve_slope"] = (
        curve["far_settlement_cents"] / curve["near_settlement_cents"] - 1.0
    )
    curve["curve_mom20"] = curve["curve_slope"].diff(20)
    curve["curve_vol20"] = curve["curve_slope"].diff().rolling(
        20, min_periods=15).std() * np.sqrt(252)
    return curve.sort_values("date", ignore_index=True)


def attach_execution_targets(
    curve: pd.DataFrame,
    contracts: pd.DataFrame,
    *,
    holding_sessions: int = HOLDING_SESSIONS,
) -> pd.DataFrame:
    """Attach same-contract next-open outright and curve outcomes."""

    if holding_sessions < 1:
        raise CoffeeModelError("holding_sessions must be at least one")
    panel = contracts.loc[contracts["root"] == "KC"].copy()
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    histories = {
        name: rows.sort_values("date").set_index("date")
        for name, rows in panel.groupby("contract", sort=False)
    }
    target_out = f"target_coffee_outright_{holding_sessions}"
    target_curve = f"target_coffee_curve_{holding_sessions}"
    round_trip_cost_usd = 2 * (
        COMMISSION_USD_PER_SIDE + SLIPPAGE_TICKS_PER_SIDE * TICK_VALUE_USD
    )
    rows: list[dict[str, object]] = []
    for decision in curve.itertuples(index=False):
        near = histories[str(decision.near_contract)]
        far = histories[str(decision.far_contract)]
        shared = near.index.intersection(far.index)
        shared = shared[shared > decision.date].sort_values()
        if len(shared) <= holding_sessions:
            continue
        entry_date = shared[0]
        exit_date = shared[holding_sessions]
        near_entry = float(near.loc[entry_date, "open_cents"])
        near_exit = float(near.loc[exit_date, "open_cents"])
        far_entry = float(far.loc[entry_date, "open_cents"])
        far_exit = float(far.loc[exit_date, "open_cents"])
        near_return = near_exit / near_entry - 1.0
        far_return = far_exit / far_entry - 1.0
        near_notional = near_entry / 100.0 * CONTRACT_SIZE_POUNDS
        far_notional = far_entry / 100.0 * CONTRACT_SIZE_POUNDS
        near_cost = round_trip_cost_usd / near_notional
        far_cost = round_trip_cost_usd / far_notional
        rows.append({
            "date": decision.date,
            "entry_date": entry_date,
            "exit_date": exit_date,
            "execution_near_contract": decision.near_contract,
            "execution_far_contract": decision.far_contract,
            "near_entry_open_cents": near_entry,
            "far_entry_open_cents": far_entry,
            target_out: near_return,
            target_curve: near_return - far_return,
            "cost_rate_outright": near_cost,
            "cost_rate_curve": near_cost + far_cost,
        })
    targets = pd.DataFrame(rows)
    if targets.empty:
        raise CoffeeModelError("no curve rows have complete execution outcomes")
    return curve.merge(targets, on="date", how="inner", validate="one_to_one")


def _non_overlapping_weekly(frame: pd.DataFrame) -> pd.DataFrame:
    week = frame["date"].dt.to_period("W-FRI")
    weekly = frame.groupby(week, sort=True, as_index=False).tail(1).copy()
    selected: list[int] = []
    previous_exit: pd.Timestamp | None = None
    for index, row in weekly.sort_values("date").iterrows():
        if previous_exit is None or pd.Timestamp(row["entry_date"]) > previous_exit:
            selected.append(index)
            previous_exit = pd.Timestamp(row["exit_date"])
    return weekly.loc[selected].sort_values("date", ignore_index=True)


def coffee_model_frame(contracts: pd.DataFrame, power: pd.DataFrame) -> pd.DataFrame:
    """Build independent 20-session coffee decisions with point-in-time inputs."""

    curve = coffee_contract_curve(contracts)
    executed = attach_execution_targets(curve, contracts, holding_sessions=HOLDING_SESSIONS)
    decisions = _non_overlapping_weekly(executed)
    weather = brazil_weather_features(power).dropna(subset=["publication_date"])
    weather = weather.rename(columns={"date": "weather_date"})
    left = decisions.sort_values("date").copy()
    right = weather.sort_values("publication_date").copy()
    left["date"] = left["date"].astype("datetime64[us]")
    right["publication_date"] = right["publication_date"].astype("datetime64[us]")
    frame = pd.merge_asof(
        left, right, left_on="date", right_on="publication_date",
        direction="backward",
    )
    frame["year"] = frame["date"].dt.year
    decision_month = frame["date"].dt.month
    # Rolling weather interactions retain the observation-date crop stage, but
    # the standalone seasonal controls describe the actual decision date.
    frame["winter"] = decision_month.isin((6, 7, 8)).astype(float)
    frame["flowering"] = decision_month.isin((9, 10)).astype(float)
    frame["cherry_fill"] = decision_month.isin((11, 12, 1, 2)).astype(float)
    phase = 2.0 * np.pi * decision_month / 12.0
    frame["month_sin"] = np.sin(phase)
    frame["month_cos"] = np.cos(phase)
    return frame.sort_values("date", ignore_index=True)


def frost_episodes(power: pd.DataFrame, *, spacing_days: int = 10) -> pd.DataFrame:
    """Collapse qualifying Brazil frost days into independent cold episodes."""

    weather = brazil_weather_features(power)
    frost = weather.loc[
        weather["date"].dt.month.isin((6, 7, 8))
        & (weather["tmin_c"] < FROST_THRESHOLD_C)
    ].sort_values("date")
    if frost.empty:
        return frost[["date", "publication_date", "tmin_c"]]
    episodes: list[pd.Series] = []
    cluster: list[pd.Series] = []
    last_date: pd.Timestamp | None = None
    for _, row in frost.iterrows():
        date = pd.Timestamp(row["date"])
        if last_date is not None and (date - last_date).days > spacing_days:
            episodes.append(min(cluster, key=lambda value: float(value["tmin_c"])))
            cluster = []
        cluster.append(row)
        last_date = date
    episodes.append(min(cluster, key=lambda value: float(value["tmin_c"])))
    return pd.DataFrame(episodes)[["date", "publication_date", "tmin_c"]].reset_index(drop=True)


def frost_event_outcomes(
    contracts: pd.DataFrame,
    power: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = (20, 60),
) -> pd.DataFrame:
    """Measure next-open Coffee C reactions after published Brazil frosts."""

    events = frost_episodes(power)
    rows: list[pd.DataFrame] = []
    for horizon in horizons:
        curve = coffee_contract_curve(
            contracts, roll_business_days=max(ROLL_BUSINESS_DAYS, horizon + 5)
        )
        outcomes = attach_execution_targets(
            curve, contracts, holding_sessions=horizon
        ).sort_values("date")
        eligible_events = events.loc[
            (events["publication_date"] >= outcomes["date"].min())
            & (events["publication_date"] <= outcomes["date"].max())
        ].copy()
        left = eligible_events.rename(columns={"date": "frost_date"}).copy()
        left["publication_date"] = left["publication_date"].astype("datetime64[us]")
        right = outcomes.copy()
        right["date"] = right["date"].astype("datetime64[us]")
        aligned = pd.merge_asof(
            left.sort_values("publication_date"), right,
            left_on="publication_date", right_on="date", direction="forward",
        )
        aligned["horizon_sessions"] = horizon
        aligned["sample"] = np.where(
            pd.to_datetime(aligned["date"]).dt.year < 2024,
            "development", "consumed_2024_plus",
        )
        rows.append(aligned)
    return pd.concat(rows, ignore_index=True)
