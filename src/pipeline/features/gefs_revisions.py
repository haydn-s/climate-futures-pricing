"""Operational GEFS point forecasts to daily Corn Belt revision features."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = {
    "initialized_at", "model_available_at", "valid_at", "lead_hours",
    "statistic", "variable", "point", "value",
}
VARIABLES = ("tmax_c", "tmin_c", "precip_mm")
WINDOWS = ((1, 3), (4, 7), (8, 10))
HEAT_THRESHOLD_C = 29.0


class GefsFeatureError(ValueError):
    """The decoded forecast panel cannot support honest daily revisions."""


def daily_point_forecasts(frame: pd.DataFrame) -> pd.DataFrame:
    """Combine four six-hour intervals into one forecast day per point.

    Temperature extrema are max/min across the four intervals; precipitation is
    their sum. A point-day missing even one interval is excluded rather than
    scaled up or imputed.
    """
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise GefsFeatureError(f"GEFS point table is missing columns: {', '.join(missing)}")
    work = frame.copy()
    work["initialized_at"] = pd.to_datetime(work["initialized_at"], utc=True)
    work["model_available_at"] = pd.to_datetime(work["model_available_at"], utc=True)
    work["valid_at"] = pd.to_datetime(work["valid_at"], utc=True)
    if not set(work["variable"]).issubset(VARIABLES):
        unexpected = sorted(set(work["variable"]) - set(VARIABLES))
        raise GefsFeatureError(f"unexpected GEFS variables: {', '.join(unexpected)}")
    if (work["lead_hours"] <= 0).any() or (work["lead_hours"] % 6 != 0).any():
        raise GefsFeatureError("GEFS leads must be positive six-hour steps")
    work["forecast_day"] = ((work["lead_hours"].astype(int) - 1) // 24) + 1

    identity = ["initialized_at", "model_available_at", "statistic", "point",
                "forecast_day", "variable"]
    duplicates = work.duplicated(identity + ["lead_hours"], keep=False)
    if duplicates.any():
        raise GefsFeatureError("GEFS point table has duplicate run/lead/variable rows")
    counts = work.groupby(identity, observed=True)["lead_hours"].nunique()
    complete = counts[counts == 4].index
    work = work.set_index(identity).loc[work.set_index(identity).index.isin(complete)].reset_index()
    if work.empty:
        return pd.DataFrame(columns=[
            "initialized_at", "model_available_at", "statistic", "point",
            "forecast_day", *VARIABLES,
        ])

    aggregations = {"tmax_c": "max", "tmin_c": "min", "precip_mm": "sum"}
    pieces = []
    group = ["initialized_at", "model_available_at", "statistic", "point", "forecast_day"]
    for variable, operation in aggregations.items():
        subset = work[work["variable"] == variable]
        values = subset.groupby(group, observed=True)["value"].agg(operation).rename(variable)
        pieces.append(values)
    daily = pd.concat(pieces, axis=1, join="inner").reset_index()
    return daily.sort_values(group, ignore_index=True)


def build_revision_features(frame: pd.DataFrame, *, windows=WINDOWS) -> pd.DataFrame:
    """One row per operational run with absolute and prior-day revision features."""
    daily = daily_point_forecasts(frame)
    if daily.empty:
        return pd.DataFrame(columns=["initialized_at", "model_available_at", "statistic"])

    expected_source = frame.copy()
    expected_source["initialized_at"] = pd.to_datetime(
        expected_source["initialized_at"], utc=True
    )
    expected_points = (expected_source.groupby(
        ["initialized_at", "statistic"], observed=True
    )["point"].nunique().rename("expected_points").reset_index())
    point_counts = (daily.groupby(
        ["initialized_at", "statistic", "forecast_day"], observed=True
    )["point"].nunique().rename("point_count").reset_index())
    point_counts = point_counts.merge(
        expected_points, on=["initialized_at", "statistic"], how="left",
        validate="many_to_one",
    )
    complete_days = point_counts.loc[
        point_counts["point_count"] == point_counts["expected_points"],
        ["initialized_at", "statistic", "forecast_day"],
    ]
    daily = daily.merge(
        complete_days,
        on=["initialized_at", "statistic", "forecast_day"],
        how="inner",
        validate="many_to_one",
    )

    belt = (daily.groupby(
        ["initialized_at", "model_available_at", "statistic", "forecast_day"],
        observed=True,
    )[["tmax_c", "tmin_c", "precip_mm"]].mean().reset_index())
    belt["heat_dd29"] = (belt["tmax_c"] - HEAT_THRESHOLD_C).clip(lower=0)

    rows: list[dict] = []
    run_keys = ["initialized_at", "model_available_at", "statistic"]
    for keys, run in belt.groupby(run_keys, observed=True, sort=True):
        initialized, available, statistic = keys
        row = {
            "initialized_at": initialized,
            "model_available_at": available,
            "statistic": statistic,
        }
        complete = True
        for start, end in windows:
            selected = run[run["forecast_day"].between(start, end)]
            expected = end - start + 1
            label = f"d{start}_{end}"
            if selected["forecast_day"].nunique() != expected:
                complete = False
                break
            row[f"{label}_tmax_c_mean"] = float(selected["tmax_c"].mean())
            row[f"{label}_tmin_c_mean"] = float(selected["tmin_c"].mean())
            row[f"{label}_precip_mm_sum"] = float(selected["precip_mm"].sum())
            row[f"{label}_heat_dd29_sum"] = float(selected["heat_dd29"].sum())
        if complete:
            rows.append(row)

    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(columns=["initialized_at", "model_available_at", "statistic"])
    result = result.sort_values(["statistic", "initialized_at"], ignore_index=True)
    absolute = [column for column in result if column.startswith("d")]
    previous_time = result.groupby("statistic", observed=True)["initialized_at"].shift()
    consecutive = (result["initialized_at"] - previous_time) == timedelta(days=1)
    for column in absolute:
        revision = result.groupby("statistic", observed=True)[column].diff()
        result[f"rev_{column}"] = revision.where(consecutive, np.nan)
    result["has_prior_day_revision"] = consecutive
    return result
