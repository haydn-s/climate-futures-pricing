"""Distribution-free predictive intervals around the fixed corn-yield forecasts.

The forward and nowcast evaluations publish a point forecast per harvest. A point
forecast is not usable as a supply-risk estimate: a buyer needs to know how wrong
it can be. This adds intervals without touching the forecasts themselves -- no
model, feature set, or alpha is refitted, retuned, or reselected here, and the
point prediction this module reports for a year is the same number already in the
committed prediction CSVs.

WHY JACKKNIFE+ RATHER THAN SPLIT CONFORMAL. Split conformal would hold out a
calibration block and take a quantile of its residuals. With sixteen scored
harvests beginning in 2010, a strictly chronological split leaves too few
calibration residuals to form a finite 90% interval until the very end of the
sample: n residuals support a level only when ceil((1-alpha)(n+1)) <= n, so 90%
needs nine, which is not reached until 2019. The seven evaluable years that
remain are all in the calm recent era the strata report shows carries least
information. Jackknife+ instead draws its residuals from inside each fold's own
training window, which already holds ten to twenty-five harvests, so every scored
year gets a finite interval at both levels.

WHAT THE GUARANTEE IS, AND IS NOT. Jackknife+ (Barber, Candes, Ramdas and
Tibshirani 2021) guarantees coverage of at least 1 - 2*alpha, not the nominal
1 - alpha, so an interval labelled 90% here carries a worst-case guarantee of
80%. Both numbers are reported. More importantly, that guarantee assumes the
training residuals are exchangeable with the test harvest, and a crop-yield
series with a fitted trend is not exchangeable: the years are ordered, the trend
is estimated from them, and weather regimes persist. So these intervals are
calibrated empirically and checked against observed coverage rather than relied
on theoretically. Treat the coverage table as the evidence and the guarantee as
background.

TWO CHOICES THAT PUSH IN KNOWN DIRECTIONS. The inner leave-one-out models train
on one year fewer than the model making the point forecast, so their residuals
are slightly too large and the intervals slightly too wide -- conservative. The
trend is refitted inside the inner loop as well, on the inner training years
only, so a leave-one-out residual is never measured against a trend the withheld
harvest helped define. Skipping that refit would have been cheaper and would have
made the intervals look better.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .yield_validation import MIN_TRAIN_YEARS, fit_trend

# Reported nominal levels. 90% is the most a sixteen-harvest sample can check
# with any resolution: one uncovered year moves observed coverage by 6.25 points.
NOMINAL_LEVELS = (0.80, 0.90)
METHODS = ("jackknife_plus", "jackknife", "gaussian")
# Grid for the calibration curve, coarse enough to stay readable at n=16.
CALIBRATION_GRID = (0.50, 0.60, 0.70, 0.80, 0.90, 0.95)
PREDICTION_COLUMNS = [
    "feature_set", "year", "method", "nominal_level", "guaranteed_level",
    "train_years", "calibration_residuals", "actual_yield", "trend_yield",
    "predicted_yield", "lower", "upper", "width", "width_pp_of_trend",
    "covered", "finite", "pit",
]


def _fold_forecast(usable: pd.DataFrame, train_years: np.ndarray,
                   features: Sequence[str], target_years: Sequence[int],
                   ) -> np.ndarray:
    """Refit trend, scaling, and Ridge on `train_years`; forecast `target_years`.

    This repeats `yield_validation.expanding_predictions` exactly so that the
    point forecast reported here is the published one rather than a near miss.
    """
    train = usable.loc[train_years]
    training_trend = fit_trend(train["yield_t_per_ha"], train.index)
    target = train["yield_t_per_ha"].to_numpy(dtype=float) / training_trend - 1.0
    estimator = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    estimator.fit(train.loc[:, list(features)].to_numpy(), target)
    anomaly = estimator.predict(
        usable.loc[list(target_years), list(features)].to_numpy())
    return fit_trend(train["yield_t_per_ha"], target_years) * (1.0 + anomaly)


def _upper(values: np.ndarray, level: float) -> float:
    """The ceil(level*(n+1))-th smallest value, or +inf when the level needs more."""
    rank = int(np.ceil(level * (len(values) + 1)))
    if rank > len(values):
        return float("inf")
    return float(np.sort(values)[rank - 1])


def _lower(values: np.ndarray, level: float) -> float:
    """The floor((1-level)*(n+1))-th smallest value, or -inf when unsupported."""
    rank = int(np.floor((1.0 - level) * (len(values) + 1)))
    if rank < 1:
        return float("-inf")
    return float(np.sort(values)[rank - 1])


def _intervals_for_year(point: float, loo_point: np.ndarray,
                        signed: np.ndarray, level: float,
                        ) -> dict[str, tuple[float, float]]:
    """Jackknife+, plain jackknife, and a Gaussian residual baseline.

    Jackknife+ widens the interval by the spread of the leave-one-out forecasts
    at the test harvest, so an unstable fold produces a visibly wider interval.
    The plain jackknife ignores that spread, and the Gaussian baseline also
    assumes symmetric normal residuals, which 2012 is the obvious reason to
    doubt. Both are reported only as comparisons.
    """
    absolute = np.abs(signed)
    half = _upper(absolute, level)
    deviation = float(np.std(signed, ddof=1)) if len(signed) > 1 else float("nan")
    # Two-sided normal quantile: level 0.90 -> 1.6449.
    z = NormalDist().inv_cdf(0.5 + level / 2.0)
    return {
        "jackknife_plus": (_lower(loo_point - absolute, level),
                           _upper(loo_point + absolute, level)),
        "jackknife": (point - half, point + half),
        "gaussian": (point - z * deviation, point + z * deviation),
    }


@dataclass(frozen=True)
class FoldResiduals:
    """Everything one chronological fold knows, before any interval is formed.

    `signed` holds the leave-one-out residuals from inside the training window
    and `loo_point` the matching leave-one-out forecasts at the test harvest, so
    a consumer can rebuild a jackknife+ interval or a conformal predictive
    sample. `train_anomalies` are the training years' observed deviations from
    that fold's own trend, which is the climatological distribution available at
    the same moment. Nothing here has seen the harvest being forecast.
    """

    feature_set: str
    year: int
    train_years: int
    point: float
    trend: float
    actual: float
    signed: np.ndarray
    loo_point: np.ndarray
    train_anomalies: np.ndarray

    @property
    def pit(self) -> float:
        """Conformal rank of the realised error among the fold's residuals."""
        error = abs(self.actual - self.point)
        return min(1.0, float((1 + np.sum(np.abs(self.signed) <= error))
                              / (len(self.signed) + 1)))


