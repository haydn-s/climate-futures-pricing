"""Point-in-time and execution tests for the cocoa model frame."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from pipeline.cocoa_model import (
    attach_execution_target,
    cocoa_front_contracts,
    cocoa_weather_features,
)


def contract_panel() -> pd.DataFrame:
    rows = []
    dates = pd.bdate_range("2026-01-02", periods=9)
    for contract, expiry, base in (
        ("CCH26", "2026-01-09", 500_000.0),
        ("CCK26", "2026-05-15", 510_000.0),
    ):
        for offset, date in enumerate(dates):
            rows.append({
                "date": date, "root": "CC", "contract": contract,
                "last_trade_date": expiry,
                "open_cents": base + offset * 100,
                "settlement_cents": base + offset * 100 + 50,
                "volume": 1000 + offset, "open_interest": 5000 - offset,
            })
    return pd.DataFrame(rows)


def test_front_contract_skips_near_expiry_and_keeps_same_contract_features() -> None:
    front = cocoa_front_contracts(contract_panel(), roll_business_days=3)
    assert front.loc[front["date"] >= pd.Timestamp("2026-01-06"), "contract"].eq(
        "CCK26"
    ).all()
    assert front["cocoa_mom5"].notna().sum() == 4


def test_execution_target_enters_next_open_and_exits_five_sessions_later() -> None:
    contracts = contract_panel()
    front = cocoa_front_contracts(contracts, roll_business_days=0).iloc[[0]]
    result = attach_execution_target(front, contracts, holding_sessions=5).iloc[0]
    assert result["entry_date"] == pd.Timestamp("2026-01-05")
    assert result["exit_date"] == pd.Timestamp("2026-01-12")
    assert result["target_cocoa_5"] == pytest.approx(500_600 / 500_100 - 1)
    assert result["cost_rate"] == pytest.approx(25 / 50_010)


def test_weather_is_country_balanced_before_rolling() -> None:
    rows = []
    publication_lag = timedelta(days=5)
    for date in pd.date_range("2020-01-01", periods=100):
        rows.extend([
            {"crop": "cocoa", "country": "A", "point": "A1", "date": date,
             "tmax_c": 30.0, "precip_mm": 0.0,
             "publication_date": date + publication_lag},
            {"crop": "cocoa", "country": "A", "point": "A2", "date": date,
             "tmax_c": 30.0, "precip_mm": 0.0,
             "publication_date": date + publication_lag},
            {"crop": "cocoa", "country": "B", "point": "B1", "date": date,
             "tmax_c": 34.0, "precip_mm": 10.0,
             "publication_date": date + publication_lag},
        ])
    weather = cocoa_weather_features(pd.DataFrame(rows))
    last = weather.iloc[-1]
    assert last["weather_countries"] == 2
    assert last["rain30"] == pytest.approx(150.0)
    assert last["heat_dd32_30"] == pytest.approx(30.0)
