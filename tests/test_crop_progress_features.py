"""USDA release features use only the contemporaneous national table."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

import pandas as pd
import pytest

from pipeline.clients.nass_crop_progress import parse_report
from pipeline.features.crop_progress import (
    CropProgressFeatureError,
    build_crop_progress_features,
)
from test_nass_crop_progress import REPORT


def observations() -> pd.DataFrame:
    rows = [asdict(row) for row in parse_report(
        REPORT, expected_release=date(2026, 10, 5)
    )]
    frame = pd.DataFrame(rows)
    frame["source_sha256"] = "a" * 64
    return frame


def test_release_features_use_the_published_comparison_columns() -> None:
    result = build_crop_progress_features(observations())
    assert len(result) == 1
    row = result.iloc[0]
    assert row["condition_good_excellent"] == 54.0
    assert row["condition_good_excellent_change_1w"] == -3.0
    assert row["condition_poor_very_poor"] == 18.0
    assert row["condition_poor_very_poor_change_1w"] == 1.0
    assert row["condition_balance"] == 36.0
    assert row["condition_balance_change_1w"] == -4.0
    assert pd.isna(row["condition_good_excellent_vs_prior_year"])
    assert row["progress_mature"] == 83.0
    assert row["progress_mature_change_1w"] == 11.0
    assert row["progress_mature_vs_five_year"] == 0.0


def test_state_values_do_not_change_the_official_aggregate_feature() -> None:
    frame = observations()
    frame.loc[frame["region"].eq("Iowa"), "value"] = 0.0
    result = build_crop_progress_features(frame)
    assert result.iloc[0]["condition_good_excellent"] == 54.0
    assert result.iloc[0]["progress_mature"] == 83.0


def test_aggregate_quality_failure_stops_feature_construction() -> None:
    frame = observations()
    frame.loc[frame["region"].eq("selected_states"), "quality_status"] = "bad"
    with pytest.raises(CropProgressFeatureError, match="aggregate source quality"):
        build_crop_progress_features(frame)
