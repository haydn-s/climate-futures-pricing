"""Immutable prospective paper-trading records for the frozen v1 signal.

The signal command already records what the model said.  This module adds the
temporal evidence needed for an honest prospective study: registration must
happen before the first possible entry session, and a later resolver appends a
separate outcome record without modifying the decision.  Neither path can place
an order.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .futures_data import read_export
from .futures_execution import (
    DEFAULT_EXECUTION_CONFIG,
    ExecutionConfig,
    load_execution_config,
)
from .lseg_replay import prepare_contracts, replay_frozen_signals
from .signal_inference import MODEL_VERSION


ROOT = Path(__file__).resolve().parents[2]
PAPER_SCHEMA_VERSION = 1
VALID_ACTIONS = {"BUY": 1, "HOLD": 0, "SELL": -1}
FINAL_REPLAY_STATUSES = {"EXECUTED", "SKIPPED_OVERLAP", "SKIPPED_NO_CONTRACT"}


class PaperTradingError(ValueError):
    """A record cannot be admitted to or resolved by the prospective ledger."""


def sha256_path(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _config_path(path: str | Path) -> Path:
    resolved = Path(path)
    return resolved if resolved.is_absolute() else ROOT / resolved


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None:
        raise PaperTradingError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_aware(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise PaperTradingError(f"{label} must be an ISO timestamp") from exc
    return _aware(parsed, label)


def _json_value(value: object) -> object:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        if pd.isna(value):
            return None
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _write_immutable(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
    except FileExistsError as exc:
        raise PaperTradingError(f"immutable paper record already exists: {path}") from exc
    return path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperTradingError(f"cannot read JSON record {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PaperTradingError(f"JSON record must be an object: {path}")
    return payload


def _paper_root(data_root: Path, model_version: str = MODEL_VERSION) -> Path:
    return data_root / "paper_trading" / model_version


def build_registration(
    signal_record: dict[str, Any],
    contracts: pd.DataFrame,
    *,
    signal_sha256: str,
    contracts_sha256: str,
    execution_config_sha256: str,
    config: ExecutionConfig,
    registered_at: datetime,
) -> dict[str, Any]:
    """Validate timing and build a prospective decision record without writing."""

    registered = _aware(registered_at, "registered_at")
    if signal_record.get("model_version") != MODEL_VERSION:
        raise PaperTradingError(
            f"signal model must be {MODEL_VERSION}, got "
            f"{signal_record.get('model_version')!r}"
        )
    if signal_record.get("execution", {}).get("orders_submitted") != 0:
        raise PaperTradingError("source signal must state that zero orders were submitted")
    if signal_record.get("execution", {}).get("authorized") is not False:
        raise PaperTradingError("source signal must explicitly disable execution")

    generated = _parse_aware(signal_record.get("generated_at_utc"), "generated_at_utc")
    if generated > registered:
        raise PaperTradingError("signal cannot be registered before it was generated")
    try:
        decision_date = pd.Timestamp(signal_record["decision"]["date"]).normalize()
        decision_status = str(signal_record["decision"]["status"])
        action = str(signal_record["signal"]["action"]).upper()
    except (KeyError, TypeError, ValueError) as exc:
        raise PaperTradingError("signal record is missing its decision or action") from exc
    if action not in VALID_ACTIONS:
        raise PaperTradingError(f"unsupported paper action: {action!r}")
    try:
        position = int(signal_record["signal"]["position"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PaperTradingError("signal position must be -1, 0, or 1") from exc
    if position != VALID_ACTIONS[action]:
        raise PaperTradingError("signal action and position disagree")
    if decision_date.date() > registered.date():
        raise PaperTradingError("future decisions cannot be registered")

    panel = prepare_contracts(contracts)
    if panel["date"].max().date() > registered.date():
        raise PaperTradingError("contract snapshot contains observations after registration")
    common = sorted(
        set(panel.loc[panel["root"] == "ZC", "date"])
        & set(panel.loc[panel["root"] == "ZW", "date"])
    )
    if not any(session <= decision_date for session in common):
        raise PaperTradingError("contract snapshot does not reach the decision date")
    evidence_eligible = decision_status == "READY_FOR_PAPER_TRADING"
    if evidence_eligible and registered.date() != decision_date.date():
        raise PaperTradingError(
            "late registration: prospective decisions must be registered on their "
            "decision date"
        )
    if evidence_eligible and any(session > decision_date for session in common):
        raise PaperTradingError(
            "late registration: the contract snapshot already contains a possible "
            "post-decision entry session"
        )

    if evidence_eligible and action == "HOLD":
        status = "FINAL_HOLD"
    elif evidence_eligible:
        status = "PENDING_OUTCOME"
    else:
        status = "OPERATIONAL_HOLD"

    paper_material = (
        f"{MODEL_VERSION}|{decision_date.date().isoformat()}|{signal_sha256}"
    ).encode("utf-8")
    paper_id = hashlib.sha256(paper_material).hexdigest()[:20]
    assumptions = config.assumptions
    return {
        "schema_version": PAPER_SCHEMA_VERSION,
        "record_type": "paper_decision",
        "paper_id": paper_id,
        "model_version": MODEL_VERSION,
        "registered_at_utc": registered.isoformat(),
        "status": status,
        "eligible_for_prospective_evidence": evidence_eligible,
        "decision": {
            "date": decision_date.date().isoformat(),
            "source_status": decision_status,
            "action": action,
            "model_action": signal_record["signal"].get("model_action"),
            "score": signal_record["signal"].get("score"),
            "threshold": signal_record["signal"].get("threshold"),
            "distance_from_threshold": signal_record["signal"].get(
                "distance_from_threshold"
            ),
        },
        "execution_plan": {
            "authorized": False,
            "orders_submitted": 0,
            "mode": "prospective paper observation only",
            "legs": signal_record["signal"].get("legs", []),
            "contract_selection": (
                "nearest dated ZC/ZW contracts outside the configured roll window"
            ),
            "fill": f"next shared session {assumptions.fill_price_column}",
            "holding_sessions_after_entry": assumptions.holding_sessions,
            "allow_overlapping_positions": assumptions.allow_overlapping_positions,
            "assumptions": asdict(assumptions),
            "execution_config_sha256": execution_config_sha256,
        },
        "source_signal": {
            "sha256": signal_sha256,
            "generated_at_utc": generated.isoformat(),
        },
        "registration_contract_snapshot": {
            "sha256": contracts_sha256,
            "rows": len(panel),
            "contracts": int(panel["contract"].nunique()),
            "through_date": panel["date"].max().date().isoformat(),
        },
        "limitations": [
            "No order was or can be submitted by this workflow.",
            "Historical margin is unavailable; outcomes use average leg notional.",
            "Exact contracts and fills are determined mechanically after outcome data arrive.",
        ],
    }


def register_signal_file(
    signal_path: str | Path,
    contracts_path: str | Path,
    *,
    data_root: str | Path,
    execution_config_path: str | Path = DEFAULT_EXECUTION_CONFIG,
    registered_at: datetime | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], Path | None]:
    """Register one saved signal before entry data exist."""

    signal_file = Path(signal_path)
    contract_file = Path(contracts_path)
    config_file = _config_path(execution_config_path)
    signal = _read_json(signal_file)
    contracts = read_export(contract_file)
    record = build_registration(
        signal,
        contracts,
        signal_sha256=sha256_path(signal_file),
        contracts_sha256=sha256_path(contract_file),
        execution_config_sha256=sha256_path(config_file),
        config=load_execution_config(config_file),
        registered_at=registered_at or datetime.now(timezone.utc),
    )
    if dry_run:
        return record, None
    path = _paper_root(Path(data_root)) / "decisions" / f"{record['paper_id']}.json"
    return record, _write_immutable(path, record)


def _registration_files(data_root: Path) -> list[Path]:
    directory = _paper_root(data_root) / "decisions"
    return sorted(directory.glob("*.json")) if directory.exists() else []


def paper_status(data_root: str | Path) -> dict[str, Any]:
    """Summarize the append-only paper ledger without changing it."""

    root = Path(data_root)
    decisions = [_read_json(path) for path in _registration_files(root)]
    outcome_dir = _paper_root(root) / "outcomes"
    outcome_files = sorted(outcome_dir.glob("*.json")) if outcome_dir.exists() else []
    outcomes = [_read_json(path) for path in outcome_files]
    resolved_ids = {record.get("paper_id") for record in outcomes}
    eligible = [record for record in decisions if record.get("eligible_for_prospective_evidence")]
    pending = [
        record for record in eligible
        if record.get("status") == "PENDING_OUTCOME"
        and record.get("paper_id") not in resolved_ids
    ]
    decision_statuses = pd.Series(
        [record.get("status", "UNKNOWN") for record in decisions], dtype="object"
    ).value_counts().sort_index().to_dict()
    outcome_statuses = pd.Series(
        [
            record.get("execution", {}).get("execution_status", "UNKNOWN")
            for record in outcomes
        ],
        dtype="object",
    ).value_counts().sort_index().to_dict()
    net = sum(
        float(record.get("execution", {}).get("execution_net_return") or 0.0)
        for record in outcomes
    )
    return {
        "model_version": MODEL_VERSION,
        "decisions": len(decisions),
        "eligible_decisions": len(eligible),
        "decision_statuses": decision_statuses,
        "outcomes": len(outcomes),
        "outcome_statuses": outcome_statuses,
        "pending_actionable": len(pending),
        "aggregate_net_on_average_leg_notional": net,
        "orders_submitted": 0,
    }


def _outcome_payload(
    registration: dict[str, Any],
    registration_sha256: str,
    replay_row: pd.Series,
    *,
    contracts_sha256: str,
    execution_config_sha256: str,
    resolved_at: datetime,
) -> dict[str, Any]:
    excluded = {
        "date", "target_end_date", "year", "actual", "signal", "fold", "sample",
        "paper_id",
        "close_status", "close_error", "lseg_close_corn_return",
        "lseg_close_wheat_return", "lseg_close_spread_return",
        "close_corn_contract", "close_wheat_contract",
    }
    execution = {
        key: _json_value(value)
        for key, value in replay_row.items()
        if key not in excluded and not key.startswith("close_")
    }
    return {
        "schema_version": PAPER_SCHEMA_VERSION,
        "record_type": "paper_outcome",
        "paper_id": registration["paper_id"],
        "model_version": registration["model_version"],
        "resolved_at_utc": resolved_at.isoformat(),
        "decision_record_sha256": registration_sha256,
        "resolution_contract_snapshot_sha256": contracts_sha256,
        "execution_config_sha256": execution_config_sha256,
        "execution": execution,
        "orders_submitted": 0,
        "return_denominator": "average leg notional, not maintenance margin",
    }


def resolve_registered_signals(
    contracts_path: str | Path,
    *,
    data_root: str | Path,
    execution_config_path: str | Path = DEFAULT_EXECUTION_CONFIG,
    resolved_at: datetime | None = None,
    dry_run: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Append final outcomes for every registered decision with enough data."""

    root = Path(data_root)
    decision_files = _registration_files(root)
    if not decision_files:
        raise PaperTradingError("no registered paper decisions were found")
    resolved = _aware(resolved_at or datetime.now(timezone.utc), "resolved_at")
    contract_file = Path(contracts_path)
    config_file = _config_path(execution_config_path)
    contracts = prepare_contracts(read_export(contract_file))
    if contracts["date"].max().date() > resolved.date():
        raise PaperTradingError("contract snapshot contains observations after resolution")
    config = load_execution_config(config_file)
    config_hash = sha256_path(config_file)

    registrations = [(_read_json(path), path) for path in decision_files]
    actionable = [
        (record, path) for record, path in registrations
        if record.get("eligible_for_prospective_evidence")
        and record.get("decision", {}).get("action") in {"BUY", "SELL"}
    ]
    mismatched_configs = [
        record["paper_id"] for record, _ in actionable
        if record.get("execution_plan", {}).get("execution_config_sha256")
        != config_hash
    ]
    if mismatched_configs:
        raise PaperTradingError(
            "execution config differs from registered decision(s): "
            + ", ".join(mismatched_configs)
        )
    dates = [record["decision"]["date"] for record, _ in actionable]
    if len(dates) != len(set(dates)):
        raise PaperTradingError("prospective ledger contains duplicate decision dates")
    if not actionable:
        return [], 0

    predictions = pd.DataFrame([
        {
            "date": record["decision"]["date"],
            "target_end_date": record["decision"]["date"],
            "year": pd.Timestamp(record["decision"]["date"]).year,
            "actual": np.nan,
            "signal": VALID_ACTIONS[record["decision"]["action"]],
            "fold": -1,
            "sample": "prospective",
            "paper_id": record["paper_id"],
        }
        for record, _ in actionable
    ])
    replay = replay_frozen_signals(predictions, contracts, config=config)
    by_date = {pd.Timestamp(row["date"]).date().isoformat(): row for _, row in replay.iterrows()}
    outcome_dir = _paper_root(root) / "outcomes"
    contract_hash = sha256_path(contract_file)
    created: list[dict[str, Any]] = []
    pending = 0

    for registration, decision_path in actionable:
        row = by_date[registration["decision"]["date"]]
        status = row.get("execution_status")
        if status not in FINAL_REPLAY_STATUSES:
            pending += 1
            continue
        payload = _outcome_payload(
            registration,
            sha256_path(decision_path),
            row,
            contracts_sha256=contract_hash,
            execution_config_sha256=config_hash,
            resolved_at=resolved,
        )
        outcome_path = outcome_dir / f"{registration['paper_id']}.json"
        if outcome_path.exists():
            existing = _read_json(outcome_path)
            for key in ("execution_status", "entry_date", "exit_date"):
                if existing.get("execution", {}).get(key) != payload["execution"].get(key):
                    raise PaperTradingError(
                        f"revised contract data changes immutable outcome "
                        f"{registration['paper_id']} ({key})"
                    )
            continue
        created.append(payload)
        if not dry_run:
            _write_immutable(outcome_path, payload)
    return created, pending

