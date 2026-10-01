"""Fold splitting: forward in time, and gapped by the embargo.

Every bug available here makes the score better rather than raising, so each is
tested for directly. The embargo test is the important one: without the gap, a
target that looks forward from the last training row finishes inside the test
block and the model is scored on an outcome it was trained on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline import backtest


# ------------------------------------------------------------------ fold shapes


def test_training_always_precedes_testing() -> None:
    """Shuffled CV would train on next month to predict last month."""
    for train, test in backtest.forward_folds(1000, n_splits=6, embargo=5):
        assert train.max() < test.min()
        assert train.min() == 0, "expanding window starts at the beginning"


def test_the_embargo_is_exactly_the_gap_requested() -> None:
    plain = backtest.forward_folds(1000, n_splits=4, embargo=0)
    gapped = backtest.forward_folds(1000, n_splits=4, embargo=10)
    for (train_plain, test_plain), (train_gap, test_gap) in zip(plain, gapped):
        assert test_plain.tolist() == test_gap.tolist(), "the test block does not move"
        assert len(train_plain) - len(train_gap) == 10
        assert test_gap.min() - train_gap.max() == 11, "ten rows sit between them"


def test_train_and_test_never_share_a_row() -> None:
    for train, test in backtest.forward_folds(500, n_splits=5, embargo=3):
        assert not set(train.tolist()) & set(test.tolist())


def test_folds_too_small_to_score_are_dropped_not_returned() -> None:
    """A two-row test set averaged into a headline is worse than no fold."""
    assert backtest.forward_folds(10, n_splits=6, embargo=0) == []
    assert backtest.forward_folds(0, n_splits=6, embargo=0) == []
    for train, test in backtest.forward_folds(1000, n_splits=6):
        assert len(train) >= backtest.MIN_TRAIN
        assert len(test) >= backtest.MIN_TEST


def test_an_embargo_longer_than_the_data_yields_no_folds_rather_than_empty_training() -> None:
    assert backtest.forward_folds(400, n_splits=6, embargo=10_000) == []


def test_a_negative_embargo_is_refused() -> None:
    with pytest.raises(ValueError, match="embargo must be >= 0"):
        backtest.forward_folds(1000, embargo=-5)


# -------------------------------------------------------------------- scoring


def noise_frame(n: int = 1400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "a": rng.normal(size=n), "b": rng.normal(size=n),
        "y": rng.normal(scale=0.02, size=n),
    })


def test_pure_noise_does_not_score_above_zero() -> None:
    """The sanity check on the whole harness: no signal, no R-squared."""
    scores = backtest.evaluate(noise_frame(), ["a", "b"], "y",
                               label="noise", embargo=5)
    assert scores, "a 1400-row frame must produce folds"
    for score in scores:
        assert score.r2 < 0.05, f"{score.model} found signal in noise: {score.r2}"


def test_a_real_relationship_is_found() -> None:
    """The opposite sanity check: a harness that can never score is useless."""
    frame = noise_frame()
    frame["y"] = 0.5 * frame["a"] + 0.01 * np.random.default_rng(1).normal(size=len(frame))
    scores = backtest.evaluate(frame, ["a", "b"], "y", label="signal", embargo=5)
    assert all(score.r2 > 0.9 for score in scores)


def test_r_squared_is_measured_against_the_training_mean() -> None:
    """A constant prediction must score exactly zero, not negative.

    Against the test mean it would score below zero, which is a harsher standard
    than any forecaster could have met -- nobody knows the test period's mean in
    advance.
    """
    frame = noise_frame()
    # A model that ignores its features and returns the training mean.
    class Constant:
        def fit(self, X, y):
            self.value = float(np.mean(y))
            return self

        def predict(self, X):
            return np.full(len(X), self.value)

    scores = backtest.evaluate(frame, ["a", "b"], "y", label="const", embargo=5,
                              models={"constant": Constant})
    assert scores[0].r2 == pytest.approx(0.0, abs=1e-9)


def test_rows_with_a_missing_feature_or_target_are_dropped_before_splitting() -> None:
    """Otherwise the folds are sized from rows the model never sees."""
    frame = noise_frame()
    frame.loc[:199, "a"] = np.nan
    scores = backtest.evaluate(frame, ["a", "b"], "y", label="holes", embargo=5)
    assert scores[0].n_train + scores[0].n_test <= len(frame) - 200


def test_a_frame_too_short_to_split_returns_no_scores() -> None:
    assert backtest.evaluate(noise_frame(20), ["a", "b"], "y",
                             label="tiny", embargo=5) == []


def test_the_untradeable_flag_travels_with_the_score() -> None:
    scores = backtest.evaluate(noise_frame(), ["a", "b"], "y", label="peek21",
                               embargo=5, tradeable=False)
    assert all(not score.tradeable for score in scores)
    assert "NOT TRADEABLE" in scores[0].line()
    assert "NOT TRADEABLE" not in backtest.evaluate(
        noise_frame(), ["a", "b"], "y", label="peek0", embargo=5)[0].line()
