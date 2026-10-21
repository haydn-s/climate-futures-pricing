"""The decision demo must stay price-free, chronological, and beatable."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.yield_decisions import (
    PENALTY_RATIOS, STRATEGIES, _newsvendor_quantile, bootstrap_difference,
    decide, predictive_shortfalls, score_decisions, severity_strata,
    spread_diagnostic,
)
from pipeline.yield_intervals import FoldResiduals, fold_residuals

FEATURES = {"weather": ["heat", "rain"]}


def fold(*, point=10.0, trend=10.0, actual=10.0, signed=None,
         anomalies=None) -> FoldResiduals:
    rng = np.random.default_rng(5)
    return FoldResiduals(
        feature_set="weather", year=2020, train_years=18, point=point,
        trend=trend, actual=actual,
        signed=np.asarray(signed if signed is not None
                          else rng.normal(0, 0.4, 18)),
        loo_point=np.full(18, point),
        train_anomalies=np.asarray(anomalies if anomalies is not None
                                   else rng.normal(0, 0.05, 18)),
    )


def table(years=range(2000, 2026)) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    years = list(years)
    heat = rng.uniform(0, 120, len(years))
    frame = pd.DataFrame({"heat": heat, "rain": rng.uniform(200, 500, len(years))},
                         index=pd.Index(years, name="year"))
    trend = 9.0 + 0.09 * (frame.index.to_numpy() - 2000)
    frame["yield_t_per_ha"] = trend * (
        1.0 - 0.0015 * heat + rng.normal(0, 0.02, len(years)))
    return frame


def test_no_price_or_return_column_reaches_the_decision():
    """The loss must depend on the harvest alone, never on a price."""
    decisions = decide(fold_residuals(table(), FEATURES))
    forbidden = ("price", "return", "settle", "close", "basis", "futures")
    assert not [column for column in decisions.columns
                if any(word in column.lower() for word in forbidden)]


def test_the_buffer_is_the_newsvendor_optimum_for_its_distribution():
    """A grid search over buffers must not beat the critical-fractile choice."""
    shortfalls = np.array([0.0, 0.0, 0.1, 0.3, 0.4, 0.8, 1.2, 2.5])
    for ratio in (2.0, 4.0, 8.0):
        chosen = _newsvendor_quantile(shortfalls, ratio / (ratio + 1.0))
        expected = np.mean([ratio * max(0.0, s - chosen) + max(0.0, chosen - s)
                            for s in shortfalls])
        grid = [np.mean([ratio * max(0.0, s - b) + max(0.0, b - s)
                         for s in shortfalls])
                for b in np.linspace(0, shortfalls.max(), 400)]
        # The fractile rule must be within a grid step of the best buffer.
        assert expected <= min(grid) + 0.05


def test_a_sharper_forecast_cannot_cost_more_on_average():
    """Shrinking the residuals toward the truth must not raise the cost."""
    rng = np.random.default_rng(11)
    anomalies = rng.normal(0, 0.05, 20)
    sharp = decide([fold(point=9.4, trend=10.0, actual=9.4,
                         signed=rng.normal(0, 0.05, 20), anomalies=anomalies)],
                   strategies=("model",))
    blunt = decide([fold(point=9.4, trend=10.0, actual=9.4,
                         signed=rng.normal(0, 0.8, 20), anomalies=anomalies)],
                   strategies=("model",))
    assert sharp["cost"].sum() < blunt["cost"].sum()


def test_cost_is_asymmetric_in_the_stated_direction():
    short = decide([fold(point=10.0, trend=10.0, actual=8.0,
                         signed=np.zeros(18))], penalty_ratios=(4.0,),
                   strategies=("model",)).iloc[0]
    carried = decide([fold(point=8.0, trend=10.0, actual=10.0,
                           signed=np.zeros(18))], penalty_ratios=(4.0,),
                     strategies=("model",)).iloc[0]
    # Two tonnes short costs four times two tonnes needlessly carried.
    assert short["cost"] == pytest.approx(4.0 * 2.0)
    assert carried["cost"] == pytest.approx(2.0)


def test_shortfall_samples_are_one_sided_and_share_the_trend_baseline():
    samples = predictive_shortfalls(fold(point=9.0, trend=10.0))
    assert set(samples) == {"climatology", "climatology_scaled", "model",
                            "model_point_only"}
    for name, values in samples.items():
        assert (values >= 0).all(), name
    # A forecast far above trend implies no provisioning at all.
    assert predictive_shortfalls(
        fold(point=99.0, trend=10.0, signed=np.zeros(18)))["model"].max() == 0.0


def test_the_scaled_control_matches_the_model_spread():
    """The conservatism control must actually carry the model's uncertainty."""
    record = fold(signed=np.random.default_rng(2).normal(0, 1.5, 18))
    deviations = record.trend * (record.train_anomalies
                                 - record.train_anomalies.mean())
    scaled = record.trend + deviations * (
        record.signed.std(ddof=1) / deviations.std(ddof=1))
    assert scaled.std(ddof=1) == pytest.approx(record.signed.std(ddof=1))
    diagnostic = spread_diagnostic([record]).iloc[0]
    assert diagnostic["spread_ratio"] == pytest.approx(
        record.signed.std(ddof=1) / deviations.std(ddof=1))


