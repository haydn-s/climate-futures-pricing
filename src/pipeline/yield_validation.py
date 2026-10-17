"""Chronological corn-yield hindcasts against a training-only trend forecast.

These are retrospective tests using the archived, potentially revised yield and
weather values. Full-season features measure harvest risk; they are not an
in-season information set. No model or parameter is selected from these scores.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .config import Geography
from .features import annual, panel
from .storage import ProcessedStore

MIN_TRAIN_YEARS = 10
FEATURE_SETS = {
    "points": ["heat_dd_july", "tmax_july", "precip_summer"],
    "counties": ["cty_heat_dd_july", "cty_tmax_july"],
    "counties_drought": ["cty_heat_dd_july", "cty_tmax_july", "peak_d2"],
    "points_drought": ["heat_dd_july", "precip_summer", "peak_d2"],
}
INPUT_TABLES = (
    "nasa_power_point_daily", "nclimgrid_county_daily",
    "usdm_county_drought", "owid_yields_annual",
)
PREDICTION_COLUMNS = [
    "feature_set", "year", "train_start", "train_end", "train_years",
    "actual_yield", "trend_yield", "predicted_yield",
    "actual_anomaly", "predicted_anomaly",
]


def build_annual_table(store: ProcessedStore, geography: Geography) -> pd.DataFrame:
    """Use the same complete April–October seasons as the original yield study."""
    crop = geography.crop("corn")
    states = tuple(state.postal for state in crop.states)
    season = range(crop.sensitive_months.start, crop.sensitive_months.end + 1)
    points = panel.belt_weather(store.read("nasa_power_point_daily"), "corn")
    counties = panel.belt_counties(store.read("nclimgrid_county_daily"), states)
    drought = panel.belt_drought(store.read("usdm_county_drought"), states)
    table = annual.annual_weather(points, season=season)
    table = table.join(annual.annual_weather(
        counties, tmax_column="cty_tmax_c", precip_column="cty_prcp_mm",
        season=season, label="cty_"))
    table = table.join(annual.annual_drought(drought, season=season))
    table["yield_t_per_ha"] = annual.corn_yield(store.read("owid_yields_annual"))
    complete = ((table["days"].fillna(0) >= 180)
                & (table["cty_days"].fillna(0) >= 180)
                & (table["maps"].fillna(0) >= 25)
                & table["yield_t_per_ha"].notna())
    return table.loc[complete].copy()


def fit_trend(train_yields: pd.Series, years: Sequence[int]) -> np.ndarray:
    """Extrapolate a linear yield trend fitted exclusively to supplied harvests."""
    if len(train_yields) < 3:
        raise ValueError("a yield trend requires at least three training years")
    origin = int(train_yields.index.min())
    coefficients = np.polyfit(
        train_yields.index.to_numpy(dtype=float) - origin,
        train_yields.to_numpy(dtype=float), 1,
    )
    trend = np.polyval(coefficients, np.asarray(years, dtype=float) - origin)
    if not np.isfinite(trend).all() or np.any(trend <= 0):
        raise ValueError("the fitted yield trend is non-positive or non-finite")
    return trend


def expanding_predictions(
    table: pd.DataFrame,
    feature_sets: Mapping[str, Sequence[str]] = FEATURE_SETS,
    *, min_train_years: int = MIN_TRAIN_YEARS,
) -> pd.DataFrame:
    """Train through t−1, then predict t on a common complete-case cohort.

    A fixed standardized Ridge(alpha=1) predicts the fractional deviation from
    that fold's trend. The trend-only control predicts zero deviation. Trends,
    scaling, and regression are all refitted inside each fold. Missing-feature
    years are excluded for every candidate, including the matched control.
    """
    if min_train_years < 3:
        raise ValueError("min_train_years must be at least three")
    if "trend_only" in feature_sets:
        raise ValueError("trend_only is reserved for the matched baseline")
    years = np.asarray(table.index, dtype=float)
    if (table.index.has_duplicates or not np.isfinite(years).all()
            or not np.equal(years, years.astype(int)).all()):
        raise ValueError("yield table must have unique integer years as its index")
    columns = sorted({name for names in feature_sets.values() for name in names})
    usable = table.loc[:, [*columns, "yield_t_per_ha"]].replace(
        [np.inf, -np.inf], np.nan).dropna().sort_index()
    if (usable["yield_t_per_ha"] <= 0).any():
        raise ValueError("observed yields must be positive")
    rows = []
    for year in usable.index:
        train = usable.loc[usable.index < year]
        if len(train) < min_train_years:
            continue
        training_trend = fit_trend(train["yield_t_per_ha"], train.index)
        trend = float(fit_trend(train["yield_t_per_ha"], [year])[0])
        target = train["yield_t_per_ha"].to_numpy() / training_trend - 1.0
        actual = float(usable.loc[year, "yield_t_per_ha"])
        common = {
            "year": int(year), "train_start": int(train.index.min()),
            "train_end": int(train.index.max()), "train_years": len(train),
            "actual_yield": actual, "trend_yield": trend,
            "actual_anomaly": actual / trend - 1.0,
        }
        for name, features in {"trend_only": [], **feature_sets}.items():
            prediction = 0.0
            if features:
                estimator = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
                estimator.fit(train.loc[:, list(features)].to_numpy(), target)
                prediction = float(estimator.predict(
                    usable.loc[[year], list(features)].to_numpy())[0])
            rows.append({
                **common, "feature_set": name,
                "predicted_yield": trend * (1.0 + prediction),
                "predicted_anomaly": prediction,
            })
    return pd.DataFrame(rows, columns=PREDICTION_COLUMNS)


def score_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    """Score yield errors against each row's own training-only trend forecast.

    R² versus trend is 1 − SSE(model)/SSE(trend), not an ex-post test-mean
    baseline. Positive skill means beating a forecast that could be fitted
    without the held-out harvest. Anomaly errors are percentage points.
    """
    rows = []
    for name, frame in predictions.groupby("feature_set", sort=False):
        actual = frame["actual_yield"].to_numpy(dtype=float)
        error = frame["predicted_yield"].to_numpy(dtype=float) - actual
        baseline_error = frame["trend_yield"].to_numpy(dtype=float) - actual
        denominator = float(np.sum(baseline_error ** 2))
        baseline_mae = float(np.mean(np.abs(baseline_error)))
        rows.append({
            "feature_set": name, "test_years": len(frame),
            "first_test_year": int(frame["year"].min()),
            "last_test_year": int(frame["year"].max()),
            "mae_t_per_ha": float(np.mean(np.abs(error))),
            "rmse_t_per_ha": float(np.sqrt(np.mean(error ** 2))),
            "trend_mae_t_per_ha": baseline_mae,
            "trend_rmse_t_per_ha": float(np.sqrt(np.mean(baseline_error ** 2))),
            "r2_vs_trend": (1.0 - float(np.sum(error ** 2)) / denominator
                            if denominator > 1e-20 else float("nan")),
            "mae_improvement": (1.0 - float(np.mean(np.abs(error))) / baseline_mae
                                if baseline_mae > 1e-10 else float("nan")),
            "anomaly_mae_pp": float(np.mean(np.abs(
                frame["predicted_anomaly"] - frame["actual_anomaly"]))) * 100,
        })
    return pd.DataFrame(rows)
