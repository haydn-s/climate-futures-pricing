"""Does free climate data measure a corn supply shock? And do county averages
beat five representative points?

Run from the repository root, after fetch and clean:

    PYTHONPATH=src python analysis/corn_yield_model.py

This is research question one, and the leg of the project most likely to produce
a positive result: the preliminary analysis already found July heat correlating
-0.78 with yield shortfalls. What is new here is the measurement. nClimGrid
county averages cover all 473 counties in the five corn states, against the one
representative point per state the previews used, so the two can be scored
against the same harvests and the 827 MB download can be asked to justify itself.

Publication lag plays no part here. The harvest is known long after every weather
observation is final, so there is nothing to withhold and no peek to sweep -- the
honesty problem in this script is entirely about the trend.

WITH 26 OBSERVATIONS, DETRENDING IS WHERE A RESULT GETS MANUFACTURED. Corn yields
rise about a bushel a year for reasons unrelated to weather, so the target is a
deviation from trend. Fit that trend once on all 26 years and the held-out year
has already helped define what counts as normal for itself. Every out-of-sample
number below therefore refits the trend inside each fold on training years only;
the descriptive correlation table does not, and says so.

Leave-one-year-out rather than k-fold: 26 rows cannot spare a fifth of
themselves, and a year is the natural unit because the weather within one is
heavily autocorrelated while the gap between two is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sklearn.linear_model import LinearRegression, Ridge  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

from pipeline.config import load_geography  # noqa: E402
from pipeline.features import annual, panel  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

# A season must be close to fully covered before its aggregates mean anything.
MIN_SEASON_DAYS = 180
MIN_SEASON_MAPS = 25

# The two measurements, and the feature sets scored for each.
CANDIDATES = {
    "points (5 states)": ["heat_dd_july", "tmax_july", "precip_summer"],
    "counties (473)": ["cty_heat_dd_july", "cty_tmax_july"],
    "counties + drought": ["cty_heat_dd_july", "cty_tmax_july", "peak_d2"],
    "points + drought": ["heat_dd_july", "precip_summer", "peak_d2"],
}


def correlate(x: pd.Series, y: pd.Series) -> tuple[float, int, float]:
    """Pearson r, n, and a normal-approximation two-sided p-value."""
    import math
    pair = pd.concat([x, y], axis=1).dropna()
    if len(pair) < 8:
        return float("nan"), len(pair), float("nan")
    r = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
    t = r * math.sqrt((len(pair) - 2) / max(1e-12, 1 - r * r))
    return r, len(pair), math.erfc(abs(t) / math.sqrt(2))


def stars(p: float) -> str:
    if np.isnan(p):
        return ""
    return "***" if p < 0.01 else "**" if p < 0.05 else "*" if p < 0.10 else ""


def build_table(store: ProcessedStore, geography: ProcessedStore) -> pd.DataFrame:
    """One row per year: both weather measurements, drought, and raw yield."""
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

    # Drop years either measurement only partly covers: a half-season sum reads
    # as a mild year rather than a missing one.
    complete = (table["days"].fillna(0) >= MIN_SEASON_DAYS) \
        & (table["cty_days"].fillna(0) >= MIN_SEASON_DAYS) \
        & (table["maps"].fillna(0) >= MIN_SEASON_MAPS) \
        & table["yield_t_per_ha"].notna()
    dropped = table.loc[~complete]
    if not dropped.empty:
        print(f"dropped {len(dropped)} incomplete year(s): "
              f"{', '.join(str(y) for y in dropped.index)}")
    return table.loc[complete].copy()


def leave_one_year_out(table: pd.DataFrame, features: list[str],
                       model_name: str = "ridge") -> tuple[float, float, int]:
    """Out-of-sample R-squared and correlation, refitting the trend per fold.

    The baseline for R-squared is the training years' mean shortfall, which is
    the only prediction available without the held-out harvest.
    """
    usable = table.dropna(subset=[*features, "yield_t_per_ha"])
    years = usable.index.to_numpy()
    if len(years) < 10:
        return float("nan"), float("nan"), len(years)

    predicted, actual = [], []
    for year in years:
        train_years = years[years != year]
        # The trend is refitted here, inside the fold, on training years only.
        shortfall = annual.detrend_pct(usable["yield_t_per_ha"], fit_years=train_years)
        target = shortfall.dropna()
        train = np.isin(target.index.to_numpy(), train_years)
        if year not in target.index or train.sum() < 8:
            continue
        X = usable.loc[target.index, features].to_numpy()
        y = target.to_numpy()
        estimator = (make_pipeline(StandardScaler(), Ridge(alpha=1.0))
                     if model_name == "ridge"
                     else make_pipeline(StandardScaler(), LinearRegression()))
        estimator.fit(X[train], y[train])
        position = int(np.flatnonzero(target.index.to_numpy() == year)[0])
        predicted.append(float(estimator.predict(X[[position]])[0]))
        actual.append(float(y[position]))

    predicted, actual = np.asarray(predicted), np.asarray(actual)
    if len(predicted) < 10:
        return float("nan"), float("nan"), len(predicted)
    baseline = np.mean(actual)
    r2 = 1.0 - np.sum((actual - predicted) ** 2) / np.sum((actual - baseline) ** 2)
    r = float(np.corrcoef(predicted, actual)[0, 1])
    return float(r2), r, len(predicted)


def main() -> None:
    geography = load_geography()
    store = ProcessedStore(default_data_root())
    table = build_table(store, geography)

    print("Does free climate data measure a corn supply shock, "
          "and do county averages beat five points?")
    print(f"\nyears: {len(table)} complete seasons "
          f"{table.index.min()}..{table.index.max()}")

    # Descriptive, full-sample trend -- comparable to the README's table.
    shortfall = annual.detrend_pct(table["yield_t_per_ha"])
    worst = shortfall.nsmallest(4)
    print("largest shortfalls: "
          + ", ".join(f"{year} {value:+.0%}" for year, value in worst.items()))

    print("\nCORRELATION WITH THE YIELD SHORTFALL  (full-sample trend, descriptive)")
    print(f"  {'measure':24s} {'r':>7s} {'n':>4s}  {'':3s}   {'excl. 2012':>10s}")
    measures = ["tmax_july", "heat_dd_july", "heat_dd_season", "precip_summer",
                "cty_tmax_july", "cty_heat_dd_july", "cty_heat_dd_season",
                "cty_precip_summer", "peak_d2", "mean_d2"]
    without_2012 = table.index != 2012
    for measure in measures:
        if measure not in table.columns:
            continue
        r, n, p = correlate(table[measure], shortfall)
        r_ex, _, p_ex = correlate(table.loc[without_2012, measure],
                                  shortfall.loc[without_2012])
        print(f"  {measure:24s} {r:+6.2f} {n:4d}  {stars(p):3s}   "
              f"{r_ex:+6.2f} {stars(p_ex):3s}")

    print("\nOUT OF SAMPLE, leave-one-year-out, trend refitted per fold")
    print(f"  {'feature set':22s} {'k':>2s}  {'R2':>7s}  {'r':>6s}  {'folds':>5s}")
    for label, features in CANDIDATES.items():
        available = [f for f in features if f in table.columns]
        if len(available) < len(features):
            print(f"  {label:22s}  -- missing {set(features) - set(available)}")
            continue
        r2, r, folds = leave_one_year_out(table, available)
        print(f"  {label:22s} {len(available):2d}  {r2:+7.3f}  {r:+6.2f}  {folds:5d}")

    print("\nSAME, EXCLUDING 2012")
    subset = table.loc[without_2012]
    for label, features in CANDIDATES.items():
        available = [f for f in features if f in table.columns]
        if len(available) < len(features):
            continue
        r2, r, folds = leave_one_year_out(subset, available)
        print(f"  {label:22s} {len(available):2d}  {r2:+7.3f}  {r:+6.2f}  {folds:5d}")

    print("\n" + "-" * 70)
    print("Reading this: a positive out-of-sample R-squared means free weather data")
    print("predicts a harvest shortfall it was never shown -- research question one")
    print("answered in the affirmative. Compare the two measurements to see whether")
    print("473 county averages buy anything over five representative points.")


if __name__ == "__main__":
    main()
