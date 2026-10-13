"""Information-boundary tests for the GEFS/LSEG model frame."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from pipeline.gefs_model import (
    ABSOLUTE_FEATURES,
    REVISION_FEATURES,
    build_execution_frame,
    zc_market_features,
)


def contract_panel() -> pd.DataFrame:
    rows = []
    for session in pd.bdate_range("2024-05-20", "2024-07-31"):
        offset = (session - pd.Timestamp("2024-05-20")).days
        contracts = (
            ("ZC", "ZCZ24", "2024-12-13", 450.0 + offset),
            ("ZC", "ZCH25", "2025-03-14", 460.0 + offset),
            ("ZW", "ZWZ24", "2024-12-13", 600.0 + offset),
        )
        for root, contract, expiry, settlement in contracts:
            rows.append({
                "date": session,
                "root": root,
                "contract": contract,
                "last_trade_date": pd.Timestamp(expiry),
                "open_cents": settlement - 1.0,
                "settlement_cents": settlement,
            })
    return pd.DataFrame(rows)


def revisions() -> pd.DataFrame:
    row = {
        "initialized_at": pd.Timestamp("2024-07-15", tz="UTC"),
        "model_available_at": pd.Timestamp("2024-07-15 07:00", tz="UTC"),
        "statistic": "geavg",
        "has_prior_day_revision": True,
    }
    row.update({column: float(number) for number, column in enumerate(
        (*ABSOLUTE_FEATURES, *REVISION_FEATURES), start=1
    )})
    return pd.DataFrame([row])


def revision_on(stamp: str) -> pd.DataFrame:
    result = revisions()
    initialized = pd.Timestamp(stamp, tz="UTC")
    result["initialized_at"] = initialized
    result["model_available_at"] = initialized + timedelta(hours=7)
    return result


def test_zc_controls_are_one_row_per_completed_session() -> None:
    result = zc_market_features(contract_panel())
    assert result["market_data_through"].is_monotonic_increasing
    assert result["zc_front_contract"].eq("ZCZ24").all()
    assert result["zc_curve_slope"].notna().all()
    assert result["zc_mom20"].notna().sum() > 0


def test_model_frame_uses_only_prior_settlements_and_later_opens() -> None:
    result = build_execution_frame(revisions(), contract_panel())
    assert len(result) == 1
    row = result.iloc[0]
    assert row["date"] == pd.Timestamp("2024-07-15")
    assert row["market_data_through"] == pd.Timestamp("2024-07-12")
    assert row["entry_date"] == pd.Timestamp("2024-07-16")
    assert row["exit_date"] == pd.Timestamp("2024-07-23")
    assert row["corn_contract"] == "ZCZ24"
    assert row["target_corn_cost_return"] > 0


def test_latest_forecast_wins_when_weekend_runs_share_an_entry() -> None:
    source = pd.concat([
        revision_on("2024-07-19"),
        revision_on("2024-07-20"),
    ], ignore_index=True)
    result = build_execution_frame(source, contract_panel())
    assert len(result) == 1
    assert result.iloc[0]["date"] == pd.Timestamp("2024-07-20")
    assert result.iloc[0]["entry_date"] == pd.Timestamp("2024-07-22")
