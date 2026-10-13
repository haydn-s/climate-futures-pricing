"""Point-in-time, curve, and execution tests for Coffee C modeling."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from pipeline.coffee_model import (
    attach_execution_targets,
    brazil_weather_features,
    coffee_contract_curve,
    coffee_model_frame,
    frost_episodes,
)


def contract_panel() -> pd.DataFrame:
    rows = []
    dates = pd.bdate_range("2026-01-02", periods=90)
    for contract, expiry, base in (
        ("KCH26", "2026-03-18", 300.00),
        ("KCK26", "2026-05-19", 305.00),
        ("KCN26", "2026-07-20", 310.00),
    ):
        for offset, date in enumerate(dates):
            rows.append({
                "date": date,
                "root": "KC",
                "contract": contract,
                "last_trade_date": expiry,
                "open_cents": base + offset * 0.05,
                "settlement_cents": base + offset * 0.05 + 0.05,
                "volume": 1000 + offset,
                "open_interest": 5000 - offset,
            })
    return pd.DataFrame(rows)


def weather_panel(
    start: str = "2020-05-01", periods: int = 130,
) -> pd.DataFrame:
    rows = []
    for date in pd.date_range(start, periods=periods):
        for point, tmin, rain in (
            ("South", 3.0 if date.day in (10, 11) else 8.0, 0.0),
            ("North", 7.0, 10.0),
            ("Central", 6.0, 5.0),
        ):
            rows.append({
                "crop": "coffee", "country": "Brazil", "point": point,
                "date": date, "tmin_c": tmin, "tmax_c": 31.0,
                "precip_mm": rain,
                "publication_date": date + timedelta(days=5),
            })
        rows.append({
            "crop": "coffee", "country": "Colombia", "point": "Control",
            "date": date, "tmin_c": -5.0, "tmax_c": 40.0,
            "precip_mm": 100.0,
            "publication_date": date + timedelta(days=5),
        })
    return pd.DataFrame(rows)


def test_weather_uses_coldest_brazil_point_but_averages_rain() -> None:
    weather = brazil_weather_features(weather_panel())
    row = weather.loc[weather["date"] == pd.Timestamp("2020-07-10")].iloc[0]
    assert row["tmin_c"] == 3.0
    assert row["brazil_points"] == 3
    assert row["rain30"] == pytest.approx(150.0)
    assert row["frost_days30"] >= 1


def test_curve_selects_two_nearest_contracts_outside_roll_buffer() -> None:
    curve = coffee_contract_curve(contract_panel(), roll_business_days=25)
    january = curve.loc[curve["date"] == pd.Timestamp("2026-01-30")].iloc[0]
    assert january["near_contract"] == "KCH26"
    assert january["far_contract"] == "KCK26"
    march = curve.loc[curve["date"] == pd.Timestamp("2026-03-02")].iloc[0]
    assert march["near_contract"] == "KCK26"
    assert march["far_contract"] == "KCN26"


def test_execution_targets_use_shared_next_open_and_exact_costs() -> None:
    contracts = contract_panel()
    curve = coffee_contract_curve(contracts, roll_business_days=0).iloc[[30]]
    result = attach_execution_targets(curve, contracts, holding_sessions=20).iloc[0]
    near_entry = result["near_entry_open_cents"]
    far_entry = result["far_entry_open_cents"]
    expected_near_cost = 42.50 / (near_entry / 100 * 37_500)
    expected_far_cost = 42.50 / (far_entry / 100 * 37_500)
    assert result["entry_date"] > result["date"]
    assert result["target_coffee_outright_20"] > 0
    assert result["cost_rate_outright"] == pytest.approx(expected_near_cost)
    assert result["cost_rate_curve"] == pytest.approx(
        expected_near_cost + expected_far_cost
    )


def test_frost_days_are_collapsed_into_episodes() -> None:
    episodes = frost_episodes(weather_panel(), spacing_days=10)
    # Day 10 and day 11 within each month are a single episode. Only June-August
    # qualify, so this synthetic panel produces three independent episodes.
    assert len(episodes) == 3
    assert episodes["date"].dt.month.tolist() == [6, 7, 8]
    assert episodes["tmin_c"].eq(3.0).all()


def test_model_frame_is_point_in_time_and_non_overlapping() -> None:
    weather = weather_panel("2025-09-01", 260)
    frame = coffee_model_frame(contract_panel(), weather)
    assert len(frame) >= 2
    assert (frame["publication_date"] <= frame["date"]).all()
    assert frame["brazil_points"].eq(3).all()
    assert (
        frame["entry_date"].iloc[1:].reset_index(drop=True)
        > frame["exit_date"].iloc[:-1].reset_index(drop=True)
    ).all()
