"""Locked, execution-aligned validation for the final corn/wheat model round.

The original signal benchmark learned continuous-series close-to-close returns.
This module instead builds labels from the dated LSEG contracts that a paper
trade would actually select: next shared-session open, five-session exit,
integer contract sizing, and row-specific commission/slippage.  Model and rule
selection use pre-2024 development folds only.  The already-reviewed 2024+
period is returned for diagnosis but never enters a gate or ranking decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Callable

import numpy as np
import pandas as pd

from . import backtest
from .lseg_replay import replay_frozen_signals
from .signal_eval import INNER_VALIDATION_FRACTION, WEEKLY_PERIODS
from .signals import FEATURE_SETS, FORECAST_INPUTS

HOLDOUT_YEAR = 2024
OUTER_SPLITS = 5
NEUTRAL_BUFFER = 0.0025


@dataclass(frozen=True)
class ExecutionTarget:
    name: str
    label: str
    gross_column: str
    cost_column: str
    momentum_column: str


TARGETS = {
    "corn_wheat": ExecutionTarget(
        "corn_wheat", "corn minus wheat",
        "execution_unsigned_gross_return", "potential_cost_return",
        "corn_rel_mom20",
    ),
    "raw_corn": ExecutionTarget(
        "raw_corn", "corn",
        "execution_unsigned_corn_return", "potential_corn_cost_return",
        "corn_mom20",
    ),
    "raw_wheat": ExecutionTarget(
        "raw_wheat", "wheat",
        "execution_unsigned_wheat_return", "potential_wheat_cost_return",
        "wheat_mom20",
    ),
}


@dataclass(frozen=True)
class CandidateSpec:
    name: str
    target: str
    feature_set: str
    model: str
    objective: str


# This is the complete final-round search space.  Adding another candidate is a
# new protocol version, not an innocuous rerun.
CANDIDATES = (
    CandidateSpec("spread_weather_elastic_net", "corn_wheat", "stage_weather",
                  "elastic_net", "net_return_regression"),
    CandidateSpec("spread_public_elastic_net", "corn_wheat", "all_public",
                  "elastic_net", "net_return_regression"),
    CandidateSpec("spread_public_boosting", "corn_wheat", "all_public",
                  "boosting", "net_return_regression"),
    CandidateSpec("spread_public_three_way", "corn_wheat", "all_public",
                  "logistic", "cost_aware_three_way"),
    CandidateSpec("corn_public_elastic_net", "raw_corn", "all_public",
                  "elastic_net", "net_return_regression"),
    CandidateSpec("corn_public_boosting", "raw_corn", "all_public",
                  "boosting", "net_return_regression"),
    CandidateSpec("corn_public_three_way", "raw_corn", "all_public",
                  "logistic", "cost_aware_three_way"),
    CandidateSpec("wheat_public_elastic_net", "raw_wheat", "all_public",
                  "elastic_net", "net_return_regression"),
    CandidateSpec("wheat_public_boosting", "raw_wheat", "all_public",
                  "boosting", "net_return_regression"),
    CandidateSpec("wheat_public_three_way", "raw_wheat", "all_public",
                  "logistic", "cost_aware_three_way"),
)

BASELINE_RULES = (
    "no_trade", "long_only", "short_only", "trailing_mean",
    "seasonal_mean", "momentum", "mean_reversion",
)


@dataclass(frozen=True)
class ValidationMetrics:
    observations: int
    trades: int
    hit_rate: float
    mean_net_per_trade: float
    total_net: float
    sharpe: float
    max_drawdown: float
    positive_folds: int
    folds: int
    positive_years: int
    years: int


@dataclass(frozen=True)
class ValidationResult:
    name: str
    kind: str
    target: str
    target_label: str
    feature_set: str
    model: str
    objective: str
    development: ValidationMetrics
    consumed_holdout: ValidationMetrics
    development_predictions: pd.DataFrame
    holdout_predictions: pd.DataFrame


def build_execution_frame(
    signal_frame: pd.DataFrame,
    contracts: pd.DataFrame,
    *,
    horizon: int = 5,
) -> pd.DataFrame:
    """Attach action-independent dated-contract outcomes to point-in-time inputs."""

    target_end = f"target_end_date_{horizon}"
    required = {"date", "year", target_end, *FEATURE_SETS["all_public"]}
    missing = sorted(required.difference(signal_frame.columns))
    if missing:
        raise ValueError("signal frame is missing: " + ", ".join(missing))

    frame = signal_frame.copy()
    forecast = [column for column in FORECAST_INPUTS if column in frame]
    coverage = frame.loc[:, forecast].notna().any(axis=1)
    if not coverage.any():
        raise ValueError("final validation requires CPC forecast coverage")
    frame = frame.loc[
        (frame["date"] >= frame.loc[coverage, "date"].min())
        & frame[target_end].notna()
    ].copy()
    decisions = pd.DataFrame({
        "date": frame["date"],
        "target_end_date": frame[target_end],
        "year": frame["year"],
        "actual": 0.0,
        "signal": 0,
        "fold": 0,
        "sample": np.where(frame["year"] < HOLDOUT_YEAR,
                           "development", "consumed_holdout"),
    })
    outcomes = replay_frozen_signals(decisions, contracts)
    outcome_columns = [
        "date", "entry_date", "exit_date", "execution_status",
        "execution_unsigned_gross_return", "execution_unsigned_corn_return",
        "execution_unsigned_wheat_return", "potential_cost_return",
        "potential_corn_cost_return", "potential_wheat_cost_return",
        "corn_rolled_forward", "wheat_rolled_forward",
    ]
    result = frame.merge(
        outcomes.loc[:, outcome_columns], on="date", how="left", validate="one_to_one"
    )
    outcome_required = [
        "entry_date", "exit_date", *[target.gross_column for target in TARGETS.values()],
        *[target.cost_column for target in TARGETS.values()],
    ]
    return result.dropna(subset=outcome_required).sort_values("date").reset_index(drop=True)


def _factories(model: str) -> list[tuple[str, Callable[[], object]]]:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import ElasticNet, LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if model == "elastic_net":
        return [
            (
                f"alpha={alpha:g},l1={ratio:g}",
                lambda alpha=alpha, ratio=ratio: make_pipeline(
                    SimpleImputer(strategy="median", keep_empty_features=True),
                    StandardScaler(),
                    ElasticNet(alpha=alpha, l1_ratio=ratio, max_iter=10_000,
                               random_state=0),
                ),
            )
            for alpha in (0.0001, 0.001, 0.01)
            for ratio in (0.1, 0.5)
        ]
    if model == "boosting":
        return [
            (
                f"leaves={leaves}",
                lambda leaves=leaves: make_pipeline(
                    SimpleImputer(strategy="median", keep_empty_features=True),
                    HistGradientBoostingRegressor(
                        max_leaf_nodes=leaves, max_iter=150, learning_rate=0.05,
                        l2_regularization=1.0, random_state=0,
                    ),
                ),
            )
            for leaves in (7, 15)
        ]
    if model == "logistic":
        return [
            (
                f"C={c:g}",
                lambda c=c: make_pipeline(
                    SimpleImputer(strategy="median", keep_empty_features=True),
                    StandardScaler(),
                    LogisticRegression(C=c, max_iter=2_000, class_weight="balanced",
                                       random_state=0),
                ),
            )
            for c in (0.1, 1.0, 10.0)
        ]
    raise ValueError(f"unknown final-validation model: {model!r}")


def _classes(gross: np.ndarray, costs: np.ndarray) -> np.ndarray:
    boundary = costs + NEUTRAL_BUFFER
    return np.where(gross > boundary, 1, np.where(gross < -boundary, -1, 0))


def _fit(factory: Callable[[], object], model: str, X: np.ndarray,
         gross: np.ndarray, costs: np.ndarray) -> object:
    target = _classes(gross, costs) if model == "logistic" else gross
    if model == "logistic" and np.unique(target).size < 2:
        raise ValueError("classification block contains fewer than two classes")
    return factory().fit(X, target)


def _scores(estimator: object, model: str, X: np.ndarray) -> np.ndarray:
    if model != "logistic":
        return np.asarray(estimator.predict(X), dtype="float64")
    probabilities = estimator.predict_proba(X)
    classes = estimator.classes_
    positive = probabilities[:, np.flatnonzero(classes == 1)[0]] if 1 in classes else 0.0
    negative = probabilities[:, np.flatnonzero(classes == -1)[0]] if -1 in classes else 0.0
    return np.asarray(positive - negative, dtype="float64")


def _thresholds(scores: np.ndarray, model: str, median_cost: float) -> tuple[float, ...]:
    if model == "logistic":
        return (0.0, 0.10, 0.20, 0.35, 0.50)
    finite = np.abs(scores[np.isfinite(scores)])
    if finite.size == 0:
        return (median_cost,)
    values = [median_cost, *np.quantile(finite, (0.50, 0.70, 0.85)).tolist()]
    return tuple(sorted(set(float(value) for value in values)))


def _raw_positions(scores: np.ndarray, threshold: float) -> np.ndarray:
    return np.where(scores > threshold, 1, np.where(scores < -threshold, -1, 0))


def apply_no_overlap(frame: pd.DataFrame, signals: np.ndarray) -> np.ndarray:
    """Apply the frozen one-position policy in chronological order."""

    if len(frame) != len(signals):
        raise ValueError("frame and signal lengths differ")
    output = np.asarray(signals, dtype="int64").copy()
    previous_exit: pd.Timestamp | None = None
    order = np.argsort(pd.to_datetime(frame["date"]).to_numpy())
    for index in order:
        signal = int(output[index])
        if signal == 0:
            continue
        entry = pd.Timestamp(frame.iloc[index]["entry_date"])
        exit_date = pd.Timestamp(frame.iloc[index]["exit_date"])
        if previous_exit is not None and entry <= previous_exit:
            output[index] = 0
        else:
            previous_exit = exit_date
    return output


def _net(gross: np.ndarray, costs: np.ndarray, signals: np.ndarray) -> np.ndarray:
    return signals * gross - np.abs(signals) * costs


def _sharpe(values: np.ndarray) -> float:
    numbers = np.asarray(values, dtype="float64")
    deviation = float(np.std(numbers, ddof=1)) if len(numbers) > 1 else 0.0
    return (float(np.sqrt(WEEKLY_PERIODS) * np.mean(numbers) / deviation)
            if deviation > 0 else float("nan"))


def _select(train: pd.DataFrame, *, features: tuple[str, ...],
            target: ExecutionTarget, model: str
            ) -> tuple[str, Callable[[], object], float]:
    n = len(train)
    boundary = max(backtest.MIN_TRAIN + 1, int(n * (1.0 - INNER_VALIDATION_FRACTION)))
    fit_end = boundary - 1
    if fit_end < backtest.MIN_TRAIN or n - boundary < backtest.MIN_TEST:
        raise ValueError(f"training block of {n} rows is too short for nested selection")

    X = train.loc[:, features].to_numpy(dtype="float64")
    gross = train[target.gross_column].to_numpy(dtype="float64")
    costs = train[target.cost_column].to_numpy(dtype="float64")
    best: tuple[float, float, str, Callable[[], object], float] | None = None
    for label, factory in _factories(model):
        try:
            estimator = _fit(factory, model, X[:fit_end], gross[:fit_end], costs[:fit_end])
        except ValueError:
            continue
        score = _scores(estimator, model, X[boundary:])
        validation = train.iloc[boundary:].reset_index(drop=True)
        for threshold in _thresholds(score, model, float(np.median(costs[:fit_end]))):
            raw = _raw_positions(score, threshold)
            executed = apply_no_overlap(validation, raw)
            trades = int(np.count_nonzero(executed))
            if trades < max(5, int(0.05 * len(validation))):
                continue
            values = _net(gross[boundary:], costs[boundary:], executed)
            sharpe = _sharpe(values)
            candidate = (
                sharpe if np.isfinite(sharpe) else -np.inf,
                float(values.sum()), label, factory, float(threshold),
            )
            if best is None or candidate[:2] > best[:2]:
                best = candidate
    if best is None:
        label, factory = _factories(model)[0]
        return label, factory, float("inf")
    return best[2], best[3], best[4]


def _supports_nested_selection(n: int) -> bool:
    """Whether a training block can support the locked inner split."""

    boundary = max(backtest.MIN_TRAIN + 1, int(n * (1.0 - INNER_VALIDATION_FRACTION)))
    fit_end = boundary - 1
    return fit_end >= backtest.MIN_TRAIN and n - boundary >= backtest.MIN_TEST


def _model_rows(train: pd.DataFrame, test: pd.DataFrame, *,
                features: tuple[str, ...], target: ExecutionTarget,
                model: str, fold: int) -> pd.DataFrame:
    parameter, factory, threshold = _select(
        train, features=features, target=target, model=model)
    X_train = train.loc[:, features].to_numpy(dtype="float64")
    gross_train = train[target.gross_column].to_numpy(dtype="float64")
    costs_train = train[target.cost_column].to_numpy(dtype="float64")
    estimator = _fit(factory, model, X_train, gross_train, costs_train)
    score = _scores(estimator, model, test.loc[:, features].to_numpy(dtype="float64"))
    raw = _raw_positions(score, threshold)
    executed = apply_no_overlap(test.reset_index(drop=True), raw)
    gross = test[target.gross_column].to_numpy(dtype="float64")
    costs = test[target.cost_column].to_numpy(dtype="float64")
    return pd.DataFrame({
        "date": test["date"].to_numpy(),
        "year": test["year"].to_numpy(),
        "month": pd.to_datetime(test["date"]).dt.month.to_numpy(),
        "entry_date": test["entry_date"].to_numpy(),
        "exit_date": test["exit_date"].to_numpy(),
        "gross": gross,
        "cost": costs,
        "score": score,
        "raw_signal": raw,
        "signal": executed,
        "net": _net(gross, costs, executed),
        "fold": fold,
        "parameter": parameter,
        "threshold": threshold,
        "momentum": test[target.momentum_column].to_numpy(dtype="float64"),
        "rolled_forward": (
            test["corn_rolled_forward"].astype(bool)
            | test["wheat_rolled_forward"].astype(bool)
        ).to_numpy(),
    })


def metrics(predictions: pd.DataFrame) -> ValidationMetrics:
    if predictions.empty:
        return ValidationMetrics(0, 0, float("nan"), float("nan"), 0.0,
                                 float("nan"), 0.0, 0, 0, 0, 0)
    traded = predictions["signal"].astype(int) != 0
    trades = int(traded.sum())
    values = predictions["net"].to_numpy(dtype="float64")
    equity = (1.0 + values).cumprod()
    drawdown = equity / np.maximum.accumulate(equity) - 1.0
    fold_net = predictions.groupby("fold")["net"].sum()
    year_net = predictions.groupby("year")["net"].sum()
    return ValidationMetrics(
        observations=len(predictions),
        trades=trades,
        hit_rate=(float((predictions.loc[traded, "signal"]
                         * predictions.loc[traded, "gross"] > 0).mean())
                  if trades else float("nan")),
        mean_net_per_trade=(float(predictions.loc[traded, "net"].mean())
                            if trades else float("nan")),
        total_net=float(values.sum()),
        sharpe=_sharpe(values),
        max_drawdown=float(drawdown.min()) if len(drawdown) else 0.0,
        positive_folds=int((fold_net > 0).sum()),
        folds=len(fold_net),
        positive_years=int((year_net > 0).sum()),
        years=len(year_net),
    )


def _usable(frame: pd.DataFrame, features: tuple[str, ...],
            target: ExecutionTarget) -> pd.DataFrame:
    columns = list(dict.fromkeys([
        "date", "year", "entry_date", "exit_date", "corn_rolled_forward",
        "wheat_rolled_forward", target.gross_column, target.cost_column,
        target.momentum_column, *features,
    ]))
    return frame.loc[
        frame[target.gross_column].notna() & frame[target.cost_column].notna(), columns
    ].sort_values("date").reset_index(drop=True)


def evaluate_candidate(frame: pd.DataFrame, spec: CandidateSpec) -> ValidationResult:
    target = TARGETS[spec.target]
    features = FEATURE_SETS[spec.feature_set]
    usable = _usable(frame, features, target)
    development = usable.loc[usable["year"] < HOLDOUT_YEAR].reset_index(drop=True)
    holdout = usable.loc[usable["year"] >= HOLDOUT_YEAR].reset_index(drop=True)
    rows = []
    for fold, (train_index, test_index) in enumerate(
        backtest.forward_folds(len(development), n_splits=OUTER_SPLITS, embargo=1), start=1
    ):
        try:
            rows.append(_model_rows(
                development.iloc[train_index], development.iloc[test_index],
                features=features, target=target, model=spec.model, fold=fold,
            ))
        except ValueError:
            continue
    predictions = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    holdout_predictions = pd.DataFrame()
    if len(development) >= backtest.MIN_TRAIN and not holdout.empty:
        holdout_predictions = _model_rows(
            development, holdout, features=features, target=target,
            model=spec.model, fold=OUTER_SPLITS + 1,
        )
    return ValidationResult(
        name=spec.name, kind="candidate", target=spec.target,
        target_label=target.label, feature_set=spec.feature_set,
        model=spec.model, objective=spec.objective,
        development=metrics(predictions),
        consumed_holdout=metrics(holdout_predictions),
        development_predictions=predictions,
        holdout_predictions=holdout_predictions,
    )


def _baseline_signals(rule: str, train: pd.DataFrame, test: pd.DataFrame,
                      target: ExecutionTarget) -> np.ndarray:
    if rule == "no_trade":
        return np.zeros(len(test), dtype="int64")
    if rule == "long_only":
        return np.ones(len(test), dtype="int64")
    if rule == "short_only":
        return -np.ones(len(test), dtype="int64")
    if rule == "momentum":
        return np.sign(test[target.momentum_column].fillna(0.0)).astype("int64").to_numpy()
    if rule == "mean_reversion":
        return -np.sign(test[target.momentum_column].fillna(0.0)).astype("int64").to_numpy()
    if rule == "trailing_mean":
        mean = float(train[target.gross_column].mean())
        cost = float(train[target.cost_column].median())
        signal = 1 if mean > cost else -1 if mean < -cost else 0
        return np.full(len(test), signal, dtype="int64")
    if rule == "seasonal_mean":
        overall = float(train[target.gross_column].mean())
        costs = float(train[target.cost_column].median())
        means = train.assign(month=pd.to_datetime(train["date"]).dt.month).groupby(
            "month"
        )[target.gross_column].mean()
        output = []
        for month in pd.to_datetime(test["date"]).dt.month:
            mean = float(means.get(month, overall))
            output.append(1 if mean > costs else -1 if mean < -costs else 0)
        return np.asarray(output, dtype="int64")
    raise ValueError(f"unknown baseline rule: {rule!r}")


def _baseline_rows(train: pd.DataFrame, test: pd.DataFrame, *,
                   target: ExecutionTarget, rule: str, fold: int) -> pd.DataFrame:
    raw = _baseline_signals(rule, train, test, target)
    executed = apply_no_overlap(test.reset_index(drop=True), raw)
    gross = test[target.gross_column].to_numpy(dtype="float64")
    costs = test[target.cost_column].to_numpy(dtype="float64")
    return pd.DataFrame({
        "date": test["date"].to_numpy(), "year": test["year"].to_numpy(),
        "month": pd.to_datetime(test["date"]).dt.month.to_numpy(),
        "entry_date": test["entry_date"].to_numpy(),
        "exit_date": test["exit_date"].to_numpy(),
        "gross": gross, "cost": costs, "score": np.nan,
        "raw_signal": raw, "signal": executed,
        "net": _net(gross, costs, executed), "fold": fold,
        "parameter": rule, "threshold": np.nan,
        "momentum": test[target.momentum_column].to_numpy(dtype="float64"),
        "rolled_forward": (
            test["corn_rolled_forward"].astype(bool)
            | test["wheat_rolled_forward"].astype(bool)
        ).to_numpy(),
    })


def evaluate_baseline(frame: pd.DataFrame, target_name: str, rule: str) -> ValidationResult:
    if rule not in BASELINE_RULES:
        raise ValueError(f"unknown baseline rule: {rule!r}")
    target = TARGETS[target_name]
    usable = _usable(frame, (), target)
    development = usable.loc[usable["year"] < HOLDOUT_YEAR].reset_index(drop=True)
    holdout = usable.loc[usable["year"] >= HOLDOUT_YEAR].reset_index(drop=True)
    rows = []
    for fold, (train_index, test_index) in enumerate(
        backtest.forward_folds(len(development), n_splits=OUTER_SPLITS, embargo=1), start=1
    ):
        # Score rules over the identical outer dates available to the nested
        # candidates.  Otherwise an extra early baseline fold would make total
        # returns and year stability incomparable at the gate.
        if not _supports_nested_selection(len(train_index)):
            continue
        rows.append(_baseline_rows(
            development.iloc[train_index], development.iloc[test_index],
            target=target, rule=rule, fold=fold,
        ))
    predictions = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    holdout_predictions = pd.DataFrame()
    if not holdout.empty:
        holdout_predictions = _baseline_rows(
            development, holdout, target=target, rule=rule, fold=OUTER_SPLITS + 1,
        )
    return ValidationResult(
        name=f"{target_name}_{rule}", kind="baseline", target=target_name,
        target_label=target.label, feature_set="rule", model=rule,
        objective="predeclared_rule", development=metrics(predictions),
        consumed_holdout=metrics(holdout_predictions),
        development_predictions=predictions,
        holdout_predictions=holdout_predictions,
    )


def uncertainty(predictions: pd.DataFrame, *, draws: int = 5_000,
                seed: int = 590) -> dict[str, float | int]:
    """Year-block confidence interval and circular-shift signal null."""

    if predictions.empty:
        return {
            "bootstrap_draws": draws, "bootstrap_p025": np.nan,
            "bootstrap_median": np.nan, "bootstrap_p975": np.nan,
            "bootstrap_probability_positive": np.nan,
            "permutation_draws": draws, "permutation_p_value": np.nan,
        }
    rng = np.random.default_rng(seed)
    blocks = [group["net"].to_numpy(dtype="float64")
              for _, group in predictions.groupby("year", sort=True)]
    boot = np.empty(draws)
    for draw in range(draws):
        chosen = rng.integers(0, len(blocks), size=len(blocks))
        boot[draw] = sum(float(blocks[index].sum()) for index in chosen)

    ordered = predictions.sort_values("date").reset_index(drop=True)
    null = np.empty(draws)
    groups = [index.to_numpy() for _, index in ordered.groupby("year").groups.items()]
    for draw in range(draws):
        shifted = np.zeros(len(ordered), dtype="int64")
        for indexes in groups:
            source = ordered.loc[indexes, "raw_signal"].to_numpy(dtype="int64")
            offset = int(rng.integers(0, len(source))) if len(source) else 0
            shifted[indexes] = np.roll(source, offset)
        executed = apply_no_overlap(ordered, shifted)
        null[draw] = float(_net(
            ordered["gross"].to_numpy(dtype="float64"),
            ordered["cost"].to_numpy(dtype="float64"), executed,
        ).sum())
    observed = float(ordered["net"].sum())
    return {
        "bootstrap_draws": draws,
        "bootstrap_p025": float(np.quantile(boot, 0.025)),
        "bootstrap_median": float(np.median(boot)),
        "bootstrap_p975": float(np.quantile(boot, 0.975)),
        "bootstrap_probability_positive": float((boot > 0).mean()),
        "permutation_draws": draws,
        "permutation_p_value": float((1 + np.count_nonzero(null >= observed)) / (draws + 1)),
    }


def gate(result: ValidationResult, baseline: ValidationResult,
         uncertainty_row: dict[str, float | int]) -> tuple[bool, list[str], float]:
    """Apply the locked development-only acceptance gate."""

    dev = result.development
    benchmark = baseline.development
    stress = float((
        result.development_predictions["signal"]
        * result.development_predictions["gross"]
        - result.development_predictions["signal"].abs()
        * 2.0 * result.development_predictions["cost"]
    ).sum()) if not result.development_predictions.empty else 0.0
    required_folds = ceil(0.60 * dev.folds) if dev.folds else 1
    required_years = ceil(0.60 * dev.years) if dev.years else 1
    checks = {
        "at least 30 development trades": dev.trades >= 30,
        "positive development net": dev.total_net > 0,
        "development Sharpe above 0.50": np.isfinite(dev.sharpe) and dev.sharpe > 0.50,
        "positive in at least 60% of folds": dev.positive_folds >= required_folds,
        "positive in at least 60% of years": dev.positive_years >= required_years,
        "beats best simple baseline net": dev.total_net > benchmark.total_net,
        "beats best simple baseline Sharpe": (
            np.isfinite(dev.sharpe)
            and (not np.isfinite(benchmark.sharpe) or dev.sharpe > benchmark.sharpe)
        ),
        "positive with doubled costs": stress > 0,
        "year-bootstrap probability positive at least 80%": (
            float(uncertainty_row["bootstrap_probability_positive"]) >= 0.80
        ),
        "circular-shift null p-value at most 10%": (
            float(uncertainty_row["permutation_p_value"]) <= 0.10
        ),
        "development drawdown no worse than -25%": dev.max_drawdown > -0.25,
    }
    failures = [label for label, passed in checks.items() if not passed]
    return not failures, failures, stress


def best_baseline(results: list[ValidationResult], target: str) -> ValidationResult:
    candidates = [result for result in results if result.target == target]
    if not candidates:
        raise ValueError(f"no baselines available for {target}")
    return max(
        candidates,
        key=lambda result: (
            result.development.sharpe if np.isfinite(result.development.sharpe) else -np.inf,
            result.development.total_net,
        ),
    )


def strata(predictions: pd.DataFrame, *, name: str, sample: str) -> pd.DataFrame:
    """Aggregate model behavior without emitting licensed row-level observations."""

    if predictions.empty:
        return pd.DataFrame()
    work = predictions.copy()
    work["direction"] = work["signal"].map({1: "BUY", -1: "SELL", 0: "HOLD"})
    work["season"] = pd.cut(
        work["month"], bins=[3, 5, 7, 10],
        labels=["early Apr-May", "mid Jun-Jul", "late Aug-Oct"],
    ).astype(str)
    work["market_regime"] = np.where(
        work["momentum"] > 0, "positive momentum",
        np.where(work["momentum"] < 0, "negative momentum", "flat"),
    )
    work["roll_selection"] = np.where(
        work["rolled_forward"], "rolled beyond nearest", "nearest contracts")
    dimensions = {
        "year": work["year"].astype(int).astype(str),
        "direction": work["direction"],
        "season": work["season"],
        "market_regime": work["market_regime"],
        "roll_selection": work["roll_selection"],
    }
    rows = []
    for dimension, buckets in dimensions.items():
        grouped = work.assign(bucket=buckets).groupby("bucket", sort=False)
        for bucket, group in grouped:
            traded = group["signal"] != 0
            rows.append({
                "name": name, "sample": sample, "dimension": dimension,
                "bucket": str(bucket), "observations": len(group),
                "trades": int(traded.sum()),
                "hit_rate": (float((group.loc[traded, "signal"]
                                     * group.loc[traded, "gross"] > 0).mean())
                             if traded.any() else np.nan),
                "total_net": float(group["net"].sum()),
                "mean_net_per_trade": (float(group.loc[traded, "net"].mean())
                                       if traded.any() else np.nan),
            })
    return pd.DataFrame(rows)
