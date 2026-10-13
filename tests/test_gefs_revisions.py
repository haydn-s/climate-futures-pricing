"""Daily completeness and revision tests for operational GEFS features."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from pipeline.features.gefs_revisions import (
    GefsFeatureError,
    build_revision_features,
    daily_point_forecasts,
)


def panel(*, dates=("2024-07-14", "2024-07-15"), points=("Iowa", "Illinois")):
    rows = []
    for run_number, stamp in enumerate(dates):
        initialized = pd.Timestamp(stamp, tz="UTC")
        for point_number, point in enumerate(points):
            for day in range(1, 11):
                for slot in (6, 12, 18, 24):
                    lead = (day - 1) * 24 + slot
                    valid = initialized + timedelta(hours=lead)
                    base = run_number * 2 + point_number
                    values = {
                        "tmax_c": 28 + day + slot / 24 + base,
                        "tmin_c": 18 + day - slot / 24 + base,
                        "precip_mm": 1 + run_number * 0.5,
                    }
                    for variable, value in values.items():
                        rows.append({
                            "initialized_at": initialized,
                            "model_available_at": initialized + timedelta(hours=7),
                            "valid_at": valid,
                            "lead_hours": lead,
                            "statistic": "geavg",
                            "variable": variable,
                            "point": point,
                            "value": value,
                        })
    return pd.DataFrame(rows)


def test_four_intervals_become_daily_extrema_and_total_precipitation() -> None:
    daily = daily_point_forecasts(panel(dates=("2024-07-14",), points=("Iowa",)))
    first = daily.query("forecast_day == 1").iloc[0]
    assert first["tmax_c"] == pytest.approx(30.0)  # max at f024
    assert first["tmin_c"] == pytest.approx(18.0)  # min at f024
    assert first["precip_mm"] == pytest.approx(4.0)


def test_an_incomplete_point_day_is_excluded_instead_of_imputed() -> None:
    source = panel(dates=("2024-07-14",), points=("Iowa",))
    source = source[~((source["lead_hours"] == 6) & (source["variable"] == "precip_mm"))]
    daily = daily_point_forecasts(source)
    assert 1 not in set(daily["forecast_day"])
    assert set(daily["forecast_day"]) == set(range(2, 11))


def test_a_day_missing_one_point_cannot_change_the_belt_composition() -> None:
    source = panel(dates=("2024-07-14",))
    source = source[~(
        (source["point"] == "Iowa")
        & (source["lead_hours"] == 6)
        & (source["variable"] == "precip_mm")
    )]
    assert build_revision_features(source).empty


def test_revision_features_compare_only_consecutive_operational_runs() -> None:
    features = build_revision_features(panel())
    assert len(features) == 2
    first, second = features.iloc[0], features.iloc[1]
    assert not first["has_prior_day_revision"]
    assert pd.isna(first["rev_d1_3_tmax_c_mean"])
    assert second["has_prior_day_revision"]
    assert second["rev_d1_3_tmax_c_mean"] == pytest.approx(2.0)
    # Each six-hour precipitation value rises 0.5; 3 days x 4 intervals = 6 mm.
    assert second["rev_d1_3_precip_mm_sum"] == pytest.approx(6.0)


def test_a_gap_does_not_turn_a_two_day_change_into_a_daily_revision() -> None:
    features = build_revision_features(panel(dates=("2024-07-13", "2024-07-15")))
    assert not features.iloc[1]["has_prior_day_revision"]
    assert pd.isna(features.iloc[1]["rev_d4_7_heat_dd29_sum"])


def test_invalid_lead_grid_fails_loudly() -> None:
    source = panel(dates=("2024-07-14",), points=("Iowa",))
    source.loc[source.index[0], "lead_hours"] = 7
    with pytest.raises(GefsFeatureError, match="six-hour steps"):
        daily_point_forecasts(source)
