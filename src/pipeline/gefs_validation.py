"""Locked constants and year folds for the GEFS corn validation protocol."""

from __future__ import annotations

from pathlib import Path

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
OUTER_TEST_YEARS = (2023, 2024, 2025)
UNTOUCHED_HOLDOUT_YEAR = 2026


class GefsValidationError(ValueError):
    """The saved protocol and implemented validation boundary disagree."""


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
    if tuple(payload["feature_sets"]) != tuple(FEATURE_SETS):
        raise GefsValidationError("implemented feature sets differ from the locked protocol")
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
