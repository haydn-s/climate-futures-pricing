"""The horse race must be honest about timing; the budget about its arithmetic."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.price_information import (
    ALPHA, BASELINE_MONTH, POWER, break_even_correlation, continuous_signal,
    december_contract_signal, detectable_correlation, horse_race, power_budget,
    required_observations, return_moments,
)


def contracts(years=range(2012, 2026)) -> pd.DataFrame:
    """Dated December contracts quoted daily from the previous October."""
    rows = []
    for year in years:
        dates = pd.date_range(f"{year - 1}-10-01", f"{year}-11-30", freq="B")
        # A deterministic ramp so each year's April-to-August change is known.
        level = 500.0 + 10.0 * (year - 2012)
        for index, date in enumerate(dates):
            rows.append({
                "date": date, "root": "ZC", "contract": f"ZCZ{year % 100:02d}",
                "last_trade_date": pd.Timestamp(f"{year}-12-14"),
                "open_cents": level, "high_cents": level, "low_cents": level,
                "settlement_cents": level + index, "volume": 100.0,
                "open_interest": 1000.0,
            })
    return pd.DataFrame(rows)


def prices(start="2000-01-03", end="2026-09-25") -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="B")
    rng = np.random.default_rng(4)
    close = 400.0 * np.exp(np.cumsum(rng.normal(0, 0.018, len(dates))))
    return pd.DataFrame({"symbol": "ZC=F", "date": dates, "close": close})


def predictions(years=range(2012, 2026)) -> pd.DataFrame:
    """A nowcast that is correlated with the harvest, for a race to be run on."""
    rng = np.random.default_rng(9)
    rows = []
    for year in years:
        actual = float(rng.normal(0, 0.08))
        for month in (8, 9, 10):
            rows.append({
                "sample": "all_years", "issue_month": month,
                "feature_set": "points_drought", "year": year,
                "actual_anomaly": actual,
                "predicted_anomaly": 0.6 * actual + float(rng.normal(0, 0.02)),
            })
    return pd.DataFrame(rows)


def test_december_signal_uses_only_prices_before_the_issue_date():
    signals = december_contract_signal(contracts())
    assert not signals.empty
    for row in signals.itertuples(index=False):
        issue = pd.Timestamp(row.year, row.issue_month, 1)
        assert row.signal_date < issue
        assert row.baseline_date <= pd.Timestamp(row.year, BASELINE_MONTH, 1)
    # A later issue date sees strictly more of the season's ramp.
    pivot = signals.pivot(index="year", columns="issue_month",
                          values="price_change")
    assert (pivot[10] > pivot[8]).all()


def test_december_signal_picks_the_matching_delivery_year():
    signals = december_contract_signal(contracts(years=[2015, 2016]))
    assert sorted(signals["year"].unique()) == [2015, 2016]
    assert set(signals["price_source"]) == {"december_contract"}


def test_continuous_signal_window_starts_at_the_baseline():
    signals = continuous_signal(prices())
    assert not signals.empty
    for row in signals.itertuples(index=False):
        assert row.signal_date < pd.Timestamp(row.year, row.issue_month, 1)
        assert row.baseline_date <= pd.Timestamp(row.year, BASELINE_MONTH, 1)


def test_a_missing_root_is_rejected_rather_than_returned_empty():
    with pytest.raises(ValueError, match="December contracts"):
        december_contract_signal(contracts(), root="ZW")
    with pytest.raises(ValueError, match="no ZS=F prices"):
        continuous_signal(prices(), symbol="ZS=F")


def test_incremental_r2_is_the_joint_minus_price_only_fit():
    race = horse_race(predictions(), december_contract_signal(contracts()))
    assert not race.empty
    for row in race.itertuples(index=False):
        assert row.incremental_r2 == pytest.approx(row.joint_r2 - row.price_r2)
        # Adding a predictor cannot reduce in-sample R-squared.
        assert row.joint_r2 >= row.price_r2 - 1e-12
        assert row.joint_r2 >= row.nowcast_r2 - 1e-12
        assert row.joint_adjusted_r2 <= row.joint_r2 + 1e-12


def test_a_price_that_knows_nothing_leaves_the_nowcast_intact():
    """With an uninformative price the nowcast must keep its coefficient."""
    forecasts = predictions()
    noise = december_contract_signal(contracts()).copy()
    generator = np.random.default_rng(1)
    noise["price_change"] = generator.normal(0, 0.1, len(noise))
    race = horse_race(forecasts, noise)
    row = race.loc[race["issue_month"] == 8].iloc[0]
    assert row["nowcast_beta_retained"] == pytest.approx(1.0, abs=0.25)
    assert row["price_r2"] < 0.3


def test_a_price_that_knows_everything_absorbs_the_nowcast():
    """When the price carries the outcome, the nowcast must stop mattering."""
    forecasts = predictions()
    signals = december_contract_signal(contracts())
    merged = signals.merge(
        forecasts.loc[forecasts["issue_month"] == 8, ["year", "actual_anomaly"]],
        on="year", how="left")
    merged["price_change"] = -merged["actual_anomaly"]
    race = horse_race(forecasts, merged.drop(columns=["actual_anomaly"]))
    row = race.loc[race["issue_month"] == 8].iloc[0]
    assert row["price_r2"] > 0.95
    assert abs(row["incremental_r2"]) < 0.02
    assert abs(row["nowcast_beta_joint"]) < abs(row["nowcast_beta_alone"])


def test_drop_one_range_brackets_the_reported_increment():
    race = horse_race(predictions(), december_contract_signal(contracts()))
    for row in race.itertuples(index=False):
        assert (row.incremental_r2_min_drop_one
                <= row.incremental_r2_max_drop_one)
        assert 2012 <= row.most_load_bearing_year <= 2025


def test_break_even_scales_with_cost_and_falls_with_volatility():
    cheap = break_even_correlation(0.045, 10.0)
    dear = break_even_correlation(0.045, 50.0)
    volatile = break_even_correlation(0.090, 10.0)
    assert dear == pytest.approx(5.0 * cheap)
    assert volatile == pytest.approx(cheap / 2.0)
    # A sign rule on 4.5% weekly moves needs very little edge to clear 10 bps.
    assert cheap < 0.05
    assert break_even_correlation(0.045, 0.0) == 0.0


def test_detection_threshold_and_required_sample_are_inverses():
    for observations in (100, 781, 5000):
        rho = detectable_correlation(observations, alpha=ALPHA, power=POWER)
        assert required_observations(rho, alpha=ALPHA, power=POWER) == \
            pytest.approx(observations, rel=1e-6)
    # More data detects smaller effects.
    assert detectable_correlation(5000) < detectable_correlation(100)


def test_power_budget_flags_only_the_undetectable_costs():
    moments = return_moments(prices())
    budget = power_budget(moments, cost_bps=(10.0, 24.0, 50.0))
    assert not budget.empty
    for row in budget.itertuples(index=False):
        assert row.blind_to_profitable_signal == (
            row.break_even_correlation < row.detectable_correlation)
        assert row.break_even_r2 == pytest.approx(
            row.break_even_correlation ** 2)
        assert row.observation_shortfall == pytest.approx(
            row.observations_needed / row.independent_observations)


def test_moments_count_non_overlapping_returns_separately():
    moments = return_moments(prices()).set_index("horizon_days")
    assert moments.loc[5, "independent_observations"] == \
        moments.loc[5, "overlapping_observations"] // 5
    assert moments.loc[1, "independent_observations"] == \
        moments.loc[1, "overlapping_observations"]
    # Longer horizons have wider dispersion.
    assert moments.loc[10, "return_sd"] > moments.loc[1, "return_sd"]


def test_degenerate_inputs_are_rejected():
    with pytest.raises(ValueError, match="dispersion must be positive"):
        break_even_correlation(0.0, 10.0)
    with pytest.raises(ValueError, match="cost cannot be negative"):
        break_even_correlation(0.045, -1.0)
    with pytest.raises(ValueError, match="four observations"):
        detectable_correlation(3)
    with pytest.raises(ValueError, match="non-zero"):
        required_observations(0.0)
