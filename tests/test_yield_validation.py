"""Chronological yield forecasts must not learn from later harvests or features."""

import numpy as np
import pandas as pd
import pytest

from pipeline.yield_validation import expanding_predictions, score_predictions


def crop_years() -> pd.DataFrame:
    years = np.arange(2000, 2021)
    heat = np.random.default_rng(42).normal(size=len(years))
    return pd.DataFrame({
        "heat": heat,
        "yield_t_per_ha": (8 + 0.12 * (years - 2000)) * (1 - 0.05 * heat),
    }, index=years)


def test_later_harvests_and_features_cannot_change_an_earlier_forecast() -> None:
    table = crop_years()
    expected = expanding_predictions(table, {"weather": ["heat"]})
    changed = table.copy()
    changed.loc[2015:, "yield_t_per_ha"] *= 1.5
    changed.loc[2015:, "heat"] += 100
    actual = expanding_predictions(changed, {"weather": ["heat"]})
    pd.testing.assert_frame_equal(expected.loc[expected.year < 2015],
                                  actual.loc[actual.year < 2015])
    assert (actual.train_end < actual.year).all()
    assert expected.year.min() == 2010


def test_held_out_harvest_cannot_change_its_trend_or_weather_prediction() -> None:
    table = crop_years()
    expected = expanding_predictions(table, {"weather": ["heat"]})
    table.loc[2010, "yield_t_per_ha"] *= 0.5
    actual = expanding_predictions(table, {"weather": ["heat"]})
    columns = ["feature_set", "trend_yield", "predicted_yield", "predicted_anomaly"]
    pd.testing.assert_frame_equal(expected.loc[expected.year == 2010, columns],
                                  actual.loc[actual.year == 2010, columns])
    assert actual.loc[actual.year == 2010, "actual_anomaly"].max() < -0.4


def test_trend_only_extrapolates_a_known_linear_yield_series() -> None:
    table = crop_years()
    table["yield_t_per_ha"] = 8 + 0.12 * (table.index - 2000)
    predictions = expanding_predictions(table, {"weather": ["heat"]})
    assert np.allclose(predictions.predicted_yield, predictions.actual_yield)
    scores = score_predictions(predictions)
    assert scores.rmse_t_per_ha.max() < 1e-10
    assert scores.r2_vs_trend.isna().all()  # no baseline error to improve on


def test_r2_uses_each_forecasts_training_trend_not_the_test_mean() -> None:
    predictions = pd.DataFrame({
        "feature_set": ["weather"] * 2, "year": [2010, 2011],
        "actual_yield": [10.0, 10.0], "trend_yield": [8.0, 12.0],
        "predicted_yield": [9.0, 11.0],
        "actual_anomaly": [0.25, -1 / 6], "predicted_anomaly": [0.125, -1 / 12],
    })
    score = score_predictions(predictions).iloc[0]
    assert score.r2_vs_trend == pytest.approx(0.75)
    assert score.mae_t_per_ha == 1.0
    assert score.trend_mae_t_per_ha == 2.0


def test_candidates_and_control_share_complete_training_and_test_years() -> None:
    table = crop_years()
    table["rain"] = 2.0
    table.loc[2003, "rain"] = np.nan
    table.loc[2015, "heat"] = np.inf
    predictions = expanding_predictions(table, {"heat": ["heat"], "rain": ["rain"]})
    for _, group in predictions.groupby("feature_set"):
        assert group.year.tolist() == [2011, 2012, 2013, 2014, 2016, 2017, 2018, 2019, 2020]
        assert group.train_years.tolist() == list(range(10, 19))


def test_excluding_2012_removes_it_from_later_training_as_well() -> None:
    table = crop_years()
    full = expanding_predictions(table, {"weather": ["heat"]})
    excluded = expanding_predictions(table.drop(index=2012), {"weather": ["heat"]})
    assert 2012 not in excluded.year.to_numpy()
    assert full.loc[full.year == 2013, "train_years"].iloc[0] == 13
    assert excluded.loc[excluded.year == 2013, "train_years"].iloc[0] == 12


def test_insufficient_history_returns_no_predictions() -> None:
    assert expanding_predictions(crop_years().iloc[:10], {"weather": ["heat"]}).empty


def test_duplicate_years_and_invalid_minimum_are_rejected() -> None:
    table = crop_years()
    with pytest.raises(ValueError, match="unique integer years"):
        expanding_predictions(pd.concat([table, table.iloc[:1]]), {"weather": ["heat"]})
    with pytest.raises(ValueError, match="at least three"):
        expanding_predictions(table, {"weather": ["heat"]}, min_train_years=2)
