"""Scoring a forecast without letting it see the answer.

Shared by the daily peek sweep and the weekly model, because the moment two
scripts each keep their own copy of a fold splitter, one of them loses the
embargo and nobody notices -- the symptom is a better number, not an error.

THE EMBARGO IS THE WHOLE POINT. A target that looks `horizon` days forward from
the last training row finishes inside the test block, so an ungapped split hands
the model part of the outcome it is being scored on. Dropping `embargo` rows from
the end of every training block is what makes the two independent. This is the
cheapest way to produce a publishable R-squared from noise, and it does not
raise.

Splits run forward in time only. Shuffled cross-validation on a price series
trains on next month to predict last month, which is not a forecast.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np
import pandas as pd

DEFAULT_SPLITS = 6
MIN_TRAIN = 50
MIN_TEST = 10


@dataclass(frozen=True)
class Score:
    """One model, one information set, out of sample."""

    label: str
    model: str
    r2: float
    hit_rate: float
    n_train: int
    n_test: int
    n_folds: int
    tradeable: bool = True

    def line(self) -> str:
        flag = "" if self.tradeable else "  NOT TRADEABLE"
        return (f"  {self.label:12s} {self.model:8s}  R2 {self.r2:+.4f}   "
                f"hit {self.hit_rate:.1%}   train {self.n_train:5d} test {self.n_test:4d}"
                f"  folds {self.n_folds}{flag}")


def forward_folds(n: int, n_splits: int = DEFAULT_SPLITS,
                  embargo: int = 0) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window splits, forward in time, gapped by `embargo` rows.

    Fold k trains on everything up to boundary k minus the embargo, and tests on
    the block that follows. A fold too small to be worth scoring is dropped
    rather than returned, so a caller never averages a two-row test set into a
    headline.
    """
    if embargo < 0:
        raise ValueError(f"embargo must be >= 0, found {embargo}")
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    fold_size = n // (n_splits + 1)
    if fold_size == 0:
        return folds
    for split in range(1, n_splits + 1):
        boundary = fold_size * split
        test_end = min(fold_size * (split + 1), n)
        train = np.arange(0, max(boundary - embargo, 0))
        test = np.arange(boundary, test_end)
        if len(train) >= MIN_TRAIN and len(test) >= MIN_TEST:
            folds.append((train, test))
    return folds


def default_models() -> Mapping[str, Callable[[], object]]:
    """Ridge for the linear read, gradient boosting for the non-linear one.

    Both are deliberately small. With a few thousand rows and a target that is
    mostly noise, a larger model does not find more signal, it finds more of the
    training set.
    """
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return {
        "ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "gbm": lambda: HistGradientBoostingRegressor(
            max_depth=3, max_iter=200, learning_rate=0.05, random_state=0),
    }


def evaluate(frame: pd.DataFrame, features: list[str], target: str, *,
             label: str, embargo: int, n_splits: int = DEFAULT_SPLITS,
             models: Mapping[str, Callable[[], object]] | None = None,
             tradeable: bool = True) -> list[Score]:
    """Out-of-sample R-squared and directional hit rate, averaged over folds.

    R-squared is measured against the TRAINING mean, the only baseline knowable
    at prediction time. A model predicting a constant scores exactly zero
    against it, so anything negative is worse than that naive forecast.

    Scaling is fit inside each fold by the pipeline, never on the whole sample.
    """
    usable = frame.dropna(subset=[*features, target]).reset_index(drop=True)
    X, y = usable[features].to_numpy(), usable[target].to_numpy()
    folds = forward_folds(len(usable), n_splits, embargo)
    if not folds:
        return []

    scores = []
    for name, make in (models or default_models()).items():
        r2s, hits, n_train, n_test = [], [], [], []
        for train, test in folds:
            model = make().fit(X[train], y[train])
            predicted = model.predict(X[test])
            baseline = y[train].mean()
            total = np.sum((y[test] - baseline) ** 2)
            r2s.append(1.0 - np.sum((y[test] - predicted) ** 2) / total
                       if total > 0 else np.nan)
            moved = predicted != 0
            hits.append(np.mean(np.sign(predicted[moved]) == np.sign(y[test][moved]))
                        if moved.any() else np.nan)
            n_train.append(len(train))
            n_test.append(len(test))
        scores.append(Score(label=label, model=name, r2=float(np.nanmean(r2s)),
                            hit_rate=float(np.nanmean(hits)),
                            n_train=int(np.mean(n_train)), n_test=int(np.mean(n_test)),
                            n_folds=len(folds), tradeable=tradeable))
    return scores