def _prepare(table: pd.DataFrame, feature_sets: Mapping[str, Sequence[str]], *,
             min_train_years: int) -> pd.DataFrame:
    if not 2 <= min_train_years:
        raise ValueError("min_train_years must be at least two")
    if "trend_only" in feature_sets:
        raise ValueError("trend_only has no fitted residuals to calibrate")
    columns = sorted({name for names in feature_sets.values() for name in names})
    return table.loc[:, [*columns, "yield_t_per_ha"]].replace(
        [np.inf, -np.inf], np.nan).dropna().sort_index()


def fold_residuals(
    table: pd.DataFrame,
    feature_sets: Mapping[str, Sequence[str]],
    *, min_train_years: int = MIN_TRAIN_YEARS,
) -> Iterator[FoldResiduals]:
    """Walk the folds forward, yielding each one's forecast and residual history.

    This is the expensive part of the calculation and the part that has to be
    right about time, so intervals and downstream decisions share it rather than
    each implementing the leave-one-out loop. For every withheld training year
    the trend and the regression are both refitted without it.
    """
    usable = _prepare(table, feature_sets, min_train_years=min_train_years)
    years = usable.index.to_numpy()
    for name, features in feature_sets.items():
        for year in years:
            train_years = years[years < year]
            if len(train_years) < min_train_years:
                continue
            training_yields = usable.loc[train_years, "yield_t_per_ha"]
            point = float(_fold_forecast(usable, train_years, features, [year])[0])
            trend = float(fit_trend(training_yields, [year])[0])
            signed, loo_point = [], []
            for withheld in train_years:
                inner = train_years[train_years != withheld]
                forecast = _fold_forecast(usable, inner, features, [withheld, year])
                signed.append(float(usable.loc[withheld, "yield_t_per_ha"])
                              - float(forecast[0]))
                loo_point.append(float(forecast[1]))
            yield FoldResiduals(
                feature_set=name, year=int(year), train_years=len(train_years),
                point=point, trend=trend,
                actual=float(usable.loc[year, "yield_t_per_ha"]),
                signed=np.asarray(signed), loo_point=np.asarray(loo_point),
                train_anomalies=(training_yields.to_numpy(dtype=float)
                                 / fit_trend(training_yields, train_years) - 1.0),
            )


