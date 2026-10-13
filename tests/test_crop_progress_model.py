"""USDA releases use pre-release controls and strictly later dated-contract opens."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from pipeline.crop_progress_model import (
    CONDITION_FEATURES,
    PROGRESS_FEATURES,
    CropProgressModelError,
    build_execution_frame,
)
from test_gefs_model import contract_panel


def release_features() -> pd.DataFrame:
    row = {
        "release_date": pd.Timestamp("2024-07-15"),
        "reference_date": pd.Timestamp("2024-07-14"),
        "available_at": datetime(2024, 7, 15, 20, 0, tzinfo=timezone.utc),
        "source_sha256": "a" * 64,
    }
    row.update({feature: float(index) for index, feature in enumerate(
        (*CONDITION_FEATURES, *PROGRESS_FEATURES), start=1
    )})
    return pd.DataFrame([row])


def test_frame_uses_same_day_settlement_but_only_a_later_open() -> None:
    result = build_execution_frame(release_features(), contract_panel())
    assert len(result) == 1
    row = result.iloc[0]
    assert row["market_data_through"] == pd.Timestamp("2024-07-15")
    assert row["entry_date"] == pd.Timestamp("2024-07-16")
    assert row["exit_date"] == pd.Timestamp("2024-07-23")
    assert row["target_corn_cost_return"] > 0


def test_release_must_be_stamped_at_four_pm_eastern() -> None:
    features = release_features()
    features["available_at"] = pd.Timestamp("2024-07-15 19:59", tz="UTC")
    with pytest.raises(CropProgressModelError, match="4:00 p.m. Eastern"):
        build_execution_frame(features, contract_panel())


def test_release_week_must_end_before_publication() -> None:
    features = release_features()
    features["reference_date"] = features["release_date"]
    with pytest.raises(CropProgressModelError, match="reference week"):
        build_execution_frame(features, contract_panel())