def test_no_buffer_provisions_nothing_and_pays_the_full_penalty():
    row = decide([fold(trend=10.0, actual=9.0)], penalty_ratios=(4.0,),
                 strategies=("no_buffer",)).iloc[0]
    assert row["buffer"] == 0.0
    assert row["cost"] == pytest.approx(4.0 * 1.0)


def test_scores_are_signed_so_positive_always_means_cheaper():
    decisions = decide(fold_residuals(table(), FEATURES))
    scores = score_decisions(decisions)
    baseline = scores.loc[scores["strategy"] == "climatology"].set_index(
        "penalty_ratio")
    for row in scores.itertuples(index=False):
        reference = baseline.loc[row.penalty_ratio, "total_cost"]
        assert row.total_cost_vs_baseline == pytest.approx(
            reference - row.total_cost)
        if row.strategy == "climatology":
            assert row.cost_reduction == pytest.approx(0.0)


def test_bootstrap_is_deterministic_and_brackets_its_point_estimate():
    decisions = decide(fold_residuals(table(), FEATURES))
    first = bootstrap_difference(decisions)
    second = bootstrap_difference(decisions)
    pd.testing.assert_frame_equal(first, second)
    for row in first.itertuples(index=False):
        assert row.bootstrap_p025 <= row.bootstrap_median <= row.bootstrap_p975
        assert 0.0 <= row.probability_positive <= 1.0
        assert row.harvests_improved + row.harvests_worsened <= row.harvests


def test_regime_strata_partition_the_harvests():
    decisions = decide(fold_residuals(table(), FEATURES))
    strata = severity_strata(decisions)
    for keys, frame in strata.groupby(["feature_set", "penalty_ratio",
                                       "strategy"]):
        total = frame["harvests"].sum()
        scored = decisions.loc[
            (decisions["feature_set"] == keys[0])
            & (decisions["penalty_ratio"] == keys[1])
            & (decisions["strategy"] == keys[2])]
        assert total == len(scored)
    assert set(strata["regime"]).issubset({"shortfall", "at_or_above_trend"})


def test_bad_inputs_are_rejected():
    folds = list(fold_residuals(table(), FEATURES))
    with pytest.raises(ValueError, match="penalty ratios"):
        decide(folds, penalty_ratios=(0.0,))
    with pytest.raises(ValueError, match="unknown strategies"):
        decide(folds, strategies=("crystal_ball",))
    with pytest.raises(ValueError, match="at least one value"):
        _newsvendor_quantile(np.array([]), 0.8)


def test_defaults_cover_the_reported_grid():
    decisions = decide(fold_residuals(table(), FEATURES))
    assert sorted(set(decisions["penalty_ratio"])) == sorted(PENALTY_RATIOS)
    assert set(decisions["strategy"]) == set(STRATEGIES)
