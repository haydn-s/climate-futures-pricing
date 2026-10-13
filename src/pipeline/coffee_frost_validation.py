"""Frozen Coffee C frost-curve rule and season-matched null diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .coffee_model import (
    HOLDING_SESSIONS,
    ROLL_BUSINESS_DAYS,
    attach_execution_targets,
    coffee_contract_curve,
    frost_event_outcomes,
)

HISTORICAL_EXTENSION_END = 2011
SELECTED_DEVELOPMENT_END = 2023


@dataclass(frozen=True)
class FrostMetrics:
    events: int
    mean_net_return: float
    median_net_return: float
    positive_events: int
    positive_fraction: float


def classify_sample(year: int) -> str:
    if year <= HISTORICAL_EXTENSION_END:
        return "new_2000_2011_extension"
    if year <= SELECTED_DEVELOPMENT_END:
        return "selected_2012_2023"
    return "consumed_2024_plus"


def frost_trade_ledger(contracts: pd.DataFrame, power: pd.DataFrame) -> pd.DataFrame:
    """Return the fixed long-near/short-deferred frost trades with exact costs."""

    events = frost_event_outcomes(
        contracts, power, horizons=(HOLDING_SESSIONS,)
    ).copy()
    events["event_year"] = pd.to_datetime(events["frost_date"]).dt.year
    events["sample"] = events["event_year"].map(classify_sample)
    events["gross"] = events[f"target_coffee_curve_{HOLDING_SESSIONS}"]
    events["cost"] = events["cost_rate_curve"]
    events["net"] = events["gross"] - events["cost"]
    return events.sort_values("frost_date", ignore_index=True)


def frost_metrics(ledger: pd.DataFrame) -> FrostMetrics:
    values = ledger["net"].dropna().to_numpy(dtype="float64")
    if len(values) == 0:
        return FrostMetrics(0, np.nan, np.nan, 0, np.nan)
    positive = int((values > 0).sum())
    return FrostMetrics(
        events=len(values),
        mean_net_return=float(np.mean(values)),
        median_net_return=float(np.median(values)),
        positive_events=positive,
        positive_fraction=positive / len(values),
    )


def daily_curve_outcomes(contracts: pd.DataFrame) -> pd.DataFrame:
    curve = coffee_contract_curve(
        contracts, roll_business_days=ROLL_BUSINESS_DAYS
    )
    outcomes = attach_execution_targets(
        curve, contracts, holding_sessions=HOLDING_SESSIONS
    ).copy()
    outcomes["net"] = (
        outcomes[f"target_coffee_curve_{HOLDING_SESSIONS}"]
        - outcomes["cost_rate_curve"]
    )
    outcomes["year"] = outcomes["date"].dt.year
    return outcomes


def season_matched_null(
    event_ledger: pd.DataFrame,
    daily_outcomes: pd.DataFrame,
    *,
    draws: int = 10_000,
    random_seed: int = 590,
    exclusion_days: int = 10,
) -> dict[str, float | int]:
    """Compare event mean with same-year, non-frost winter decisions."""

    if draws < 1:
        raise ValueError("draws must be positive")
    events = event_ledger.dropna(subset=["net"]).copy()
    if events.empty:
        raise ValueError("at least one event is required")
    daily = daily_outcomes.loc[
        daily_outcomes["date"].dt.month.isin((6, 7, 8))
    ].copy()
    pools: dict[int, np.ndarray] = {}
    counts: dict[int, int] = {}
    for year, year_events in events.groupby("event_year"):
        candidates = daily.loc[daily["year"] == int(year)].copy()
        frost_dates = pd.to_datetime(year_events["frost_date"])
        clean = np.ones(len(candidates), dtype=bool)
        for frost_date in frost_dates:
            clean &= (
                (candidates["date"] - frost_date).abs().dt.days > exclusion_days
            ).to_numpy()
        values = candidates.loc[clean, "net"].dropna().to_numpy(dtype="float64")
        count = len(year_events)
        if len(values) < count:
            raise ValueError(f"year {year} has too few season-matched placebo dates")
        pools[int(year)] = values
        counts[int(year)] = count

    rng = np.random.default_rng(random_seed)
    null_means = np.empty(draws, dtype="float64")
    for draw in range(draws):
        sampled: list[float] = []
        for year, pool in pools.items():
            sampled.extend(
                rng.choice(pool, size=counts[year], replace=False).tolist()
            )
        null_means[draw] = float(np.mean(sampled))
    observed = float(events["net"].mean())
    p_value = float((1 + np.count_nonzero(null_means >= observed)) / (draws + 1))
    return {
        "events": len(events),
        "draws": draws,
        "observed_mean_net": observed,
        "null_mean": float(np.mean(null_means)),
        "null_median": float(np.median(null_means)),
        "null_95_lower": float(np.quantile(null_means, 0.025)),
        "null_95_upper": float(np.quantile(null_means, 0.975)),
        "p_value": p_value,
    }


def acceptance_gate(
    metrics: FrostMetrics,
    null: dict[str, float | int],
) -> tuple[bool, dict[str, bool]]:
    checks = {
        "at least five newly added events": metrics.events >= 5,
        "positive mean net return": metrics.mean_net_return > 0,
        "positive median net return": metrics.median_net_return > 0,
        "at least 70% positive events": metrics.positive_fraction >= 0.70,
        "season-matched null p-value at most 10%": float(null["p_value"]) <= 0.10,
    }
    return all(checks.values()), checks
