"""The frozen inference path must be reproducible, cautious, and non-executing."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline import signal_inference
from pipeline.signals import FEATURE_SETS

ROOT = Path(__file__).resolve().parents[1]


def synthetic_frame(rows: int = 180) -> pd.DataFrame:
    rng = np.random.default_rng(14)
    dates = pd.date_range("2022-01-06", periods=rows, freq="7D")
    feature = rng.normal(size=rows)
    target = 0.03 * feature + rng.normal(scale=0.002, size=rows)
    return pd.DataFrame({
        "date": dates,
        "year": dates.year,
        "market_data_through": dates - pd.Timedelta(days=1),
        "fc_610temp": feature,
        "target_corn_wheat_5": target,
        "target_end_date_5": dates + pd.Timedelta(days=5),
    })


def test_committed_baseline_matches_the_frozen_runtime_contract() -> None:
    path = (ROOT / "models" / signal_inference.MODEL_VERSION / "baseline.json")
    baseline = json.loads(path.read_text())

    assert baseline["baseline_id"] == signal_inference.BASELINE_ID
    assert baseline["model_version"] == signal_inference.MODEL_VERSION
    assert baseline["holdout"]["consumed"] is True
    assert baseline["holdout"]["reuse_for_model_selection"] is False
    assert baseline["specification"]["features"] == list(FEATURE_SETS["all_public"])
    assert baseline["evaluation"]["live_capital_approved"] is False


def test_training_outcome_must_finish_strictly_before_decision() -> None:
    frame = synthetic_frame(70)
    decision = frame.loc[60, "date"]

    result = signal_inference.training_rows(
        frame, features=("fc_610temp",), decision_date=decision,
        target_column="target_corn_wheat_5", target_end_column="target_end_date_5")

    assert (result["target_end_date_5"] < decision).all()
    assert result["target_end_date_5"].max() < decision


def test_stale_inputs_force_hold_without_changing_the_model_read() -> None:
    frame = synthetic_frame()
    generated = datetime(2026, 1, 2, 15, 30, tzinfo=UTC)
    as_of = frame["date"].max().date() + timedelta(days=20)

    run = signal_inference.infer(
        frame, features=("fc_610temp",), as_of=as_of,
        generated_at=generated, snapshot={"test": True})

    assert run.record["signal"]["action"] == "HOLD"
    assert run.record["signal"]["position"] == 0
    assert run.record["decision"]["status"] == "STALE_INPUTS"
    assert run.record["execution"] == {
        "authorized": False,
        "mode": "paper-trading signal only",
        "orders_submitted": 0,
    }
    assert any("forced to HOLD" in warning for warning in run.record["warnings"])


def test_fresh_signal_preserves_model_action_but_still_cannot_execute() -> None:
    frame = synthetic_frame()
    run = signal_inference.infer(
        frame, features=("fc_610temp",), as_of=frame["date"].max().date(),
        generated_at=datetime(2026, 1, 2, 15, 30, tzinfo=UTC),
        snapshot={"test": True})

    assert run.record["decision"]["status"] == "READY_FOR_PAPER_TRADING"
    assert run.record["signal"]["action"] == run.record["signal"]["model_action"]
    assert run.record["signal"]["position"] == run.record["signal"]["model_position"]
    expected_legs = 0 if run.record["signal"]["action"] == "HOLD" else 2
    assert len(run.record["signal"]["legs"]) == expected_legs
    assert run.record["execution"]["authorized"] is False


def test_signal_artifact_and_record_are_hashed_and_never_overwritten(tmp_path) -> None:
    frame = synthetic_frame()
    generated = datetime(2026, 1, 2, 15, 30, tzinfo=UTC)
    run = signal_inference.infer(
        frame, features=("fc_610temp",), as_of=frame["date"].max().date(),
        generated_at=generated, snapshot={"test": True})

    record_path, model_path = signal_inference.save_run(run, tmp_path)
    record = json.loads(record_path.read_text())
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()

    assert record["model_artifact"]["sha256"] == digest
    assert record["execution"]["orders_submitted"] == 0
    assert record_path.is_file() and model_path.is_file()
    loaded = signal_inference.load_model_artifact(model_path)
    sample = frame.tail(1)
    assert loaded.scores(sample) == run.fitted.scores(sample)
    assert loaded.positions(sample) == run.fitted.positions(sample)
    try:
        signal_inference.save_run(run, tmp_path)
    except FileExistsError:
        pass
    else:
        raise AssertionError("an audit record must never be overwritten")
