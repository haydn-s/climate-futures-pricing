"""Targets, point-in-time signal features, and nested evaluation guards."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis import ml_signal_baseline
from pipeline import signal_eval, signals


def test_market_neutral_targets_remove_the_common_agricultural_move() -> None:
    frame = pd.DataFrame({
        "fwd5_corn": [0.04],
        "fwd5_soybeans": [0.02],
        "fwd5_wheat": [0.02],
        "corn_beta_ag126": [1.5],
    })

    result = signals.add_targets(frame, (5,))

    assert result.loc[0, "target_raw_corn_5"] == pytest.approx(0.04)
    assert result.loc[0, "target_corn_wheat_5"] == pytest.approx(0.02)
    assert result.loc[0, "target_corn_ag_beta_5"] == pytest.approx(0.01)


def test_pollination_interactions_only_turn_on_in_july_and_august() -> None:
    frame = pd.DataFrame({
        "month": [6, 7, 8, 9],
        "heat_dd7_z": [2.0] * 4,
        "d2_delta1": [3.0] * 4,
        "fc_610temp_rev": [4.0] * 4,
    })

    result = signals.add_stage_features(frame)

    assert result["stage_pollination"].tolist() == [0.0, 1.0, 1.0, 0.0]
    assert result["pollination_heat"].tolist() == [0.0, 2.0, 2.0, 0.0]
    assert result["pollination_drought"].tolist() == [0.0, 3.0, 3.0, 0.0]
    assert result["pollination_temp_revision"].tolist() == [0.0, 4.0, 4.0, 0.0]


def test_market_features_exclude_the_target_entry_close() -> None:
    dates = pd.date_range("2020-01-01", periods=160, freq="B")
    frame = pd.DataFrame({
        "date": dates,
        "close_corn": np.linspace(100.0, 140.0, len(dates)),
        "close_soybeans": np.linspace(100.0, 120.0, len(dates)),
        "close_wheat": np.linspace(100.0, 110.0, len(dates)),
    })
    original = signals.add_market_features(frame)
    changed = frame.copy()
    changed.loc[119:, "close_corn"] *= 10.0
    revised = signals.add_market_features(changed)

    pd.testing.assert_series_equal(
        original.loc[:119, "corn_beta_ag126"],
        revised.loc[:119, "corn_beta_ag126"],
    )
    assert original.loc[119, "corn_mom20"] == revised.loc[119, "corn_mom20"]
    assert original.loc[120, "corn_mom20"] != revised.loc[120, "corn_mom20"]


def test_forecasts_without_release_times_are_delayed_one_session() -> None:
    frame = pd.DataFrame({
        "fc_610temp": [1.0, 2.0, 3.0],
        "fc_610temp_rev": [0.1, 0.2, 0.3],
        "not_a_forecast": [4.0, 5.0, 6.0],
    })

    result = signals.lag_forecasts_with_unknown_release_time(frame)

    assert result["fc_610temp"].tolist()[1:] == [1.0, 2.0]
    assert result["fc_610temp_rev"].tolist()[1:] == [0.1, 0.2]
    assert np.isnan(result.loc[0, "fc_610temp"])
    assert result["not_a_forecast"].tolist() == [4.0, 5.0, 6.0]


def test_ten_day_decisions_are_thinned_to_non_overlapping_fortnights() -> None:
    dates = pd.date_range("2024-04-01", periods=60, freq="B")
    frame = pd.DataFrame({"date": dates, "month": dates.month})
    publications = pd.Series(pd.date_range("2024-04-04", periods=8, freq="7D"))

    five = signals.weekly_decisions(frame, publications, horizon=5)
    ten = signals.weekly_decisions(frame, publications, horizon=10)

    assert len(five) == 8
    assert len(ten) == 4
    assert ten["date"].tolist() == five.iloc[::2]["date"].tolist()


def test_holiday_shortened_week_does_not_create_overlapping_labels() -> None:
    dates = pd.date_range("2024-06-28", "2024-07-15", freq="B")
    dates = dates[dates != pd.Timestamp("2024-07-04")]
    frame = pd.DataFrame({"date": dates, "month": dates.month})
    publications = pd.Series(pd.to_datetime(["2024-07-03", "2024-07-10"]))

    decisions = signals.weekly_decisions(frame, publications, horizon=5)

    assert decisions["date"].tolist() == [pd.Timestamp("2024-07-03")]


def test_strategy_cost_is_charged_only_when_the_model_trades() -> None:
    actual = np.array([0.02, -0.01, 0.03])
    positions = np.array([1, 0, -1])

    net = signal_eval.strategy_returns(actual, positions, cost_rate=0.001)

    assert net.tolist() == pytest.approx([0.019, 0.0, -0.031])


def test_forecast_feature_set_starts_when_forecast_coverage_begins() -> None:
    dates = pd.date_range("2011-12-29", periods=4, freq="7D")
    frame = pd.DataFrame({
        "date": dates,
        "year": dates.year,
        "target_raw_corn_5": [0.01, 0.02, 0.03, 0.04],
        "d2_delta1": [1.0, 2.0, 3.0, 4.0],
        "fc_610temp": [np.nan, np.nan, 0.2, np.nan],
    })

    result = signal_eval._eligible_rows(
        frame, features=("d2_delta1", "fc_610temp"),
        target_column="target_raw_corn_5")

    assert result["date"].tolist() == dates[2:].tolist()


def test_nested_ridge_finds_a_planted_relationship_and_keeps_holdout_separate() -> None:
    rng = np.random.default_rng(4)
    dates = pd.date_range("2005-01-06", periods=1100, freq="7D")
    x = rng.normal(size=len(dates))
    target = 0.03 * x + rng.normal(scale=0.002, size=len(dates))
    frame = pd.DataFrame({
        "date": dates,
        "year": dates.year,
        "x": x,
        "target_raw_corn_5": target,
    })
    raw = signals.TargetSpec("raw_corn", "raw corn", 1)

    result = signal_eval.evaluate(
        frame, features=("x",), feature_set="synthetic", target=raw,
        horizon=5, model_name="ridge", cost_bps_per_leg=0.0,
        holdout_year=2024,
    )

    assert result.development.r2 > 0.95
    assert result.development.trades > 30
    assert result.holdout.trades > 3
    assert result.predictions["date"].max().year < 2024
    assert result.holdout_predictions["date"].min().year >= 2024


def test_unknown_model_and_negative_cost_fail_loudly() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        signal_eval.transaction_cost(signals.TARGETS[0], basis_points_per_leg=-1)
    with pytest.raises(ValueError, match="unknown model"):
        signal_eval._candidate_factories("neural-net")


def test_conclusion_compares_risk_adjusted_raw_and_relative_results() -> None:
    rows = pd.DataFrame([
        {
            "target": "raw_corn", "target_label": "raw corn",
            "feature_set": "weather", "model": "ridge", "horizon": 5,
            "dev_sharpe": 0.60, "holdout_total_net": 0.12,
            "holdout_sharpe": 0.40, "passes_gate": False,
        },
        {
            "target": "corn_wheat", "target_label": "corn minus wheat",
            "feature_set": "all_public", "model": "boosting", "horizon": 5,
            "dev_sharpe": 0.80, "holdout_total_net": 0.11,
            "holdout_sharpe": 0.90, "passes_gate": True,
        },
    ])

    message, _, best_relative = ml_signal_baseline.conclusion(rows)

    assert "clears the explicit paper-trading gate" in message
    assert best_relative is not None
    assert best_relative["model"] == "boosting"
