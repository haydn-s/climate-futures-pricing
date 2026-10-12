"""Frozen-signal replay against dated contracts without invented margin."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from pipeline.futures_execution import ExecutionConfig, load_execution_config
from pipeline.lseg_replay import ReplayError, replay_frozen_signals, size_price_only_spread


def replay_config() -> ExecutionConfig:
    loaded = load_execution_config()
    return ExecutionConfig(
        contracts=loaded.contracts,
        assumptions=replace(
            loaded.assumptions,
            holding_sessions=1,
            roll_business_days=0,
            commission_usd_per_contract_side=2.0,
            slippage_ticks_per_contract_side=1.0,
            max_contracts_per_leg=5,
        ),
    )


def contract_panel() -> pd.DataFrame:
    rows = []
    for day, corn, wheat in [
        ("2026-01-02", 400.0, 500.0),
        ("2026-01-05", 410.0, 490.0),
        ("2026-01-06", 420.0, 480.0),
        ("2026-01-07", 430.0, 470.0),
    ]:
        rows.extend([
            {
                "date": day, "root": "ZC", "contract": "ZCH26",
                "last_trade_date": "2026-03-13", "open_cents": corn,
                "settlement_cents": corn,
            },
            {
                "date": day, "root": "ZW", "contract": "ZWH26",
                "last_trade_date": "2026-03-13", "open_cents": wheat,
                "settlement_cents": wheat,
            },
        ])
    return pd.DataFrame(rows)


def predictions(signal: int = 1) -> pd.DataFrame:
    return pd.DataFrame([{
        "date": "2026-01-02",
        "target_end_date": "2026-01-05",
        "year": 2026,
        "actual": 0.04,
        "signal": signal,
        "fold": 6,
        "sample": "holdout",
    }])


def test_price_only_sizing_matches_dollar_neutral_execution_sizing() -> None:
    config = replay_config()
    size = size_price_only_spread(
        400.0, 500.0, specs=config.contracts, max_contracts_per_leg=5
    )
    assert (size.corn_contracts, size.wheat_contracts) == (5, 4)
    assert size.corn_notional_usd == size.wheat_notional_usd == 100_000.0
    assert size.average_leg_notional_usd == 100_000.0


def test_replay_separates_close_target_from_next_open_execution() -> None:
    replay = replay_frozen_signals(
        predictions(), contract_panel(), config=replay_config()
    )
    row = replay.iloc[0]
    expected_close = 410 / 400 - 490 / 500
    assert row["lseg_close_spread_return"] == pytest.approx(expected_close)
    assert row["entry_date"] == pd.Timestamp("2026-01-05")
    assert row["exit_date"] == pd.Timestamp("2026-01-06")
    assert row["execution_gross_return"] > 0
    assert row["execution_net_return"] < row["execution_gross_return"]
    assert row["execution_unsigned_corn_return"] == pytest.approx(420 / 410 - 1)
    assert row["execution_unsigned_wheat_return"] == pytest.approx(480 / 490 - 1)
    assert row["potential_cost_return"] > 0
    assert row["potential_corn_cost_return"] > 0
    assert row["potential_wheat_cost_return"] > 0
    assert row["execution_status"] == "EXECUTED"
    assert row["execution_signal"] == 1
    assert row["corn_nearest_contract"] == "ZCH26"
    assert row["wheat_nearest_contract"] == "ZWH26"
    assert not row["corn_rolled_forward"]
    assert not row["wheat_rolled_forward"]
    assert row["corn_business_days_to_last_trade_at_exit"] > 0
    assert row["execution_commission_usd"] > 0
    assert row["execution_slippage_usd"] > 0


def test_hold_still_gets_an_unsigned_outcome_but_pays_no_cost() -> None:
    row = replay_frozen_signals(
        predictions(signal=0), contract_panel(), config=replay_config()
    ).iloc[0]
    assert row["execution_unsigned_gross_return"] > 0
    assert row["execution_status"] == "HOLD"
    assert row["execution_signal"] == 0
    assert row["execution_gross_return"] == 0
    assert row["execution_net_return"] == 0
    assert row["execution_cost_return"] == 0
    assert row["potential_cost_return"] > 0


def test_replay_suppresses_signals_that_overlap_an_open_trade() -> None:
    decisions = pd.DataFrame([
        {
            "date": "2026-01-02",
            "target_end_date": "2026-01-05",
            "year": 2026,
            "actual": 0.04,
            "signal": 1,
            "fold": 6,
            "sample": "holdout",
        },
        {
            "date": "2026-01-05",
            "target_end_date": "2026-01-06",
            "year": 2026,
            "actual": 0.04,
            "signal": 1,
            "fold": 6,
            "sample": "holdout",
        },
    ])

    replay = replay_frozen_signals(decisions, contract_panel(), config=replay_config())

    assert replay["execution_status"].tolist() == ["EXECUTED", "SKIPPED_OVERLAP"]
    assert replay["execution_signal"].tolist() == [1, 0]
    assert replay["execution_net_return"].iloc[1] == 0
    assert replay["execution_cost_return"].iloc[1] == 0


def test_replay_rejects_fractional_signals() -> None:
    with pytest.raises(ReplayError, match="signals must be -1, 0, or 1"):
        replay_frozen_signals(
            predictions(signal=0.5), contract_panel(), config=replay_config()
        )
