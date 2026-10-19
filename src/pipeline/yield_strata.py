"""Where a reported corn-yield score comes from: per-year errors, influence, strata.

The forward and nowcast evaluations each report one number per feature set across
sixteen test harvests. With a sample that small, an aggregate R² can be carried by
one or two seasons, so this module decomposes a score that has already been
computed rather than computing a new one.

THE DISTINCTION THAT MATTERS HERE. `yield_validation.score_predictions` reports a
score over a cohort of test years. Removing a year from that cohort and rescoring
is NOT the same experiment as the `exclude_2012` sample, which drops the year from
training as well and refits every fold. This module only ever does the former: it
asks how much of a published number depends on a single season's error. It never
refits, never selects, and cannot produce a better score to report. Read a large
influence value as low confidence in the aggregate, not as a result.

STRATA ARE DESCRIPTIVE CUTS. Tercile boundaries are taken from the scored test
years themselves, so a bucket is defined with knowledge of the outcomes it
contains. That is acceptable for asking which kinds of season a model helps in
and unacceptable as evidence of skill, because nothing selects a model here. Each
bucket keeps its own trend denominator, and a bucket of mild years has a small
one: a near-zero R² there can mean the model added nothing or that there was very
little trend error to beat, which is why MAE and the trend's own MAE are reported
alongside every ratio.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from .yield_validation import score_predictions

# Terciles across sixteen test years leave five or six seasons each. Below four a
# bucket's R² is a statement about individual harvests, so it is left unscored
# rather than published with a caveat nobody reads.
MIN_STRATUM_YEARS = 4
TERCILE_LABELS = ("low", "mid", "high")
# The trend forecast is the denominator of every score here, so its own R² is
# identically zero and every influence or stratum row for it would report that
# zero as though it were a measurement. It stays in the per-harvest errors, where
# its column is the baseline being compared against, and is dropped elsewhere.
BASELINE_FEATURE_SET = "trend_only"


def _validate(predictions: pd.DataFrame, group_columns: Sequence[str]) -> list[str]:
    group_columns = list(group_columns)
    if "feature_set" not in group_columns:
        raise ValueError("group_columns must include feature_set")
    required = {*group_columns, "year", "actual_yield", "trend_yield",
                "predicted_yield", "actual_anomaly", "predicted_anomaly"}
    missing = required.difference(predictions.columns)
    if missing:
        raise ValueError(f"predictions missing columns: {sorted(missing)}")
    if predictions.empty:
        raise ValueError("predictions is empty")
    if predictions.duplicated([*group_columns, "year"]).any():
        raise ValueError("predictions contain duplicate group/year rows")
    return group_columns


def per_year_errors(predictions: pd.DataFrame, *,
                    group_columns: Sequence[str] = ("sample", "feature_set"),
                    ) -> pd.DataFrame:
    """One row per scored harvest: the model's error, the trend's, and the gap.

    `sse_share` is the season's share of its group's model squared error, and
    `trend_sse_share` the same for the denominator. A year with a large share of
    the denominator is a year the aggregate score is mostly about.
    """
    group_columns = _validate(predictions, group_columns)
    frame = predictions.copy()
    frame["error"] = frame["predicted_yield"] - frame["actual_yield"]
    frame["trend_error"] = frame["trend_yield"] - frame["actual_yield"]
    frame["abs_error"] = frame["error"].abs()
    frame["trend_abs_error"] = frame["trend_error"].abs()
    # Positive means the model beat the trend forecast on this harvest.
    frame["abs_error_gap"] = frame["trend_abs_error"] - frame["abs_error"]
    frame["anomaly_error_pp"] = (
        frame["predicted_anomaly"] - frame["actual_anomaly"]) * 100
    grouped = frame.groupby(group_columns, sort=False)
    frame["sse_share"] = frame["error"] ** 2 / grouped["error"].transform(
        lambda errors: np.sum(errors ** 2))
    frame["trend_sse_share"] = frame["trend_error"] ** 2 / grouped[
        "trend_error"].transform(lambda errors: np.sum(errors ** 2))
    columns = [*group_columns, "year", "actual_yield", "trend_yield",
               "predicted_yield", "actual_anomaly", "predicted_anomaly",
               "anomaly_error_pp", "error", "trend_error", "abs_error",
               "trend_abs_error", "abs_error_gap", "sse_share", "trend_sse_share"]
    return frame.loc[:, columns].sort_values([*group_columns, "year"],
                                             ignore_index=True)


def _score_subset(frame: pd.DataFrame, group_columns: Sequence[str],
                  extra: dict | None = None) -> dict:
    """Score one group with the published metric definitions, not a new one."""
    scored = score_predictions(frame).iloc[0].to_dict()
    keys = {column: frame[column].iloc[0] for column in group_columns
            if column != "feature_set"}
    return {**keys, **scored, **(extra or {})}


def score_by_group(predictions: pd.DataFrame, *,
                   group_columns: Sequence[str] = ("sample", "feature_set"),
                   ) -> pd.DataFrame:
    """Reproduce the published aggregate score for every group, as a check."""
    group_columns = _validate(predictions, group_columns)
    rows = [_score_subset(frame, group_columns)
            for _, frame in predictions.groupby(group_columns, sort=False)]
    return pd.DataFrame(rows)


def score_influence(predictions: pd.DataFrame, *,
                    group_columns: Sequence[str] = ("sample", "feature_set"),
                    ) -> pd.DataFrame:
    """Drop one scored harvest at a time and rescore, without refitting anything.

    `r2_delta` is the rescored value minus the published one, so a large positive
    delta marks a season that was holding the published score down and a large
    negative delta marks the season carrying it. Nothing here is a candidate
    score to report: every row deliberately discards an observed harvest.
    """
    group_columns = _validate(predictions, group_columns)
    scored = predictions.loc[predictions["feature_set"] != BASELINE_FEATURE_SET]
    rows = []
    for _, frame in scored.groupby(group_columns, sort=False):
        if len(frame) < 3:
            continue
        baseline = _score_subset(frame, group_columns)
        for year in frame["year"]:
            retained = frame.loc[frame["year"] != year]
            dropped = _score_subset(retained, group_columns)
            rows.append({
                **{column: baseline[column] for column in group_columns},
                "dropped_year": int(year),
                "test_years": int(baseline["test_years"]),
                "retained_years": int(dropped["test_years"]),
                "baseline_r2_vs_trend": baseline["r2_vs_trend"],
                "r2_vs_trend_without_year": dropped["r2_vs_trend"],
                "r2_delta": dropped["r2_vs_trend"] - baseline["r2_vs_trend"],
                "baseline_mae_t_per_ha": baseline["mae_t_per_ha"],
                "mae_without_year": dropped["mae_t_per_ha"],
            })
    return pd.DataFrame(rows)


def influence_summary(influence: pd.DataFrame, *,
                      group_columns: Sequence[str] = ("sample", "feature_set"),
                      ) -> pd.DataFrame:
    """Per group: the published score and the range single-year removal spans.

    `sign_flips` counts removals that move R² across zero. A positive aggregate
    that flips when any one harvest is withheld is not a stable positive.
    """
    if influence.empty:
        return pd.DataFrame()
    group_columns = list(group_columns)
    rows = []
    for keys, frame in influence.groupby(group_columns, sort=False):
        baseline = float(frame["baseline_r2_vs_trend"].iloc[0])
        without = frame["r2_vs_trend_without_year"]
        carrying = frame.loc[frame["r2_delta"].idxmin()]
        rows.append({
            **dict(zip(group_columns, keys if isinstance(keys, tuple) else (keys,))),
            "test_years": int(frame["test_years"].iloc[0]),
            "r2_vs_trend": baseline,
            "r2_min_without_one_year": float(without.min()),
            "r2_max_without_one_year": float(without.max()),
            "r2_range": float(without.max() - without.min()),
            "most_load_bearing_year": int(carrying["dropped_year"]),
            "r2_without_that_year": float(carrying["r2_vs_trend_without_year"]),
            "sign_flips": int(np.sum(np.sign(without) != np.sign(baseline))),
        })
    return pd.DataFrame(rows)


def assign_strata(predictions: pd.DataFrame,
                  annual_table: pd.DataFrame | None = None, *,
                  group_columns: Sequence[str] = ("sample", "feature_set"),
                  ) -> pd.DataFrame:
    """Label each scored harvest by outcome severity, era, direction, and weather.

    Severity, drought, and heat buckets are terciles of the scored test years, so
    boundaries differ between groups whose cohorts differ and are descriptive
    rather than predeclared. `annual_table` supplies the full-season belt drought
    and heat used as season labels; without it those two dimensions are omitted.
    Full-season labels are used for the nowcast too, so a stratum describes the
    season that happened, not the information an August forecast could see.
    """
    group_columns = _validate(predictions, group_columns)
    frame = predictions.copy()
    frame["shortfall_pp"] = frame["actual_anomaly"] * 100
    frame["severity_pp"] = frame["actual_anomaly"].abs() * 100
    frame["direction"] = np.where(frame["actual_anomaly"] < 0,
                                  "shortfall", "surplus")
    if annual_table is not None:
        for column, name in (("peak_d2", "drought"), ("cty_heat_dd_july", "heat")):
            if column in annual_table.columns:
                frame[name] = frame["year"].map(annual_table[column])
    grouped = frame.groupby(group_columns, sort=False)
    frame["era"] = grouped["year"].transform(
        lambda years: np.where(years <= years.median(), "early", "late"))
    for source, name in (("severity_pp", "severity"), ("drought", "drought_level"),
                         ("heat", "heat_level")):
        if source in frame.columns:
            frame[name] = grouped[source].transform(_terciles)
    return frame


def _terciles(values: pd.Series) -> pd.Series:
    """Tercile labels, or a single NaN-free label when the cut is degenerate."""
    usable = values.dropna()
    if len(usable) < 2 * MIN_STRATUM_YEARS or usable.nunique() < 3:
        return pd.Series(np.nan, index=values.index, dtype="object")
    try:
        cut = pd.qcut(usable, 3, labels=list(TERCILE_LABELS), duplicates="drop")
    except ValueError:
        return pd.Series(np.nan, index=values.index, dtype="object")
    return cut.astype("object").reindex(values.index)


def score_strata(labelled: pd.DataFrame, *,
                 group_columns: Sequence[str] = ("sample", "feature_set"),
                 dimensions: Sequence[str] = ("direction", "era", "severity",
                                              "drought_level", "heat_level"),
                 min_years: int = MIN_STRATUM_YEARS) -> pd.DataFrame:
    """Score each bucket with the published metrics, skipping thin buckets.

    A bucket's R² uses that bucket's own trend errors as its denominator, so
    values are not comparable across buckets as if they shared a scale. The
    retained trend MAE is reported so a small denominator is visible.
    """
    group_columns = _validate(labelled, group_columns)
    scored = labelled.loc[labelled["feature_set"] != BASELINE_FEATURE_SET]
    rows = []
    for _, frame in scored.groupby(group_columns, sort=False):
        for dimension in dimensions:
            if dimension not in frame.columns:
                continue
            for bucket, bucket_frame in frame.groupby(dimension, sort=False,
                                                      dropna=True):
                if len(bucket_frame) < min_years:
                    continue
                rows.append(_score_subset(bucket_frame, group_columns, extra={
                    "dimension": dimension, "bucket": str(bucket),
                    "years": ";".join(str(year) for year
                                      in sorted(bucket_frame["year"])),
                }))
    if not rows:
        return pd.DataFrame()
    columns = [*group_columns, "dimension", "bucket", "test_years", "years",
               "mae_t_per_ha", "rmse_t_per_ha", "trend_mae_t_per_ha",
               "trend_rmse_t_per_ha", "r2_vs_trend", "mae_improvement",
               "anomaly_mae_pp", "first_test_year", "last_test_year"]
    result = pd.DataFrame(rows)
    return result.loc[:, [column for column in columns if column in result.columns]]
