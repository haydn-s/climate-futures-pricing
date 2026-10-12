"""Prospective records must be timely, immutable, and incapable of trading."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from pipeline.paper_trading import (
    PaperTradingError,
    paper_status,
    register_signal_file,
    resolve_registered_signals,
)

ROOT = Path(__file__).resolve().parents[1]


def signal_record(action: str = "BUY") -> dict:
    position = {"BUY": 1, "HOLD": 0, "SELL": -1}[action]
    return {
        "schema_version": 1,
        "model_version": "corn-wheat-all-public-boosting-v1",
        "generated_at_utc": "2026-01-02T21:00:00+00:00",
        "decision": {
            "date": "2026-01-02",
            "status": "READY_FOR_PAPER_TRADING",
        },
        "signal": {
            "action": action,
            "model_action": action,
            "position": position,
            "score": 0.025,
            "threshold": 0.010,
            "distance_from_threshold": 0.015,
            "legs": [] if action == "HOLD" else [
                {"instrument": "corn", "side": "long"},
                {"instrument": "wheat", "side": "short"},
            ],
        },
        "execution": {
            "authorized": False,
            "orders_submitted": 0,
        },
    }


def contract_panel(periods: int = 1) -> pd.DataFrame:
    rows = []
    for number, day in enumerate(pd.bdate_range("2026-01-02", periods=periods)):
        rows.extend([
            {
                "date": day,
                "root": "ZC",
                "contract": "ZCH26",
                "last_trade_date": "2026-03-13",
                "open_cents": 400.0 + 2 * number,
                "settlement_cents": 400.5 + 2 * number,
            },
            {
                "date": day,
                "root": "ZW",
                "contract": "ZWH26",
                "last_trade_date": "2026-03-13",
                "open_cents": 500.0 - number,
                "settlement_cents": 500.5 - number,
            },
        ])
    return pd.DataFrame(rows)


def write_inputs(tmp_path: Path, *, periods: int = 1, action: str = "BUY") -> tuple[Path, Path]:
    signal = tmp_path / "signal.json"
    signal.write_text(json.dumps(signal_record(action)), encoding="utf-8")
    contracts = tmp_path / "contracts.parquet"
    contract_panel(periods).to_parquet(contracts, index=False)
    return signal, contracts


def test_registration_is_hash_linked_paper_only_and_immutable(tmp_path: Path) -> None:
    signal, contracts = write_inputs(tmp_path)
    registered = datetime(2026, 1, 2, 22, tzinfo=UTC)

    record, path = register_signal_file(
        signal,
        contracts,
        data_root=tmp_path,
        registered_at=registered,
    )

    assert path is not None and path.exists()
    assert record["status"] == "PENDING_OUTCOME"
    assert record["eligible_for_prospective_evidence"] is True
    assert record["execution_plan"]["authorized"] is False
    assert record["execution_plan"]["orders_submitted"] == 0
    assert len(record["source_signal"]["sha256"]) == 64
    assert len(record["registration_contract_snapshot"]["sha256"]) == 64
    with pytest.raises(PaperTradingError, match="already exists"):
        register_signal_file(
            signal,
            contracts,
            data_root=tmp_path,
            registered_at=registered,
        )


def test_late_registration_is_rejected_as_non_prospective(tmp_path: Path) -> None:
    signal, contracts = write_inputs(tmp_path, periods=2)

    with pytest.raises(PaperTradingError, match="late registration"):
        register_signal_file(
            signal,
            contracts,
            data_root=tmp_path,
            registered_at=datetime(2026, 1, 5, 22, tzinfo=UTC),
        )


def test_registration_rejects_an_action_position_mismatch(tmp_path: Path) -> None:
    signal, contracts = write_inputs(tmp_path)
    payload = json.loads(signal.read_text())
    payload["signal"]["position"] = -1
    signal.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PaperTradingError, match="action and position disagree"):
        register_signal_file(
            signal,
            contracts,
            data_root=tmp_path,
            registered_at=datetime(2026, 1, 2, 22, tzinfo=UTC),
        )


def test_resolver_waits_until_the_full_horizon_exists(tmp_path: Path) -> None:
    signal, contracts = write_inputs(tmp_path)
    register_signal_file(
        signal,
        contracts,
        data_root=tmp_path,
        registered_at=datetime(2026, 1, 2, 22, tzinfo=UTC),
    )

    outcomes, pending = resolve_registered_signals(
        contracts,
        data_root=tmp_path,
        resolved_at=datetime(2026, 1, 2, 23, tzinfo=UTC),
    )

    assert outcomes == []
    assert pending == 1
    assert not (tmp_path / "paper_trading" / "corn-wheat-all-public-boosting-v1" / "outcomes").exists()


def test_resolver_appends_an_outcome_without_changing_the_decision(tmp_path: Path) -> None:
    signal, initial = write_inputs(tmp_path)
    registration, decision_path = register_signal_file(
        signal,
        initial,
        data_root=tmp_path,
        registered_at=datetime(2026, 1, 2, 22, tzinfo=UTC),
    )
    decision_before = decision_path.read_bytes()  # type: ignore[union-attr]
    complete = tmp_path / "complete.parquet"
    contract_panel(periods=7).to_parquet(complete, index=False)

    outcomes, pending = resolve_registered_signals(
        complete,
        data_root=tmp_path,
        resolved_at=datetime(2026, 1, 12, 22, tzinfo=UTC),
    )

    assert pending == 0
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome["paper_id"] == registration["paper_id"]
    assert outcome["execution"]["execution_status"] == "EXECUTED"
    assert outcome["execution"]["entry_date"].startswith("2026-01-05")
    assert outcome["execution"]["exit_date"].startswith("2026-01-12")
    assert outcome["execution"]["execution_net_return"] > 0
    assert outcome["orders_submitted"] == 0
    assert decision_path.read_bytes() == decision_before  # type: ignore[union-attr]

    repeated, repeated_pending = resolve_registered_signals(
        complete,
        data_root=tmp_path,
        resolved_at=datetime(2026, 1, 13, 22, tzinfo=UTC),
    )
    assert repeated == []
    assert repeated_pending == 0


def test_resolver_refuses_to_change_the_registered_execution_config(tmp_path: Path) -> None:
    signal, initial = write_inputs(tmp_path)
    register_signal_file(
        signal,
        initial,
        data_root=tmp_path,
        registered_at=datetime(2026, 1, 2, 22, tzinfo=UTC),
    )
    complete = tmp_path / "complete.parquet"
    contract_panel(periods=7).to_parquet(complete, index=False)
    changed = tmp_path / "changed_execution.yaml"
    original = (ROOT / "config" / "futures_execution.yaml").read_text()
    changed.write_text(
        original.replace("commission_usd_per_contract_side: 2.50", "commission_usd_per_contract_side: 3.00"),
        encoding="utf-8",
    )

    with pytest.raises(PaperTradingError, match="execution config differs"):
        resolve_registered_signals(
            complete,
            data_root=tmp_path,
            execution_config_path=changed,
            resolved_at=datetime(2026, 1, 12, 22, tzinfo=UTC),
        )


def test_a_timely_model_hold_is_a_final_prospective_observation(tmp_path: Path) -> None:
    signal, contracts = write_inputs(tmp_path, action="HOLD")

    record, _ = register_signal_file(
        signal,
        contracts,
        data_root=tmp_path,
        registered_at=datetime(2026, 1, 2, 22, tzinfo=UTC),
    )

    assert record["status"] == "FINAL_HOLD"
    assert record["eligible_for_prospective_evidence"] is True


def test_status_summarizes_decisions_and_outcomes_without_mutation(tmp_path: Path) -> None:
    signal, initial = write_inputs(tmp_path)
    register_signal_file(
        signal,
        initial,
        data_root=tmp_path,
        registered_at=datetime(2026, 1, 2, 22, tzinfo=UTC),
    )
    before = paper_status(tmp_path)
    assert before["decisions"] == 1
    assert before["outcomes"] == 0
    assert before["pending_actionable"] == 1

    complete = tmp_path / "complete.parquet"
    contract_panel(periods=7).to_parquet(complete, index=False)
    resolve_registered_signals(
        complete,
        data_root=tmp_path,
        resolved_at=datetime(2026, 1, 12, 22, tzinfo=UTC),
    )
    after = paper_status(tmp_path)
    assert after["outcomes"] == 1
    assert after["outcome_statuses"] == {"EXECUTED": 1}
    assert after["pending_actionable"] == 0
    assert after["aggregate_net_on_average_leg_notional"] > 0
    assert after["orders_submitted"] == 0
