"""Release-time features from official national USDA corn progress tables."""

from __future__ import annotations

import numpy as np
import pandas as pd

STAGES = ("planted", "emerged", "silking", "dough", "dented", "mature", "harvested")
CONDITION_BUCKETS = {
    "good_excellent": ("good", "excellent"),
    "poor_very_poor": ("poor", "very_poor"),
}
COMPARISONS = ("current", "previous_week", "previous_year", "five_year_average")


class CropProgressFeatureError(ValueError):
    """The cleaned release table cannot support an auditable feature row."""


def build_crop_progress_features(observations: pd.DataFrame) -> pd.DataFrame:
    required = {
        "release_date", "reference_date", "available_at", "measure", "stage",
        "region", "region_type", "comparison", "category", "value",
        "quality_status", "source_sha256",
    }
    missing = sorted(required - set(observations.columns))
    if missing:
        raise CropProgressFeatureError(
            "Crop Progress observations are missing: " + ", ".join(missing)
        )
    frame = observations.copy()
    frame["release_date"] = pd.to_datetime(frame["release_date"], errors="coerce")
    frame["reference_date"] = pd.to_datetime(frame["reference_date"], errors="coerce")
    frame["available_at"] = pd.to_datetime(frame["available_at"], errors="coerce", utc=True)
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    if frame[["release_date", "reference_date", "available_at"]].isna().any().any():
        raise CropProgressFeatureError("Crop Progress dates contain an invalid value")
    if (frame["available_at"].dt.date != frame["release_date"].dt.date).any():
        raise CropProgressFeatureError("release date and 4 p.m. availability date disagree")
    aggregate = frame[frame["region"].eq("selected_states")].copy()
    if aggregate.empty:
        raise CropProgressFeatureError("no official selected-states aggregate is present")
    if aggregate["quality_status"].ne("ok").any():
        bad = sorted(aggregate.loc[aggregate["quality_status"].ne("ok"),
                                   "quality_status"].unique())
        raise CropProgressFeatureError(
            "aggregate source quality failed: " + ", ".join(map(str, bad))
        )

    metadata = aggregate.groupby("release_date", sort=True).agg(
        reference_date=("reference_date", _one),
        available_at=("available_at", _one),
        source_sha256=("source_sha256", _one),
        source_observations=("value", "size"),
    )
    result = metadata.reset_index()
    condition = _condition_features(aggregate)
    progress = _progress_features(aggregate)
    result = result.merge(condition, on="release_date", how="left", validate="one_to_one")
    result = result.merge(progress, on="release_date", how="left", validate="one_to_one")
    result["year"] = result["reference_date"].dt.year.astype("int64")
    ordered = [
        "release_date", "reference_date", "available_at", "year",
        "source_sha256", "source_observations",
    ]
    ordered.extend(column for column in result.columns if column not in ordered)
    return result.loc[:, ordered].sort_values("available_at", ignore_index=True)


def _one(values: pd.Series):
    unique = values.drop_duplicates()
    if len(unique) != 1:
        raise CropProgressFeatureError(
            f"one release maps to {len(unique)} conflicting metadata values"
        )
    return unique.iloc[0]


def _condition_features(frame: pd.DataFrame) -> pd.DataFrame:
    subset = frame[frame["measure"].eq("condition")]
    if subset.empty:
        return pd.DataFrame({"release_date": pd.Series(dtype="datetime64[ns]")})
    _unique(subset, ["release_date", "comparison", "category"], "condition")
    wide = subset.pivot(index="release_date", columns=["comparison", "category"],
                        values="value")
    output = pd.DataFrame(index=wide.index)
    for bucket, categories in CONDITION_BUCKETS.items():
        for comparison in ("current", "previous_week", "previous_year"):
            output[f"_{bucket}_{comparison}"] = sum(
                _column(wide, comparison, category) for category in categories
            )
        output[f"condition_{bucket}"] = output[f"_{bucket}_current"]
        output[f"condition_{bucket}_change_1w"] = (
            output[f"_{bucket}_current"] - output[f"_{bucket}_previous_week"]
        )
        output[f"condition_{bucket}_vs_prior_year"] = (
            output[f"_{bucket}_current"] - output[f"_{bucket}_previous_year"]
        )
    output["condition_balance"] = (
        output["condition_good_excellent"] - output["condition_poor_very_poor"]
    )
    output["condition_balance_change_1w"] = (
        output["condition_good_excellent_change_1w"]
        - output["condition_poor_very_poor_change_1w"]
    )
    output["condition_balance_vs_prior_year"] = (
        output["condition_good_excellent_vs_prior_year"]
        - output["condition_poor_very_poor_vs_prior_year"]
    )
    output = output.drop(columns=[column for column in output if column.startswith("_")])
    return output.reset_index()


def _progress_features(frame: pd.DataFrame) -> pd.DataFrame:
    subset = frame[frame["measure"].eq("progress")]
    if subset.empty:
        return pd.DataFrame({"release_date": pd.Series(dtype="datetime64[ns]")})
    _unique(subset, ["release_date", "stage", "comparison"], "progress")
    wide = subset.pivot(index="release_date", columns=["stage", "comparison"],
                        values="value")
    output = pd.DataFrame(index=wide.index)
    for stage in STAGES:
        current = _column(wide, stage, "current")
        previous_week = _column(wide, stage, "previous_week")
        previous_year = _column(wide, stage, "previous_year")
        average = _column(wide, stage, "five_year_average")
        output[f"progress_{stage}"] = current
        output[f"progress_{stage}_change_1w"] = current - previous_week
        output[f"progress_{stage}_vs_prior_year"] = current - previous_year
        output[f"progress_{stage}_vs_five_year"] = current - average
    return output.reset_index()


def _column(frame: pd.DataFrame, outer: str, inner: str) -> pd.Series:
    key = (outer, inner)
    if key in frame.columns:
        return frame[key].astype("float64")
    return pd.Series(np.nan, index=frame.index, dtype="float64")


def _unique(frame: pd.DataFrame, identity: list[str], label: str) -> None:
    duplicates = frame.duplicated(identity, keep=False)
    if duplicates.any():
        sample = frame.loc[duplicates, identity].iloc[0].to_dict()
        raise CropProgressFeatureError(f"duplicate {label} aggregate: {sample}")
