"""Nested walk-forward evaluation for buy, sell, or hold research signals.

Outer folds estimate performance. Model hyperparameters and the threshold that
turns a score into a position are selected only inside each outer training block.
The final calendar holdout is never used for either choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Callable

import numpy as np
import pandas as pd

from . import backtest
from .signals import FORECAST_INPUTS, TargetSpec

MODEL_NAMES = ("ridge", "logistic", "boosting")
HOLDOUT_YEAR = 2024
OUTER_SPLITS = 5
INNER_VALIDATION_FRACTION = 0.20
WEEKLY_PERIODS = 52
DEFAULT_COST_BPS_PER_LEG = 5.0
NEUTRAL_BUFFER = 0.0025


@dataclass(frozen=True)
class StrategyMetrics:
    observations: int
    trades: int
    coverage: float
    hit_rate: float
    mean_net_per_trade: float
    total_net: float
    sharpe: float
    max_drawdown: float
    r2: float
    positive_folds: int
    folds: int
    net_excluding_2012: float
    baseline_total_net: float
    baseline_sharpe: float


@dataclass(frozen=True)
class Evaluation:
    target: str
    target_label: str
    horizon: int
    feature_set: str
    model: str
    features: tuple[str, ...]
    cost_rate: float
    development: StrategyMetrics
    holdout: StrategyMetrics
    predictions: pd.DataFrame
    holdout_predictions: pd.DataFrame

    @property
    def passes_gate(self) -> bool:
        dev = self.development
        holdout = self.holdout
        required_positive = ceil(0.60 * dev.folds) if dev.folds else 1
        return (
            dev.trades >= 30
            and np.isfinite(dev.sharpe)
            and dev.sharpe > 0.50
            and dev.positive_folds >= required_positive
            and dev.net_excluding_2012 > 0.0
            and dev.total_net > dev.baseline_total_net
            and dev.max_drawdown > -0.25
            and holdout.trades >= 3
            and holdout.total_net > 0.0
            and holdout.total_net > holdout.baseline_total_net
            and holdout.max_drawdown > -0.15
        )


def transaction_cost(target: TargetSpec, *,
                     basis_points_per_leg: float = DEFAULT_COST_BPS_PER_LEG) -> float:
    """Round-trip cost charged whenever a signal opens a position."""
    if basis_points_per_leg < 0:
        raise ValueError("basis_points_per_leg must be non-negative")
    return target.legs * basis_points_per_leg / 10_000.0


def strategy_returns(actual: np.ndarray, signals: np.ndarray, cost_rate: float) -> np.ndarray:
    """Net return of isolated horizon trades; hold observations earn zero."""
    actual = np.asarray(actual, dtype="float64")
    signals = np.asarray(signals, dtype="float64")
    return signals * actual - np.abs(signals) * cost_rate


def _positions(scores: np.ndarray, threshold: float) -> np.ndarray:
    scores = np.asarray(scores, dtype="float64")
    return np.where(scores > threshold, 1, np.where(scores < -threshold, -1, 0))


def _classes(values: np.ndarray, buffer: float) -> np.ndarray:
    return np.where(values > buffer, 1, np.where(values < -buffer, -1, 0))


def _candidate_factories(model_name: str) -> list[tuple[str, Callable[[], object]]]:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if model_name == "ridge":
        return [
            (f"alpha={alpha:g}",
             lambda alpha=alpha: make_pipeline(
                 SimpleImputer(strategy="median", keep_empty_features=True),
                 StandardScaler(), Ridge(alpha=alpha)))
            for alpha in (1.0, 10.0, 100.0)
        ]
    if model_name == "logistic":
        return [
            (f"C={c:g}",
             lambda c=c: make_pipeline(
                 SimpleImputer(strategy="median", keep_empty_features=True),
                 StandardScaler(), LogisticRegression(
                     C=c, max_iter=2000, class_weight="balanced", random_state=0)))
            for c in (0.1, 1.0, 10.0)
        ]
    if model_name == "boosting":
        return [
            (f"leaves={leaves}",
             lambda leaves=leaves: make_pipeline(
                 SimpleImputer(strategy="median", keep_empty_features=True),
                 HistGradientBoostingRegressor(
                     max_leaf_nodes=leaves, max_iter=150, learning_rate=0.05,
                     l2_regularization=1.0, random_state=0)))
            for leaves in (7, 15)
        ]
    raise ValueError(f"unknown model {model_name!r}; choose from {', '.join(MODEL_NAMES)}")


def _fit(estimator: object, model_name: str, X: np.ndarray, y: np.ndarray,
         label_buffer: float) -> object:
    labels = _classes(y, label_buffer) if model_name == "logistic" else y
    if model_name == "logistic" and np.unique(labels).size < 2:
        raise ValueError("logistic training block contains only one target class")
    return estimator.fit(X, labels)


def _score(estimator: object, model_name: str, X: np.ndarray) -> np.ndarray:
    if model_name != "logistic":
        return np.asarray(estimator.predict(X), dtype="float64")
    probabilities = estimator.predict_proba(X)
    classes = estimator.classes_
    positive = probabilities[:, np.flatnonzero(classes == 1)[0]] if 1 in classes else 0.0
    negative = probabilities[:, np.flatnonzero(classes == -1)[0]] if -1 in classes else 0.0
    return np.asarray(positive - negative, dtype="float64")


def _thresholds(scores: np.ndarray, model_name: str, cost_rate: float) -> tuple[float, ...]:
    if model_name == "logistic":
        return (0.0, 0.10, 0.20, 0.35, 0.50)
    absolute = np.abs(scores[np.isfinite(scores)])
    if absolute.size == 0:
        return (cost_rate,)
    values = [cost_rate, *np.quantile(absolute, (0.50, 0.70, 0.85)).tolist()]
    return tuple(sorted(set(float(value) for value in values)))


def _sharpe(returns: np.ndarray) -> float:
    values = np.asarray(returns, dtype="float64")
    deviation = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    return float(np.sqrt(WEEKLY_PERIODS) * np.mean(values) / deviation) if deviation > 0 else float("nan")


def _choose(frame: pd.DataFrame, features: tuple[str, ...], target_column: str,
            model_name: str, cost_rate: float) -> tuple[str, Callable[[], object], float]:
    """Choose hyperparameters and action threshold on the tail of training only."""
    n = len(frame)
    boundary = max(backtest.MIN_TRAIN + 1, int(n * (1.0 - INNER_VALIDATION_FRACTION)))
    fit_end = boundary - 1  # one weekly row embargo
    if fit_end < backtest.MIN_TRAIN or n - boundary < backtest.MIN_TEST:
        raise ValueError(f"training block of {n} rows is too short for nested validation")

    X = frame.loc[:, features].to_numpy(dtype="float64")
    y = frame[target_column].to_numpy(dtype="float64")
    X_fit, y_fit = X[:fit_end], y[:fit_end]
    X_valid, y_valid = X[boundary:], y[boundary:]
    label_buffer = cost_rate + NEUTRAL_BUFFER
    minimum_trades = max(10, int(0.05 * len(y_valid)))

    best: tuple[float, float, str, Callable[[], object], float] | None = None
    for label, factory in _candidate_factories(model_name):
        try:
            estimator = _fit(factory(), model_name, X_fit, y_fit, label_buffer)
        except ValueError:
            continue
        scores = _score(estimator, model_name, X_valid)
        for threshold in _thresholds(scores, model_name, cost_rate):
            positions = _positions(scores, threshold)
            trades = int(np.count_nonzero(positions))
            if trades < minimum_trades:
                continue
            net = strategy_returns(y_valid, positions, cost_rate)
            sharpe = _sharpe(net)
            objective = sharpe if np.isfinite(sharpe) else -np.inf
            candidate = (objective, float(np.sum(net)), label, factory, threshold)
            if best is None or candidate[:2] > best[:2]:
                best = candidate

    if best is None:
        # A no-trade threshold is safer than relaxing the minimum-trade guard to
        # select a result from one lucky validation event.
        label, factory = _candidate_factories(model_name)[0]
        return label, factory, float("inf")
    return best[2], best[3], best[4]


def _prediction_rows(train: pd.DataFrame, test: pd.DataFrame, *,
                     features: tuple[str, ...], target_column: str,
                     model_name: str, cost_rate: float, fold: int) -> pd.DataFrame:
    parameter, factory, threshold = _choose(
        train, features, target_column, model_name, cost_rate)
    X_train = train.loc[:, features].to_numpy(dtype="float64")
    y_train = train[target_column].to_numpy(dtype="float64")
    X_test = test.loc[:, features].to_numpy(dtype="float64")
    label_buffer = cost_rate + NEUTRAL_BUFFER
    estimator = _fit(factory(), model_name, X_train, y_train, label_buffer)
    scores = _score(estimator, model_name, X_test)
    positions = _positions(scores, threshold)
    actual = test[target_column].to_numpy(dtype="float64")
    return pd.DataFrame({
        "date": test["date"].to_numpy(),
        "year": test["year"].to_numpy(),
        "actual": actual,
        "score": scores,
        "signal": positions,
        "net": strategy_returns(actual, positions, cost_rate),
        "baseline": float(np.mean(y_train)),
        "baseline_signal": (
            1 if float(np.mean(y_train)) > cost_rate
            else -1 if float(np.mean(y_train)) < -cost_rate
            else 0),
        "fold": fold,
        "parameter": parameter,
        "threshold": threshold,
        "cost_rate": cost_rate,
    })


def metrics(predictions: pd.DataFrame, *, regression: bool) -> StrategyMetrics:
    if predictions.empty:
        return StrategyMetrics(0, 0, 0.0, float("nan"), float("nan"), 0.0,
                               float("nan"), 0.0, float("nan"), 0, 0, 0.0,
                               0.0, float("nan"))
    traded = predictions["signal"] != 0
    trades = int(traded.sum())
    hit = float((np.sign(predictions.loc[traded, "actual"])
                 == predictions.loc[traded, "signal"]).mean()) if trades else float("nan")
    mean_trade = float(predictions.loc[traded, "net"].mean()) if trades else float("nan")
    equity = (1.0 + predictions["net"]).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    folds = int(predictions["fold"].nunique())
    positive = int((predictions.groupby("fold")["net"].sum() > 0.0).sum())
    r2 = float("nan")
    if regression:
        denominator = np.sum((predictions["actual"] - predictions["baseline"]) ** 2)
        if denominator > 0:
            r2 = float(1.0 - np.sum((predictions["actual"] - predictions["score"]) ** 2)
                       / denominator)
    baseline_returns = strategy_returns(
        predictions["actual"].to_numpy(),
        predictions["baseline_signal"].to_numpy(),
        float(predictions["cost_rate"].iloc[0]))
    return StrategyMetrics(
        observations=len(predictions), trades=trades, coverage=trades / len(predictions),
        hit_rate=hit, mean_net_per_trade=mean_trade,
        total_net=float(predictions["net"].sum()),
        sharpe=_sharpe(predictions["net"].to_numpy()),
        max_drawdown=float(drawdown.min()), r2=r2,
        positive_folds=positive, folds=folds,
        net_excluding_2012=float(predictions.loc[predictions["year"] != 2012, "net"].sum()),
        baseline_total_net=float(np.sum(baseline_returns)),
        baseline_sharpe=_sharpe(baseline_returns),
    )


def _eligible_rows(frame: pd.DataFrame, *, features: tuple[str, ...],
                   target_column: str) -> pd.DataFrame:
    """Select dated targets whose requested data family has begun coverage."""
    mask = frame[target_column].notna()
    forecast_features = tuple(column for column in FORECAST_INPUTS
                              if column in features and column in frame)
    if forecast_features:
        # Median imputation remains appropriate for occasional missing cells,
        # but rows before CPC coverage are not forecast-enhanced observations.
        has_forecast = frame.loc[:, forecast_features].notna().any(axis=1)
        if has_forecast.any():
            first_forecast_date = frame.loc[has_forecast, "date"].min()
            mask &= frame["date"] >= first_forecast_date
        else:
            mask &= False
    required = ["date", "year", target_column, *features]
    return frame.loc[mask, required].sort_values("date", ignore_index=True)


def evaluate(frame: pd.DataFrame, *, features: tuple[str, ...], feature_set: str,
             target: TargetSpec, horizon: int, model_name: str,
             cost_bps_per_leg: float = DEFAULT_COST_BPS_PER_LEG,
             holdout_year: int = HOLDOUT_YEAR,
             outer_splits: int = OUTER_SPLITS) -> Evaluation:
    """Nested development folds followed by one fixed calendar holdout."""
    if model_name not in MODEL_NAMES:
        raise ValueError(f"unknown model {model_name!r}")
    target_column = target.column(horizon)
    required = ["date", "year", target_column, *features]
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"signal frame is missing columns: {', '.join(missing)}")

    usable = _eligible_rows(frame, features=features, target_column=target_column)
    development = usable.loc[usable["year"] < holdout_year].reset_index(drop=True)
    holdout = usable.loc[usable["year"] >= holdout_year].reset_index(drop=True)
    cost_rate = transaction_cost(target, basis_points_per_leg=cost_bps_per_leg)

    rows = []
    for fold, (train_index, test_index) in enumerate(
            backtest.forward_folds(len(development), n_splits=outer_splits, embargo=1), start=1):
        try:
            rows.append(_prediction_rows(
                development.iloc[train_index], development.iloc[test_index],
                features=features, target_column=target_column, model_name=model_name,
                cost_rate=cost_rate, fold=fold))
        except ValueError:
            # The earliest outer fold can be large enough to score but too small
            # to split again for nested tuning, especially for CPC's 2012 start.
            # Drop it rather than tune on its own test block.
            continue
    predictions = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()

    holdout_predictions = pd.DataFrame()
    if not holdout.empty and len(development) >= backtest.MIN_TRAIN:
        holdout_predictions = _prediction_rows(
            development, holdout, features=features, target_column=target_column,
            model_name=model_name, cost_rate=cost_rate, fold=outer_splits + 1)

    regression = model_name != "logistic"
    return Evaluation(
        target=target.name, target_label=target.label, horizon=horizon,
        feature_set=feature_set, model=model_name, features=features,
        cost_rate=cost_rate,
        development=metrics(predictions, regression=regression),
        holdout=metrics(holdout_predictions, regression=regression),
        predictions=predictions, holdout_predictions=holdout_predictions,
    )
