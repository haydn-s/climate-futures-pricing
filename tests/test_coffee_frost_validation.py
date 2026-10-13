"""Tests for the frozen Coffee C frost-curve validation."""

from __future__ import annotations

import pandas as pd

from pipeline.coffee_frost_validation import (
    FrostMetrics,
    acceptance_gate,
    classify_sample,
    frost_metrics,
    season_matched_null,
)


def test_sample_labels_do_not_promote_selected_or_consumed_history() -> None:
    assert classify_sample(2009) == "new_2000_2011_extension"
    assert classify_sample(2019) == "selected_2012_2023"
    assert classify_sample(2025) == "consumed_2024_plus"


def test_frost_metrics_score_fixed_long_curve_direction() -> None:
    ledger = pd.DataFrame({"net": [0.01, -0.02, 0.03]})
    metrics = frost_metrics(ledger)
    assert metrics.events == 3
    assert metrics.positive_events == 2
    assert metrics.positive_fraction == 2 / 3
    assert metrics.median_net_return == 0.01


def test_null_uses_same_year_winter_dates_and_is_reproducible() -> None:
    events = pd.DataFrame({
        "event_year": [2009, 2011],
        "frost_date": pd.to_datetime(["2009-06-10", "2011-07-10"]),
        "net": [0.01, 0.02],
    })
    dates = list(pd.date_range("2009-06-01", "2009-08-31", freq="7D"))
    dates += list(pd.date_range("2011-06-01", "2011-08-31", freq="7D"))
    daily = pd.DataFrame({
        "date": pd.to_datetime(dates),
        "year": [date.year for date in dates],
        "net": [-0.01 + index * 0.0001 for index in range(len(dates))],
    })
    first = season_matched_null(events, daily, draws=100, random_seed=7)
    second = season_matched_null(events, daily, draws=100, random_seed=7)
    assert first == second
    assert first["events"] == 2
    assert 0 < first["p_value"] <= 1


def test_gate_requires_sample_size_even_when_returns_are_positive() -> None:
    metrics = FrostMetrics(3, 0.01, 0.01, 3, 1.0)
    passed, checks = acceptance_gate(metrics, {"p_value": 0.01})
    assert not passed
    assert not checks["at least five newly added events"]
