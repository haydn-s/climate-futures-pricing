"""Auditable inference for the frozen corn-minus-wheat paper-trading model.

This module deliberately exposes one versioned specification rather than a
model-selection interface. Research selected the specification; prospective
inference must not reopen that search against the consumed 2024-2026 holdout.
It trains only on outcomes completed before the decision date, reports stale
inputs, persists the fitted estimator and signal metadata, and never places an
order.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from .config import Geography
from .signal_eval import (
    DEFAULT_COST_BPS_PER_LEG,
    FittedSignalModel,
    eligible_rows,
    fit_signal_model,
    transaction_cost,
)
from .signals import FEATURE_SETS, TARGETS, build
from .storage import ProcessedStore, RawStore

MODEL_VERSION = "corn-wheat-all-public-boosting-v1"
BASELINE_ID = "ml-signal-baseline-v1"
FEATURE_SET = "all_public"
MODEL_NAME = "boosting"
HORIZON = 5
MAX_DECISION_AGE_DAYS = 10
SNAPSHOT_TABLES = (
    "cpc_outlook_point_daily",
    "nasa_power_point_daily",
    "nclimgrid_county_daily",
    "usdm_county_drought",
    "yahoo_prices_daily",
)
SNAPSHOT_SOURCES = (
    "cpc_outlook", "nasa_power", "nclimgrid", "usdm", "yahoo_prices",
)
PRIMARY_TARGET = next(target for target in TARGETS if target.name == "corn_wheat")


@dataclass(frozen=True)
class SignalRun:
    """The fitted model plus the JSON-safe decision record it produced."""

    fitted: FittedSignalModel
    record: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_snapshot(raw: RawStore, processed: ProcessedStore) -> dict[str, Any]:
    """Fingerprint the exact local archive used to generate a signal."""
    sources: dict[str, Any] = {}
    for source in SNAPSHOT_SOURCES:
        records = raw.history(source)
        publications = [record.publication_date for record in records
                        if record.publication_date is not None]
        sources[source] = {
            "fetch_records": len(records),
            "latest_fetch_utc": (
                max(record.fetched_at for record in records).isoformat()
                if records else None),
            "latest_publication_date": (
                max(publications).isoformat() if publications else None),
        }

    tables: dict[str, Any] = {}
    for name in SNAPSHOT_TABLES:
        path = processed.path(name)
        if not path.exists():
            raise FileNotFoundError(f"required signal table is missing: {path}")
        tables[name] = {
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }

    manifest = raw.manifest_path
    if not manifest.exists():
        raise FileNotFoundError(f"raw archive manifest is missing: {manifest}")
    return {
        "manifest": {"sha256": _sha256(manifest), "bytes": manifest.stat().st_size},
        "processed_tables": tables,
        "sources": sources,
    }


def training_rows(frame: pd.DataFrame, *, features: tuple[str, ...],
                  decision_date: pd.Timestamp, target_column: str,
                  target_end_column: str) -> pd.DataFrame:
    """Return labels fully known before the proposed decision close."""
    usable = eligible_rows(
        frame, features=features, target_column=target_column,
        extra_columns=(target_end_column,))
    completed = usable[target_end_column].notna()
    completed &= usable[target_end_column] < decision_date
    return usable.loc[completed].reset_index(drop=True)


def _action(position: int) -> str:
    return {1: "BUY", 0: "HOLD", -1: "SELL"}[int(position)]


def _legs(action: str) -> list[dict[str, str]]:
    if action == "BUY":
        return [{"instrument": "corn", "side": "long"},
                {"instrument": "wheat", "side": "short"}]
    if action == "SELL":
        return [{"instrument": "corn", "side": "short"},
                {"instrument": "wheat", "side": "long"}]
    return []


def infer(frame: pd.DataFrame, *, features: tuple[str, ...], as_of: date,
          generated_at: datetime, snapshot: dict[str, Any]) -> SignalRun:
    """Fit the frozen specification and score the latest eligible decision."""
    if generated_at.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    required = {
        "date", "year", "market_data_through", PRIMARY_TARGET.column(HORIZON),
        f"target_end_date_{HORIZON}", *features,
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"signal frame is missing columns: {', '.join(missing)}")

    candidates = frame.loc[frame["date"] <= pd.Timestamp(as_of)].copy()
    forecast_columns = [column for column in features if column.startswith("fc_")]
    if forecast_columns:
        candidates = candidates.loc[candidates[forecast_columns].notna().any(axis=1)]
    if candidates.empty:
        raise ValueError(f"no eligible signal decision exists on or before {as_of.isoformat()}")
    decision = candidates.sort_values("date").iloc[-1]
    decision_date = pd.Timestamp(decision["date"])
    target_column = PRIMARY_TARGET.column(HORIZON)
    target_end_column = f"target_end_date_{HORIZON}"
    train = training_rows(
        frame, features=features, decision_date=decision_date,
        target_column=target_column, target_end_column=target_end_column)
    if train.empty:
        raise ValueError("no completed historical targets precede the decision")

    cost_rate = transaction_cost(
        PRIMARY_TARGET, basis_points_per_leg=DEFAULT_COST_BPS_PER_LEG)
    fitted = fit_signal_model(
        train, features=features, target_column=target_column,
        model_name=MODEL_NAME, cost_rate=cost_rate)
    decision_frame = pd.DataFrame([decision])
    score = float(fitted.scores(decision_frame)[0])
    if not np.isfinite(score):
        raise ValueError("fitted model produced a non-finite signal score")
    position = int(fitted.positions(decision_frame)[0])
    model_action = _action(position)
    age_days = (as_of - decision_date.date()).days
    stale = age_days > MAX_DECISION_AGE_DAYS
    action = "HOLD" if stale else model_action
    missing_features = [column for column in features if pd.isna(decision[column])]
    warnings = []
    if stale:
        warnings.append(
            f"latest eligible decision is {age_days} calendar days old; "
            "action forced to HOLD")
    if missing_features:
        warnings.append(
            f"{len(missing_features)} feature(s) require training-fold median imputation")
    finite_threshold = np.isfinite(fitted.threshold)
    if not finite_threshold:
        warnings.append("training validation selected no actionable threshold")

    record: dict[str, Any] = {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "baseline_id": BASELINE_ID,
        "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
        "as_of_date": as_of.isoformat(),
        "decision": {
            "date": decision_date.date().isoformat(),
            "market_data_through": pd.Timestamp(
                decision["market_data_through"]).date().isoformat(),
            "age_calendar_days": age_days,
            "status": "STALE_INPUTS" if stale else "READY_FOR_PAPER_TRADING",
        },
        "signal": {
            "action": action,
            "model_action": model_action,
            "position": 0 if stale else position,
            "model_position": position,
            "score": score,
            "threshold": float(fitted.threshold) if finite_threshold else None,
            "distance_from_threshold": (
                float(abs(score) - fitted.threshold) if finite_threshold else None),
            "legs": _legs(action),
        },
        "model": {
            "family": MODEL_NAME,
            "selected_parameter": fitted.parameter,
            "feature_set": FEATURE_SET,
            "features": list(features),
            "missing_features": missing_features,
            "target": PRIMARY_TARGET.name,
            "horizon_trading_days": HORIZON,
            "cost_bps_per_leg": DEFAULT_COST_BPS_PER_LEG,
        },
        "training": {
            "rows": len(train),
            "decision_start": pd.Timestamp(train["date"].min()).date().isoformat(),
            "decision_end": pd.Timestamp(train["date"].max()).date().isoformat(),
            "outcomes_known_through": pd.Timestamp(
                train[target_end_column].max()).date().isoformat(),
            "eligibility_rule": "target end date is strictly before decision date",
            "includes_consumed_2024_2026_holdout": bool((train["year"] >= 2024).any()),
        },
        "data_snapshot": snapshot,
        "warnings": warnings,
        "execution": {
            "authorized": False,
            "mode": "paper-trading signal only",
            "orders_submitted": 0,
        },
    }
    return SignalRun(fitted=fitted, record=record)


def run_primary_signal(*, raw: RawStore, processed: ProcessedStore,
                       geography: Geography, as_of: date,
                       generated_at: datetime | None = None) -> SignalRun:
    """Build point-in-time inputs and run the frozen primary specification."""
    frames, available = build(processed, geography, horizons=(HORIZON,))
    frozen_features = FEATURE_SETS[FEATURE_SET]
    missing = sorted(set(frozen_features).difference(available[FEATURE_SET]))
    if missing:
        raise ValueError(
            "frozen model features are unavailable: " + ", ".join(missing))
    return infer(
        frames[HORIZON], features=frozen_features, as_of=as_of,
        generated_at=generated_at or datetime.now(timezone.utc),
        snapshot=archive_snapshot(raw, processed))


def save_run(run: SignalRun, root: Path) -> tuple[Path, Path]:
    """Persist a unique fitted artifact and immutable JSON decision record."""
    generated = datetime.fromisoformat(run.record["generated_at_utc"])
    stamp = generated.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    decision = run.record["decision"]["date"]
    stem = f"{decision}_{stamp}"
    model_dir = root / "models" / MODEL_VERSION
    signal_dir = root / "signals" / MODEL_VERSION
    model_dir.mkdir(parents=True, exist_ok=True)
    signal_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"{stem}.joblib"
    record_path = signal_dir / f"{stem}.json"
    if model_path.exists() or record_path.exists():
        raise FileExistsError(f"signal run already exists for stamp {stamp}")

    bundle = {
        "model_version": MODEL_VERSION,
        "baseline_id": BASELINE_ID,
        "decision_date": decision,
        "features": run.fitted.features,
        "parameter": run.fitted.parameter,
        "threshold": run.fitted.threshold,
        "cost_rate": run.fitted.cost_rate,
        "estimator": run.fitted.estimator,
    }
    joblib.dump(bundle, model_path)
    record = json.loads(json.dumps(run.record))
    record["model_artifact"] = {
        "path": model_path.relative_to(root).as_posix(),
        "sha256": _sha256(model_path),
        "bytes": model_path.stat().st_size,
    }
    with open(record_path, "x", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    return record_path, model_path


def load_model_artifact(path: Path) -> FittedSignalModel:
    """Load and validate a serialized artifact produced by :func:`save_run`."""
    bundle = joblib.load(path)
    if bundle.get("model_version") != MODEL_VERSION:
        raise ValueError(
            f"artifact model version {bundle.get('model_version')!r} does not match "
            f"runtime {MODEL_VERSION!r}")
    required = {
        "features", "parameter", "threshold", "cost_rate", "estimator",
    }
    missing = sorted(required.difference(bundle))
    if missing:
        raise ValueError(f"model artifact is missing fields: {', '.join(missing)}")
    return FittedSignalModel(
        model_name=MODEL_NAME,
        features=tuple(bundle["features"]),
        parameter=str(bundle["parameter"]),
        threshold=float(bundle["threshold"]),
        cost_rate=float(bundle["cost_rate"]),
        estimator=bundle["estimator"],
    )


def render_summary(record: dict[str, Any]) -> str:
    """Human-readable output that keeps stale and paper-only status prominent."""
    signal = record["signal"]
    decision = record["decision"]
    threshold = signal["threshold"]
    rendered_threshold = f"{threshold:.6f}" if threshold is not None else "none"
    lines = [
        f"{record['model_version']}: {signal['action']}",
        f"  decision date: {decision['date']} ({decision['status']})",
        f"  model action:  {signal['model_action']}",
        f"  score:         {signal['score']:+.6f}",
        f"  threshold:     {rendered_threshold}",
        f"  trained rows:  {record['training']['rows']}",
        "  execution:     disabled; paper-trading signal only",
    ]
    lines.extend(f"  WARNING: {warning}" for warning in record["warnings"])
    return "\n".join(lines)
