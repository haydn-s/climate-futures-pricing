"""Contract choice, sizing, costs, and timing for paper execution."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from pipeline.futures_execution import (
    ContractSpec,
    ExecutionConfig,
    ExecutionError,
    load_execution_config,
    select_contract,
    simulate_spread_trades,
    size_dollar_neutral_spread,
    validate_contract_panel,
)


def sample_panel() -> pd.DataFrame:
    rows = []
    for date, corn, wheat in [
        ("2026-01-02", 400.0, 500.0),
        ("2026-01-05", 410.0, 495.0),
        ("2026-01-06", 415.0, 490.0),
        ("2026-01-07", 420.0, 485.0),
    ]:
        rows.extend([
            {
                "date": date,
                "root": "ZC",
                "contract": "ZCH26",
                "last_trade_date": "2026-03-13",
                "open_cents": corn,
                "maintenance_margin_usd": 1000.0,
            },
            {
                "date": date,
                "root": "ZW",
                "contract": "ZWH26",
                "last_trade_date": "2026-03-13",
                "open_cents": wheat,
                "maintenance_margin_usd": 2000.0,
            },
        ])
    return pd.DataFrame(rows)


def one_session_config() -> ExecutionConfig:
    loaded = load_execution_config()
    return ExecutionConfig(
        contracts=loaded.contracts,
        assumptions=replace(
            loaded.assumptions,
            holding_sessions=1,
            roll_business_days=0,
            commission_usd_per_contract_side=2.0,
            max_contracts_per_leg=5,
        ),
    )


def test_exchange_specs_are_internally_consistent() -> None:
    config = load_execution_config()
    for spec in config.contracts.values():
        assert spec.contract_size_bushels == 5000
        assert spec.tick_size_cents == 0.25
        assert spec.tick_value_usd == 12.50


def test_inconsistent_tick_value_is_refused() -> None:
    with pytest.raises(ExecutionError, match="tick value"):
        ContractSpec(
            root="ZC",
            name="bad",
            contract_size_bushels=5000,
            tick_size_cents=0.25,
            tick_value_usd=10.0,
            listed_month_codes=("H",),
            source_url="https://example.test",
        )


def test_contract_panel_rejects_missing_margin_column() -> None:
    config = load_execution_config()
    with pytest.raises(ExecutionError, match="maintenance_margin_usd"):
        validate_contract_panel(
            sample_panel().drop(columns="maintenance_margin_usd"), config.contracts
        )


def test_roll_rule_skips_contract_too_close_to_last_trade() -> None:
    config = load_execution_config()
    panel = sample_panel()
    near = panel[panel["root"] == "ZC"].copy()
    near["contract"] = "ZCZ25"
    near["last_trade_date"] = "2026-01-08"
    panel = pd.concat([near, panel], ignore_index=True)
    clean = validate_contract_panel(panel, config.contracts)
    selected = select_contract(
        clean,
        root="ZC",
        entry_date=pd.Timestamp("2026-01-02"),
        exit_date=pd.Timestamp("2026-01-05"),
        roll_business_days=3,
    )
    assert selected["contract"] == "ZCH26"


def test_contract_panel_rejects_missing_dated_margin_value() -> None:
    config = load_execution_config()
    panel = sample_panel()
    panel.loc[0, "maintenance_margin_usd"] = None
    with pytest.raises(ExecutionError, match="dated maintenance margins"):
        validate_contract_panel(panel, config.contracts)


def test_spread_sizing_uses_small_integer_dollar_neutral_pair() -> None:
    config = load_execution_config()
    size = size_dollar_neutral_spread(
        400.0,
        500.0,
        corn_spec=config.contracts["ZC"],
        wheat_spec=config.contracts["ZW"],
        corn_margin_usd=1000.0,
        wheat_margin_usd=2000.0,
        max_contracts_per_leg=8,
    )
    assert (size.corn_contracts, size.wheat_contracts) == (5, 4)
    assert size.corn_notional_usd == size.wheat_notional_usd == 100_000.0
    assert size.imbalance_fraction == 0
    assert size.maintenance_margin_usd == 13_000.0


def test_margin_budget_is_enforced_during_sizing() -> None:
    config = load_execution_config()
    with pytest.raises(ExecutionError, match="cannot fund"):
        size_dollar_neutral_spread(
            400.0,
            500.0,
            corn_spec=config.contracts["ZC"],
            wheat_spec=config.contracts["ZW"],
            corn_margin_usd=1000.0,
            wheat_margin_usd=2000.0,
            max_contracts_per_leg=8,
            margin_budget_usd=2500.0,
        )


def test_buy_trade_uses_next_open_and_deducts_round_trip_costs() -> None:
    ledger = simulate_spread_trades(
        pd.DataFrame([{"decision_date": "2026-01-01", "action": "BUY"}]),
        sample_panel(),
        config=one_session_config(),
    )
    trade = ledger.iloc[0]
    assert trade["status"] == "EXECUTED"
    assert trade["entry_date"] == pd.Timestamp("2026-01-02")
    assert trade["exit_date"] == pd.Timestamp("2026-01-05")
    assert (trade["corn_contracts"], trade["wheat_contracts"]) == (5, 4)
    assert trade["corn_gross_pnl_usd"] == 2500.0
    assert trade["wheat_gross_pnl_usd"] == 1000.0
    assert trade["commission_usd"] == 36.0
    assert trade["slippage_usd"] == 225.0
    assert trade["net_pnl_usd"] == 3239.0
    assert trade["return_on_margin"] == pytest.approx(3239 / 13_000)


def test_capital_account_marks_to_realized_pnl_after_trade() -> None:
    ledger = simulate_spread_trades(
        pd.DataFrame([
            {"decision_date": "2026-01-01", "action": "BUY"},
            {"decision_date": "2026-01-05", "action": "SELL"},
        ]),
        sample_panel(),
        config=one_session_config(),
        capital_usd=25_000.0,
    )
    trade = ledger.iloc[0]
    assert trade["status"] == "EXECUTED"
    assert trade["capital_before_usd"] == 25_000.0
    assert trade["margin_budget_usd"] == 25_000.0
    assert trade["margin_utilization"] == pytest.approx(13_000 / 25_000)
    assert trade["return_on_capital"] == pytest.approx(3239 / 25_000)
    assert trade["capital_after_usd"] == 28_239.0
    assert ledger.iloc[1]["capital_before_usd"] == 28_239.0


def test_capital_simulation_refuses_overlapping_account_lookahead() -> None:
    config = one_session_config()
    config = ExecutionConfig(
        contracts=config.contracts,
        assumptions=replace(config.assumptions, allow_overlapping_positions=True),
    )
    with pytest.raises(ExecutionError, match="requires non-overlapping"):
        simulate_spread_trades(
            pd.DataFrame([{"decision_date": "2026-01-01", "action": "BUY"}]),
            sample_panel(),
            config=config,
            capital_usd=25_000.0,
        )


def test_sell_reverses_both_legs() -> None:
    ledger = simulate_spread_trades(
        pd.DataFrame([{"decision_date": "2026-01-01", "action": "SELL"}]),
        sample_panel(),
        config=one_session_config(),
    )
    trade = ledger.iloc[0]
    assert trade["corn_side"] == "SHORT"
    assert trade["wheat_side"] == "LONG"
    assert trade["gross_pnl_usd"] == -3500.0
    assert trade["net_pnl_usd"] == -3761.0


def test_holds_and_overlapping_signals_are_visible_in_ledger() -> None:
    signals = pd.DataFrame([
        {"decision_date": "2025-12-31", "action": "HOLD"},
        {"decision_date": "2026-01-01", "action": "BUY"},
        {"decision_date": "2026-01-02", "action": "SELL"},
    ])
    ledger = simulate_spread_trades(signals, sample_panel(), config=one_session_config())
    assert ledger["status"].tolist() == ["HOLD", "EXECUTED", "SKIPPED_OVERLAP"]


def test_insufficient_margin_skips_trade_without_losing_audit_row() -> None:
    ledger = simulate_spread_trades(
        pd.DataFrame([{"decision_date": "2026-01-01", "action": "BUY"}]),
        sample_panel(),
        config=one_session_config(),
        capital_usd=2500.0,
    )
    row = ledger.iloc[0]
    assert row["status"] == "SKIPPED_MARGIN"
    assert "cannot fund" in row["skip_reason"]


def test_no_roll_eligible_contract_skips_trade_without_crashing_run() -> None:
    config = one_session_config()
    config = ExecutionConfig(
        contracts=config.contracts,
        assumptions=replace(config.assumptions, roll_business_days=50),
    )
    ledger = simulate_spread_trades(
        pd.DataFrame([{"decision_date": "2026-01-01", "action": "SELL"}]),
        sample_panel(),
        config=config,
    )
    row = ledger.iloc[0]
    assert row["status"] == "SKIPPED_NO_CONTRACT"
    assert "remains more than 50 business days" in row["skip_reason"]
