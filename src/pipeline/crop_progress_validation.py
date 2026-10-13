"""Locked validation for scheduled USDA corn progress and condition releases."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .crop_progress_model import CONDITION_FEATURES, PROGRESS_FEATURES
from .gefs_model import MARKET_FEATURES
from .gefs_validation import (
    MODELS,
    HoldoutBundle,
    ValidationBundle,
    ValidationResult,
    _model_rows,
    _net,
    _select_params,
    _year_block_probability_positive,
    apply_no_overlap,
    metrics,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROTOCOL = ROOT / "config" / "nass_corn_progress_validation.yaml"
FEATURE_SETS = {
    "market_only": MARKET_FEATURES,
    "condition_plus_market": (*MARKET_FEATURES, *CONDITION_FEATURES),
    "progress_plus_market": (*MARKET_FEATURES, *PROGRESS_FEATURES),
    "all_usda_plus_market": (
        *MARKET_FEATURES, *CONDITION_FEATURES, *PROGRESS_FEATURES,
    ),
}
USDA_FEATURES = (*CONDITION_FEATURES, *PROGRESS_FEATURES)
OUTER_TEST_YEARS = tuple(range(2017, 2026))
SELECTION_YEARS = tuple(range(2017, 2024))
CONFIRMATION_YEARS = (2024, 2025)
UNTOUCHED_HOLDOUT_YEAR = 2026
TARGET = "target_corn_gross_return"
COST = "target_corn_cost_return"


class CropProgressValidationError(ValueError):
    """The model frame, protocol, or sealed information boundary is inconsistent."""


def load_protocol(path: str | Path = DEFAULT_PROTOCOL) -> dict:
    try:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CropProgressValidationError(
            f"cannot load Crop Progress validation protocol: {exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise CropProgressValidationError("protocol must have schema_version 1")
    boundary = payload["information_boundary"]
    checks = {
        "outer years": tuple(boundary["outer_test_years"]) == OUTER_TEST_YEARS,
        "selection years": (
            tuple(boundary["candidate_selection_outer_years"]) == SELECTION_YEARS
        ),
        "confirmation years": (
            tuple(boundary["final_development_confirmation_years"])
            == CONFIRMATION_YEARS
        ),
        "holdout year": (
            boundary["untouched_model_specific_holdout_year"]
            == UNTOUCHED_HOLDOUT_YEAR
        ),
        "feature sets": tuple(payload["feature_sets"]) == tuple(FEATURE_SETS),
        "models": tuple(payload["models"]) == MODELS,
    }
    failures = [label for label, passed in checks.items() if not passed]
    if failures:
        raise CropProgressValidationError(
            "implementation differs from locked protocol: " + ", ".join(failures)
        )
    return payload


def outer_year_folds(frame: pd.DataFrame) -> tuple[tuple[pd.Index, pd.Index], ...]:
    if "year" not in frame:
        raise CropProgressValidationError("model frame has no year column")
    years = pd.to_numeric(frame["year"], errors="coerce")
    folds = []
    for test_year in OUTER_TEST_YEARS:
        train = frame.index[(years >= 2012) & (years < test_year)]
        test = frame.index[years == test_year]
        if len(train) and len(test):
            folds.append((train, test))
    return tuple(folds)


def _validate_frame(frame: pd.DataFrame, *, include_holdout: bool = False) -> pd.DataFrame:
    required = {
        "date", "year", "entry_date", "exit_date", TARGET, COST,
        *FEATURE_SETS["all_usda_plus_market"],
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise CropProgressValidationError("model frame is missing: " + ", ".join(missing))
    clean = frame.copy()
    for column in ("date", "entry_date", "exit_date"):
        clean[column] = pd.to_datetime(clean[column], errors="coerce")
    clean["year"] = pd.to_numeric(clean["year"], errors="coerce")
    clean[TARGET] = pd.to_numeric(clean[TARGET], errors="coerce")
    clean[COST] = pd.to_numeric(clean[COST], errors="coerce")
    clean = clean.dropna(subset=["date", "year", "entry_date", "exit_date", TARGET, COST])
    clean = clean.sort_values("date").reset_index(drop=True)
    if clean.duplicated("entry_date").any():
        raise CropProgressValidationError("model frame has duplicate entry sessions")
    if (clean["entry_date"] <= clean["date"]).any():
        raise CropProgressValidationError("entries must follow release dates")
    if (clean["exit_date"] <= clean["entry_date"]).any():
        raise CropProgressValidationError("exits must follow entries")
    if (clean[COST] <= 0).any():
        raise CropProgressValidationError("execution costs must be positive")
    if not include_holdout:
        clean = clean[clean["year"] < UNTOUCHED_HOLDOUT_YEAR].reset_index(drop=True)
    missing_years = sorted(set(OUTER_TEST_YEARS) - set(clean["year"].astype(int)))
    if missing_years:
        raise CropProgressValidationError(
            "development frame is incomplete; missing year(s): "
            + ", ".join(map(str, missing_years))
        )
    if include_holdout and UNTOUCHED_HOLDOUT_YEAR not in set(clean["year"].astype(int)):
        raise CropProgressValidationError("model frame has no sealed 2026 rows")
    return clean


def evaluate_candidate(frame: pd.DataFrame, *, feature_set: str, model: str,
                       protocol: dict) -> ValidationResult:
    rows = []
    features = FEATURE_SETS[feature_set]
    for fold, (train_index, test_index) in enumerate(outer_year_folds(frame), start=1):
        train = frame.loc[train_index].reset_index(drop=True)
        test = frame.loc[test_index].reset_index(drop=True)
        params = _select_params(train, features=features, model=model, protocol=protocol)
        rows.append(_model_rows(
            train, test, features=features, model=model, params=params, fold=fold
        ))
    predictions = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return ValidationResult(feature_set, model, metrics(predictions), predictions)


def _simple_rule_rows(test: pd.DataFrame, *, fold: int) -> pd.DataFrame:
    change = pd.to_numeric(test["condition_balance_change_1w"], errors="coerce")
    score = -change.to_numpy(dtype="float64")
    raw = np.where(np.isfinite(score), np.sign(score), 0).astype("int64")
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
        "parameter": "buy_condition_deterioration",
    })


def evaluate_simple_rule(frame: pd.DataFrame) -> ValidationResult:
    rows = []
    for fold, (_, test_index) in enumerate(outer_year_folds(frame), start=1):
        rows.append(_simple_rule_rows(frame.loc[test_index].reset_index(drop=True), fold=fold))
    predictions = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return ValidationResult("condition_rule", "fixed_rule", metrics(predictions), predictions)


def shifted_usda(frame: pd.DataFrame, periods: int) -> pd.DataFrame:
    shifted = frame.copy()
    shifted.loc[:, USDA_FEATURES] = (
        shifted.groupby("year", sort=False)[list(USDA_FEATURES)].shift(periods)
    )
    return shifted


def _result_row(result: ValidationResult) -> dict[str, Any]:
    return {
        "candidate": f"{result.feature_set}_{result.model}",
        "feature_set": result.feature_set,
        "model": result.model,
        "features": len(FEATURE_SETS[result.feature_set]),
        **asdict(result.metrics),
    }


def evaluate_development(frame: pd.DataFrame, *, protocol: dict | None = None
                         ) -> ValidationBundle:
    protocol = protocol or load_protocol()
    development = _validate_frame(frame)
    candidates = [
        evaluate_candidate(development, feature_set=feature_set, model=model,
                           protocol=protocol)
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

    headline_result = max(weather, key=selection_rank)
    headline = f"{headline_result.feature_set}_{headline_result.model}"
    market = next(result for result in candidates
                  if result.feature_set == "market_only"
                  and result.model == headline_result.model)
    rule = evaluate_simple_rule(development)
    forward = evaluate_candidate(
        shifted_usda(development, 1), feature_set=headline_result.feature_set,
        model=headline_result.model, protocol=protocol,
    )
    backward = evaluate_candidate(
        shifted_usda(development, -1), feature_set=headline_result.feature_set,
        model=headline_result.model, protocol=protocol,
    )
    predictions = headline_result.predictions
    double_cost_total = float(_net(
        predictions["gross"].to_numpy(dtype="float64"),
        predictions["cost"].to_numpy(dtype="float64"),
        predictions["signal"].to_numpy(dtype="int64"), cost_multiplier=2.0,
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
        "beats fixed release rule net": score.total_net > rule.metrics.total_net,
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
    protocol = protocol or load_protocol()
    full = _validate_frame(frame, include_holdout=True)
    matches = [model for model in MODELS if headline.endswith(f"_{model}")]
    if len(matches) != 1:
        raise CropProgressValidationError(f"cannot parse frozen headline {headline!r}")
    model = matches[0]
    feature_set = headline[:-(len(model) + 1)]
    if feature_set not in FEATURE_SETS or feature_set == "market_only":
        raise CropProgressValidationError(f"invalid frozen feature set {feature_set!r}")
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
        predictions["signal"].to_numpy(dtype="int64"), cost_multiplier=2.0,
    ).sum())
    gate = protocol["holdout_assessment"]
    checks = {
        f"at least {gate['minimum_trades']} holdout trades": (
            headline_metrics.trades >= int(gate["minimum_trades"])
        ),
        "positive holdout net": headline_metrics.total_net > 0,
        "positive holdout net at doubled costs": double_cost_total > 0,
        "improves on market-only holdout net": headline_metrics.total_net > market_metrics.total_net,
        "improves on market-only holdout Sharpe": (
            np.isfinite(headline_metrics.sharpe)
            and (not np.isfinite(market_metrics.sharpe)
                 or headline_metrics.sharpe > market_metrics.sharpe)
        ),
        "beats fixed release rule holdout net": headline_metrics.total_net > rule_metrics.total_net,
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
