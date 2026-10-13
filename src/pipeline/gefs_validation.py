"""Locked, development-only validation for operational GEFS corn revisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .gefs_model import ABSOLUTE_FEATURES, MARKET_FEATURES, REVISION_FEATURES

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROTOCOL = ROOT / "config" / "gefs_corn_validation.yaml"

FEATURE_SETS = {
    "market_only": MARKET_FEATURES,
    "absolute_plus_market": (*MARKET_FEATURES, *ABSOLUTE_FEATURES),
    "revision_plus_market": (*MARKET_FEATURES, *REVISION_FEATURES),
    "all_gefs_plus_market": (
        *MARKET_FEATURES, *ABSOLUTE_FEATURES, *REVISION_FEATURES,
    ),
}
MODELS = ("ridge", "logistic", "shallow_boosting")
OUTER_TEST_YEARS = (2023, 2024, 2025)
SELECTION_YEARS = (2023, 2024)
CONFIRMATION_YEAR = 2025
UNTOUCHED_HOLDOUT_YEAR = 2026
TARGET = "target_corn_gross_return"
COST = "target_corn_cost_return"


class GefsValidationError(ValueError):
    """The model frame, saved protocol, or validation boundary is inconsistent."""


@dataclass(frozen=True)
class Metrics:
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
    feature_set: str
    model: str
    metrics: Metrics
    predictions: pd.DataFrame


@dataclass(frozen=True)
class ValidationBundle:
    results: pd.DataFrame
    headline: str
    headline_predictions: pd.DataFrame
    simple_rule: Metrics
    placebo_forward: Metrics
    placebo_backward: Metrics
    double_cost_total_net: float
    year_block_probability_positive: float
    passes_gate: bool
    gate_failures: tuple[str, ...]


@dataclass(frozen=True)
class HoldoutBundle:
    headline: str
    headline_metrics: Metrics
    market_metrics: Metrics
    simple_rule_metrics: Metrics
    predictions: pd.DataFrame
    parameter: str
    double_cost_total_net: float
    passes_assessment: bool
    assessment_failures: tuple[str, ...]


def load_protocol(path: str | Path = DEFAULT_PROTOCOL) -> dict:
    try:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GefsValidationError(f"cannot load GEFS validation protocol: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise GefsValidationError("GEFS protocol must be a mapping with schema_version 1")
    if tuple(payload["information_boundary"]["outer_test_years"]) != OUTER_TEST_YEARS:
        raise GefsValidationError("implemented outer years differ from the locked protocol")
    if payload["information_boundary"]["untouched_holdout_year"] != UNTOUCHED_HOLDOUT_YEAR:
        raise GefsValidationError("implemented holdout differs from the locked protocol")
    if tuple(payload["information_boundary"]["candidate_selection_outer_years"]) != SELECTION_YEARS:
        raise GefsValidationError("implemented selection years differ from the locked protocol")
    if payload["information_boundary"]["final_development_confirmation_year"] != CONFIRMATION_YEAR:
        raise GefsValidationError("implemented confirmation year differs from the locked protocol")
    if tuple(payload["feature_sets"]) != tuple(FEATURE_SETS):
        raise GefsValidationError("implemented feature sets differ from the locked protocol")
    if tuple(payload["models"]) != MODELS:
        raise GefsValidationError("implemented models differ from the locked protocol")
    return payload


def outer_year_folds(frame: pd.DataFrame) -> tuple[tuple[pd.Index, pd.Index], ...]:
    """Expanding season folds; 2026 is never returned by this function."""
    if "year" not in frame:
        raise GefsValidationError("GEFS model frame has no year column")
    years = pd.to_numeric(frame["year"], errors="coerce")
    folds = []
    for test_year in OUTER_TEST_YEARS:
        train = frame.index[(years >= 2021) & (years < test_year)]
        test = frame.index[years == test_year]
        if len(train) and len(test):
            folds.append((train, test))
    return tuple(folds)


def apply_no_overlap(frame: pd.DataFrame, signals: np.ndarray) -> np.ndarray:
    """Suppress a signal whose entry is not after the last executed exit."""
    output = np.asarray(signals, dtype="int64").copy()
    previous_exit: pd.Timestamp | None = None
    for index in range(len(frame)):
        if output[index] == 0:
            continue
        entry = pd.Timestamp(frame.iloc[index]["entry_date"])
        if previous_exit is not None and entry <= previous_exit:
            output[index] = 0
            continue
        previous_exit = pd.Timestamp(frame.iloc[index]["exit_date"])
    return output


def _validate_frame(frame: pd.DataFrame, *, include_holdout: bool = False) -> pd.DataFrame:
    required = {
        "date", "year", "entry_date", "exit_date", TARGET, COST,
        *FEATURE_SETS["all_gefs_plus_market"],
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise GefsValidationError(f"GEFS model frame is missing: {', '.join(missing)}")
    clean = frame.copy()
    for column in ("date", "entry_date", "exit_date"):
        clean[column] = pd.to_datetime(clean[column], errors="coerce")
    clean["year"] = pd.to_numeric(clean["year"], errors="coerce")
    clean[TARGET] = pd.to_numeric(clean[TARGET], errors="coerce")
    clean[COST] = pd.to_numeric(clean[COST], errors="coerce")
    clean = clean.dropna(subset=["date", "year", "entry_date", "exit_date", TARGET, COST])
    clean = clean.sort_values("date").reset_index(drop=True)
    if clean.duplicated("entry_date").any():
        raise GefsValidationError("GEFS model frame has duplicate entry sessions")
    if (clean["entry_date"] <= clean["date"]).any():
        raise GefsValidationError("GEFS entries must follow their model dates")
    if (clean["exit_date"] <= clean["entry_date"]).any():
        raise GefsValidationError("GEFS exits must follow their entries")
    if (clean[COST] <= 0).any():
        raise GefsValidationError("GEFS execution costs must be positive")
    if not include_holdout:
        clean = clean[clean["year"] < UNTOUCHED_HOLDOUT_YEAR].reset_index(drop=True)
    missing_years = sorted(set(OUTER_TEST_YEARS) - set(clean["year"].astype(int)))
    if missing_years:
        raise GefsValidationError(
            "development frame is incomplete; missing locked test year(s): "
            + ", ".join(map(str, missing_years))
        )
    if include_holdout and UNTOUCHED_HOLDOUT_YEAR not in set(clean["year"].astype(int)):
        raise GefsValidationError(
            f"model frame has no sealed {UNTOUCHED_HOLDOUT_YEAR} holdout rows"
        )
    return clean


def _parameter_grid(protocol: dict, model: str) -> list[dict[str, float | int]]:
    values = protocol["models"][model]
    if model == "ridge":
        return [{"alpha": float(alpha)} for alpha in values["alpha"]]
    if model == "logistic":
        return [
            {"C": float(c), "minimum_probability": float(probability)}
            for c, probability in product(values["C"], values["minimum_probability"])
        ]
    if model == "shallow_boosting":
        return [
            {
                "learning_rate": float(rate),
                "max_leaf_nodes": int(leaves),
                "max_iter": int(iterations),
            }
            for rate, leaves, iterations in product(
                values["learning_rate"], values["max_leaf_nodes"], values["max_iter"]
            )
        ]
    raise GefsValidationError(f"unknown GEFS model {model!r}")


def _estimator(model: str, params: dict[str, float | int]):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if model == "ridge":
        return make_pipeline(
            SimpleImputer(strategy="median", keep_empty_features=True),
            StandardScaler(), Ridge(alpha=float(params["alpha"])),
        )
    if model == "logistic":
        return make_pipeline(
            SimpleImputer(strategy="median", keep_empty_features=True),
            StandardScaler(),
            LogisticRegression(C=float(params["C"]), max_iter=2_000, random_state=590),
        )
    if model == "shallow_boosting":
        return make_pipeline(
            SimpleImputer(strategy="median", keep_empty_features=True),
            HistGradientBoostingRegressor(
                learning_rate=float(params["learning_rate"]),
                max_leaf_nodes=int(params["max_leaf_nodes"]),
                max_iter=int(params["max_iter"]),
                random_state=590,
            ),
        )
    raise GefsValidationError(f"unknown GEFS model {model!r}")


def _fit(estimator, model: str, train: pd.DataFrame, features: tuple[str, ...]):
    target = (train[TARGET] > 0).astype(int) if model == "logistic" else train[TARGET]
    if model == "logistic" and target.nunique() < 2:
        raise GefsValidationError("logistic training block has only one return direction")
    return estimator.fit(train.loc[:, features], target)


def _scores(estimator, model: str, frame: pd.DataFrame,
            features: tuple[str, ...]) -> np.ndarray:
    if model == "logistic":
        classes = estimator.classes_
        positive = int(np.flatnonzero(classes == 1)[0])
        return estimator.predict_proba(frame.loc[:, features])[:, positive]
    return estimator.predict(frame.loc[:, features])


def _raw_signals(scores: np.ndarray, model: str,
                 params: dict[str, float | int], cost_threshold: float) -> np.ndarray:
    if model == "logistic":
        probability = float(params["minimum_probability"])
        return np.where(scores >= probability, 1,
                        np.where(scores <= 1.0 - probability, -1, 0)).astype("int64")
    return np.where(scores > cost_threshold, 1,
                    np.where(scores < -cost_threshold, -1, 0)).astype("int64")


def _net(gross: np.ndarray, costs: np.ndarray, signals: np.ndarray, *,
         cost_multiplier: float = 1.0) -> np.ndarray:
    return signals * gross - np.abs(signals) * costs * cost_multiplier


def _model_rows(train: pd.DataFrame, test: pd.DataFrame, *,
                features: tuple[str, ...], model: str,
                params: dict[str, float | int], fold: int) -> pd.DataFrame:
    estimator = _fit(_estimator(model, params), model, train, features)
    scores = _scores(estimator, model, test, features)
    threshold = float(train[COST].median())
    raw = _raw_signals(scores, model, params, threshold)
    signals = apply_no_overlap(test.reset_index(drop=True), raw)
    gross = test[TARGET].to_numpy(dtype="float64")
    costs = test[COST].to_numpy(dtype="float64")
    return pd.DataFrame({
        "date": test["date"].to_numpy(),
        "year": test["year"].astype(int).to_numpy(),
        "entry_date": test["entry_date"].to_numpy(),
        "exit_date": test["exit_date"].to_numpy(),
        "gross": gross,
        "cost": costs,
        "score": scores,
        "raw_signal": raw,
        "signal": signals,
        "net": _net(gross, costs, signals),
        "fold": fold,
        "parameter": yaml.safe_dump(params, default_flow_style=True).strip(),
    })


def _sharpe(values: np.ndarray) -> float:
    values = np.asarray(values, dtype="float64")
    if len(values) < 2:
        return float("nan")
    deviation = float(np.std(values, ddof=1))
    return float(np.sqrt(52) * np.mean(values) / deviation) if deviation > 0 else float("nan")


def metrics(predictions: pd.DataFrame) -> Metrics:
    if predictions.empty:
        return Metrics(0, 0, np.nan, np.nan, 0.0, np.nan, 0.0, 0, 0, 0, 0)
    traded = predictions["signal"].astype(int) != 0
    trades = int(traded.sum())
    trade_net = predictions.loc[traded, "net"].to_numpy(dtype="float64")
    equity = (1.0 + predictions["net"].to_numpy(dtype="float64")).cumprod()
    drawdown = equity / np.maximum.accumulate(equity) - 1.0
    fold_net = predictions.groupby("fold")["net"].sum()
    year_net = predictions.groupby("year")["net"].sum()
    return Metrics(
        observations=len(predictions),
        trades=trades,
        hit_rate=(float((predictions.loc[traded, "signal"]
                         * predictions.loc[traded, "gross"] > 0).mean())
                  if trades else np.nan),
        mean_net_per_trade=float(np.mean(trade_net)) if trades else np.nan,
        total_net=float(predictions["net"].sum()),
        sharpe=_sharpe(trade_net),
        max_drawdown=float(drawdown.min()) if len(drawdown) else 0.0,
        positive_folds=int((fold_net > 0).sum()),
        folds=len(fold_net),
        positive_years=int((year_net > 0).sum()),
        years=len(year_net),
    )


def _select_params(train: pd.DataFrame, *, features: tuple[str, ...], model: str,
                   protocol: dict) -> dict[str, float | int]:
    fraction = float(protocol["information_boundary"]["inner_validation_fraction"])
    boundary = max(30, int(len(train) * (1.0 - fraction)))
    if boundary >= len(train) - 10:
        return _parameter_grid(protocol, model)[0]
    validation = train.iloc[boundary:].reset_index(drop=True)
    first_entry = pd.Timestamp(validation.iloc[0]["entry_date"])
    fit = train.iloc[:boundary]
    fit = fit[pd.to_datetime(fit["exit_date"]) < first_entry].reset_index(drop=True)
    if len(fit) < 30:
        return _parameter_grid(protocol, model)[0]
    best: tuple[float, float, dict[str, float | int]] | None = None
    for params in _parameter_grid(protocol, model):
        try:
            rows = _model_rows(
                fit, validation, features=features, model=model, params=params, fold=0
            )
        except (GefsValidationError, ValueError):
            continue
        score = metrics(rows)
        ranking = (
            score.sharpe if np.isfinite(score.sharpe) else -np.inf,
            score.total_net,
            params,
        )
        if best is None or ranking[:2] > best[:2]:
            best = ranking
    return best[2] if best is not None else _parameter_grid(protocol, model)[0]


def evaluate_candidate(frame: pd.DataFrame, *, feature_set: str, model: str,
                       protocol: dict) -> ValidationResult:
    features = FEATURE_SETS[feature_set]
    rows = []
    for fold, (train_index, test_index) in enumerate(outer_year_folds(frame), start=1):
        train = frame.loc[train_index].reset_index(drop=True)
        test = frame.loc[test_index].reset_index(drop=True)
        params = _select_params(train, features=features, model=model, protocol=protocol)
        rows.append(_model_rows(
            train, test, features=features, model=model, params=params, fold=fold
        ))
    predictions = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return ValidationResult(feature_set, model, metrics(predictions), predictions)


def evaluate_simple_rule(frame: pd.DataFrame) -> ValidationResult:
    rows = []
    for fold, (_, test_index) in enumerate(outer_year_folds(frame), start=1):
        test = frame.loc[test_index].reset_index(drop=True)
        rows.append(_simple_rule_rows(test, fold=fold))
    predictions = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return ValidationResult("revision_rule", "fixed_rule", metrics(predictions), predictions)


def _simple_rule_rows(test: pd.DataFrame, *, fold: int) -> pd.DataFrame:
    score = (
        test["rev_d1_3_heat_dd29_sum"].to_numpy(dtype="float64")
        - 0.1 * test["rev_d1_3_precip_mm_sum"].to_numpy(dtype="float64")
    )
    raw = np.sign(score).astype("int64")
    signals = apply_no_overlap(test.reset_index(drop=True), raw)
    gross = test[TARGET].to_numpy(dtype="float64")
    costs = test[COST].to_numpy(dtype="float64")
    return pd.DataFrame({
        "date": test["date"].to_numpy(), "year": test["year"].astype(int).to_numpy(),
        "entry_date": test["entry_date"].to_numpy(),
        "exit_date": test["exit_date"].to_numpy(),
        "gross": gross, "cost": costs, "score": score,
        "raw_signal": raw, "signal": signals,
        "net": _net(gross, costs, signals), "fold": fold,
        "parameter": "fixed_heat_minus_rain",
    })


def shifted_revisions(frame: pd.DataFrame, periods: int) -> pd.DataFrame:
    """Move all revision values together within calendar year for a placebo."""
    shifted = frame.copy()
    shifted.loc[:, REVISION_FEATURES] = (
        shifted.groupby("year", sort=False)[list(REVISION_FEATURES)].shift(periods)
    )
    return shifted


def _year_block_probability_positive(predictions: pd.DataFrame, *,
                                     draws: int, seed: int) -> float:
    year_totals = predictions.groupby("year")["net"].sum().to_numpy(dtype="float64")
    if not len(year_totals):
        return np.nan
    rng = np.random.default_rng(seed)
    samples = rng.choice(year_totals, size=(draws, len(year_totals)), replace=True)
    return float((samples.sum(axis=1) > 0).mean())


def _result_row(result: ValidationResult) -> dict[str, Any]:
    return {
        "candidate": f"{result.feature_set}_{result.model}",
        "feature_set": result.feature_set,
        "model": result.model,
        "features": len(FEATURE_SETS.get(result.feature_set, ())),
        **asdict(result.metrics),
    }


def evaluate_development(frame: pd.DataFrame, *, protocol: dict | None = None
                         ) -> ValidationBundle:
    """Run the locked search without returning or scoring any 2026 observation."""
    protocol = protocol or load_protocol()
    development = _validate_frame(frame)
    candidates = [
        evaluate_candidate(
            development, feature_set=feature_set, model=model, protocol=protocol
        )
        for feature_set in FEATURE_SETS
        for model in MODELS
    ]
    results = pd.DataFrame([_result_row(result) for result in candidates])
    weather = [result for result in candidates if result.feature_set != "market_only"]
    def selection_rank(result: ValidationResult) -> tuple[float, float]:
        selected = result.predictions[result.predictions["year"].isin(SELECTION_YEARS)]
        score = metrics(selected)
        return (
            score.sharpe if np.isfinite(score.sharpe) else -np.inf,
            score.total_net,
        )

    headline_result = max(
        weather,
        key=selection_rank,
    )
    headline = f"{headline_result.feature_set}_{headline_result.model}"
    market = next(
        result for result in candidates
        if result.feature_set == "market_only" and result.model == headline_result.model
    )
    rule = evaluate_simple_rule(development)
    forward = evaluate_candidate(
        shifted_revisions(development, 1),
        feature_set=headline_result.feature_set,
        model=headline_result.model,
        protocol=protocol,
    )
    backward = evaluate_candidate(
        shifted_revisions(development, -1),
        feature_set=headline_result.feature_set,
        model=headline_result.model,
        protocol=protocol,
    )
    predictions = headline_result.predictions
    double_cost_total = float(_net(
        predictions["gross"].to_numpy(dtype="float64"),
        predictions["cost"].to_numpy(dtype="float64"),
        predictions["signal"].to_numpy(dtype="int64"),
        cost_multiplier=2.0,
    ).sum())
    uncertainty = protocol["uncertainty"]
    probability = _year_block_probability_positive(
        predictions, draws=int(uncertainty["draws"]), seed=int(uncertainty["seed"])
    )
    gate = protocol["acceptance_gate"]
    score = headline_result.metrics
    checks = {
        f"at least {gate['minimum_development_trades']} development trades": (
            score.trades >= int(gate["minimum_development_trades"])
        ),
        "positive development net": score.total_net > 0,
        "positive at doubled costs": double_cost_total > 0,
        "improves on same-model market-only net": score.total_net > market.metrics.total_net,
        "improves on same-model market-only Sharpe": (
            np.isfinite(score.sharpe)
            and (not np.isfinite(market.metrics.sharpe) or score.sharpe > market.metrics.sharpe)
        ),
        "positive in enough outer folds": (
            score.positive_folds >= int(gate["minimum_positive_outer_folds"])
        ),
        "positive in enough calendar years": (
            score.positive_years >= int(gate["minimum_positive_calendar_years"])
        ),
        "year-block probability positive meets threshold": (
            probability >= float(gate["minimum_year_block_probability_positive"])
        ),
        "beats fixed revision rule net": score.total_net > rule.metrics.total_net,
        "beats forward-shift placebo net": score.total_net > forward.metrics.total_net,
        "beats backward-shift placebo net": score.total_net > backward.metrics.total_net,
    }
    failures = tuple(label for label, passed in checks.items() if not passed)
    return ValidationBundle(
        results=results,
        headline=headline,
        headline_predictions=predictions,
        simple_rule=rule.metrics,
        placebo_forward=forward.metrics,
        placebo_backward=backward.metrics,
        double_cost_total_net=double_cost_total,
        year_block_probability_positive=probability,
        passes_gate=not failures,
        gate_failures=failures,
    )


def evaluate_holdout(frame: pd.DataFrame, *, headline: str,
                     protocol: dict | None = None) -> HoldoutBundle:
    """Score the frozen headline on 2026 exactly once, with no reselection."""
    protocol = protocol or load_protocol()
    full = _validate_frame(frame, include_holdout=True)
    matches = [model for model in MODELS if headline.endswith(f"_{model}")]
    if len(matches) != 1:
        raise GefsValidationError(f"cannot parse frozen headline {headline!r}")
    model = matches[0]
    feature_set = headline[:-(len(model) + 1)]
    if feature_set not in FEATURE_SETS or feature_set == "market_only":
        raise GefsValidationError(f"invalid frozen GEFS feature set {feature_set!r}")

    train = full[full["year"] < UNTOUCHED_HOLDOUT_YEAR].reset_index(drop=True)
    test = full[full["year"] == UNTOUCHED_HOLDOUT_YEAR].reset_index(drop=True)
    params = _select_params(
        train, features=FEATURE_SETS[feature_set], model=model, protocol=protocol
    )
    predictions = _model_rows(
        train, test, features=FEATURE_SETS[feature_set], model=model,
        params=params, fold=len(OUTER_TEST_YEARS) + 1,
    )
    market_params = _select_params(
        train, features=FEATURE_SETS["market_only"], model=model, protocol=protocol
    )
    market_predictions = _model_rows(
        train, test, features=FEATURE_SETS["market_only"], model=model,
        params=market_params, fold=len(OUTER_TEST_YEARS) + 1,
    )
    rule_predictions = _simple_rule_rows(test, fold=len(OUTER_TEST_YEARS) + 1)
    headline_metrics = metrics(predictions)
    market_metrics = metrics(market_predictions)
    rule_metrics = metrics(rule_predictions)
    double_cost_total = float(_net(
        predictions["gross"].to_numpy(dtype="float64"),
        predictions["cost"].to_numpy(dtype="float64"),
        predictions["signal"].to_numpy(dtype="int64"),
        cost_multiplier=2.0,
    ).sum())
    gate = protocol["holdout_assessment"]
    checks = {
        f"at least {gate['minimum_trades']} holdout trades": (
            headline_metrics.trades >= int(gate["minimum_trades"])
        ),
        "positive holdout net": headline_metrics.total_net > 0,
        "positive holdout net at doubled costs": double_cost_total > 0,
        "improves on market-only holdout net": (
            headline_metrics.total_net > market_metrics.total_net
        ),
        "improves on market-only holdout Sharpe": (
            np.isfinite(headline_metrics.sharpe)
            and (not np.isfinite(market_metrics.sharpe)
                 or headline_metrics.sharpe > market_metrics.sharpe)
        ),
        "beats fixed revision rule holdout net": (
            headline_metrics.total_net > rule_metrics.total_net
        ),
    }
    failures = tuple(label for label, passed in checks.items() if not passed)
    return HoldoutBundle(
        headline=headline,
        headline_metrics=headline_metrics,
        market_metrics=market_metrics,
        simple_rule_metrics=rule_metrics,
        predictions=predictions,
        parameter=predictions["parameter"].iloc[0],
        double_cost_total_net=double_cost_total,
        passes_assessment=not failures,
        assessment_failures=failures,
    )
