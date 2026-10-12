"""Tests for the locked execution-aligned validation primitives."""

from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline import model_validation


def execution_rows(n: int = 12) -> pd.DataFrame:
    dates = pd.date_range("2020-04-01", periods=n, freq="7D")
    return pd.DataFrame({
        "date": dates,
        "year": dates.year,
        "entry_date": dates + pd.offsets.BDay(1),
        "exit_date": dates + pd.offsets.BDay(6),
        "gross": np.linspace(-0.03, 0.03, n),
        "cost": 0.001,
        "raw_signal": np.ones(n, dtype=int),
        "signal": np.ones(n, dtype=int),
        "net": np.linspace(-0.03, 0.03, n) - 0.001,
        "fold": np.repeat([1, 2], n // 2),
    })


def test_no_overlap_suppresses_a_trade_entering_on_previous_exit() -> None:
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-12"]),
        "entry_date": pd.to_datetime(["2026-01-05", "2026-01-12", "2026-01-13"]),
        "exit_date": pd.to_datetime(["2026-01-12", "2026-01-19", "2026-01-20"]),
    })

    result = model_validation.apply_no_overlap(frame, np.array([1, -1, 1]))

    assert result.tolist() == [1, 0, 1]


def test_metrics_count_years_folds_and_trades() -> None:
    frame = execution_rows()
    result = model_validation.metrics(frame)

    assert result.observations == len(frame)
    assert result.trades == len(frame)
    assert result.folds == 2
    assert result.years == 1
    assert result.total_net == frame["net"].sum()


def test_cost_aware_classes_leave_small_moves_flat() -> None:
    classes = model_validation._classes(
        np.array([-0.02, -0.002, 0.0, 0.002, 0.02]),
        np.full(5, 0.001),
    )

    assert classes.tolist() == [-1, 0, 0, 0, 1]


def test_gate_uses_development_metrics_not_consumed_holdout() -> None:
    predictions = execution_rows(40)
    predictions["gross"] = np.tile([0.015, 0.020, 0.025, 0.030, 0.018], 8)
    predictions["net"] = predictions["gross"] - predictions["cost"]
    predictions["signal"] = 1
    predictions["year"] = np.repeat([2019, 2020, 2021, 2022], 10)
    predictions["fold"] = np.repeat([1, 2, 3, 4], 10)
    strong = model_validation.metrics(predictions)
    terrible_holdout = model_validation.ValidationMetrics(
        observations=10, trades=10, hit_rate=0.0, mean_net_per_trade=-1.0,
        total_net=-10.0, sharpe=-5.0, max_drawdown=-0.9,
        positive_folds=0, folds=1, positive_years=0, years=1,
    )
    result = model_validation.ValidationResult(
        name="candidate", kind="candidate", target="corn_wheat",
        target_label="corn minus wheat", feature_set="all_public",
        model="boosting", objective="net_return_regression",
        development=strong, consumed_holdout=terrible_holdout,
        development_predictions=predictions,
        holdout_predictions=pd.DataFrame(),
    )
    weak = model_validation.ValidationMetrics(
        observations=40, trades=0, hit_rate=np.nan, mean_net_per_trade=np.nan,
        total_net=0.0, sharpe=np.nan, max_drawdown=0.0,
        positive_folds=0, folds=4, positive_years=0, years=4,
    )
    baseline = model_validation.ValidationResult(
        name="baseline", kind="baseline", target="corn_wheat",
        target_label="corn minus wheat", feature_set="rule", model="no_trade",
        objective="predeclared_rule", development=weak,
        consumed_holdout=weak, development_predictions=pd.DataFrame(),
        holdout_predictions=pd.DataFrame(),
    )
    uncertainty = {
        "bootstrap_probability_positive": 1.0,
        "permutation_p_value": 0.01,
    }

    passed, failures, stress = model_validation.gate(result, baseline, uncertainty)

    assert passed
    assert failures == []
    assert stress > 0


def test_uncertainty_is_deterministic() -> None:
    frame = execution_rows(12)
    first = model_validation.uncertainty(frame, draws=50, seed=9)
    second = model_validation.uncertainty(frame, draws=50, seed=9)

    assert first == second