def interval_predictions(
    table: pd.DataFrame,
    feature_sets: Mapping[str, Sequence[str]],
    *, min_train_years: int = MIN_TRAIN_YEARS,
    levels: Sequence[float] = NOMINAL_LEVELS,
) -> pd.DataFrame:
    """One row per harvest, feature set, method, and level.

    Every fold trains only on earlier harvests, and every calibration residual
    comes from a model that saw neither the harvest it scores nor the harvest
    being forecast. `pit` is the conformal rank of the realised error among the
    fold's calibration residuals, uniform on (0, 1] under exchangeability and the
    basis of the calibration curve.
    """
    if any(not 0.0 < level < 1.0 for level in levels):
        raise ValueError("levels must lie strictly between zero and one")
    rows = []
    for fold in fold_residuals(table, feature_sets,
                               min_train_years=min_train_years):
        for level in levels:
            bounds = _intervals_for_year(fold.point, fold.loo_point,
                                         fold.signed, level)
            for method, (lower, upper) in bounds.items():
                finite = np.isfinite(lower) and np.isfinite(upper)
                rows.append({
                    "feature_set": fold.feature_set, "year": fold.year,
                    "method": method, "nominal_level": level,
                    "guaranteed_level": (2.0 * level - 1.0
                                         if method == "jackknife_plus" else np.nan),
                    "train_years": fold.train_years,
                    "calibration_residuals": len(fold.signed),
                    "actual_yield": fold.actual, "trend_yield": fold.trend,
                    "predicted_yield": fold.point, "lower": lower, "upper": upper,
                    "width": upper - lower if finite else float("inf"),
                    "width_pp_of_trend": ((upper - lower) / fold.trend * 100
                                          if finite else float("inf")),
                    "covered": bool(lower <= fold.actual <= upper),
                    "finite": bool(finite), "pit": fold.pit,
                })
    return pd.DataFrame(rows, columns=PREDICTION_COLUMNS)


def coverage(intervals: pd.DataFrame, *,
             group_columns: Sequence[str] = ("feature_set",)) -> pd.DataFrame:
    """Observed coverage and interval width per group, method, and level.

    `coverage_resolution_pp` is one harvest expressed in percentage points, the
    smallest change the sample can register. Quote observed coverage with it:
    at sixteen harvests a single miss is worth more than six points, so "88%"
    and "94%" are one season apart rather than meaningfully different.
    """
    if intervals.empty:
        return pd.DataFrame()
    keys = [*group_columns, "method", "nominal_level"]
    rows = []
    for values, frame in intervals.groupby(keys, sort=False):
        finite = frame.loc[frame["finite"]]
        rows.append({
            **dict(zip(keys, values if isinstance(values, tuple) else (values,))),
            "guaranteed_level": frame["guaranteed_level"].iloc[0],
            "harvests": len(frame),
            "finite_intervals": len(finite),
            "covered": int(frame["covered"].sum()),
            "observed_coverage": float(frame["covered"].mean()),
            "coverage_resolution_pp": 100.0 / len(frame),
            "mean_width": float(finite["width"].mean()) if len(finite) else np.nan,
            "median_width": float(finite["width"].median()) if len(finite) else np.nan,
            "mean_width_pp_of_trend": (float(finite["width_pp_of_trend"].mean())
                                       if len(finite) else np.nan),
            "uncovered_years": ";".join(
                str(year) for year in sorted(frame.loc[~frame["covered"], "year"])),
        })
    return pd.DataFrame(rows)


def calibration_curve(intervals: pd.DataFrame, *,
                      group_columns: Sequence[str] = ("feature_set",),
                      grid: Sequence[float] = CALIBRATION_GRID) -> pd.DataFrame:
    """Observed frequency of the conformal rank falling below each nominal level.

    This is method-independent: it reads the residual ranks rather than any one
    interval construction, so it says whether the fold's own residual history
    describes the error that actually arrived. Perfect calibration tracks the
    diagonal; values below it mean the intervals are too narrow.
    """
    if intervals.empty:
        return pd.DataFrame()
    keys = list(group_columns)
    # One row per harvest: pit does not depend on method or level.
    unique = intervals.drop_duplicates([*keys, "year"])
    rows = []
    for values, frame in unique.groupby(keys, sort=False):
        for level in grid:
            rows.append({
                **dict(zip(keys, values if isinstance(values, tuple) else (values,))),
                "nominal_level": level, "harvests": len(frame),
                "observed": float((frame["pit"] <= level).mean()),
            })
    return pd.DataFrame(rows)
