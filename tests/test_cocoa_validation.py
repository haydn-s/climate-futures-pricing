"""Fixed-signal cocoa validation tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.cocoa_validation import (
    acceptance_gate,
    align_predictions,
    baseline_ledger,
    cost_stress,
)
from pipeline.model_validation import ValidationMetrics, metrics


def model_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-03", periods=4, freq="W-FRI")
    return pd.DataFrame({
        "date": dates,
        "year": dates.year,
        "entry_date": dates + pd.offsets.BDay(1),
        "exit_date": dates + pd.offsets.BDay(6),
        "target_cocoa_5": [0.02, -0.01, 0.03, -0.02],
        "cost_rate": [0.001, 0.002, 0.001, 0.002],
        "cocoa_mom20": [0.1, 0.1, -0.1, -0.1],
        "cocoa_vol20": [0.2, 0.3, 0.2, 0.4],
    })


def predictions() -> pd.DataFrame:
    frame = model_frame()
    return pd.DataFrame({
        "date": frame["date"], "actual": frame["target_cocoa_5"],
        "score": [0.8, -0.7, 0.1, -0.2], "signal": [1, -1, 0, -1],
        "fold": [1, 1, 2, 2],
    })


def test_alignment_uses_row_costs_and_blocks_positions_touching_previous_exit() -> None:
    ledger = align_predictions(predictions(), model_frame())
    assert ledger["raw_signal"].tolist() == [1, -1, 0, -1]
    # Weekly entry coincides with the prior trade's exit, so it is skipped.
    assert ledger["signal"].tolist() == [1, 0, 0, -1]
    assert ledger["net"].tolist() == pytest.approx([0.019, 0.0, 0.0, 0.018])


def test_rule_baselines_use_identical_dates_costs_and_overlap_policy() -> None:
    ledger = align_predictions(predictions(), model_frame())
    long_only = baseline_ledger(ledger, "long_only")
    assert long_only["date"].tolist() == ledger["date"].tolist()
    assert long_only["signal"].tolist() == [1, 0, 1, 0]
    momentum = baseline_ledger(ledger, "momentum")
    assert momentum["raw_signal"].tolist() == [1, 1, -1, -1]


def test_double_cost_changes_economics_without_changing_actions() -> None:
    ledger = align_predictions(predictions(), model_frame())
    stressed, result = cost_stress(ledger, 2.0)
    assert stressed["signal"].tolist() == ledger["signal"].tolist()
    assert stressed.loc[0, "net"] == pytest.approx(0.018)
    assert result.total_net == pytest.approx(np.sum(stressed["net"]))


def test_gate_names_baseline_and_uncertainty_failures() -> None:
    strong = ValidationMetrics(100, 50, 0.6, 0.01, 0.5, 0.7, -0.1, 4, 5, 7, 10)
    baseline = ValidationMetrics(100, 50, 0.5, 0.01, 0.6, 0.8, -0.1, 4, 5, 7, 10)
    passed, checks = acceptance_gate(
        strong, baseline,
        {"bootstrap_probability_positive": 0.9, "permutation_p_value": 0.2},
        metrics(align_predictions(predictions(), model_frame())),
    )
    assert not passed
    assert not checks["beats best simple baseline net"]
    assert not checks["beats best simple baseline Sharpe"]
    assert not checks["circular-shift null p-value at most 10%"]
