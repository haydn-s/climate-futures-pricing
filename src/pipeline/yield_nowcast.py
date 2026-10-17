"""Fixed-date corn yield hindcasts with explicit historical availability limits.

POWER and USDM publication columns reconstruct availability using the existing
pipeline's lag rules. Their values and the final yield labels can be revised.
County publication dates are audited, never replaced with assumed older dates.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from .calendar import USDM_RELEASE_LAG_DAYS
from .config import Geography
from .features import annual
from .features.panel import HEAT_THRESHOLD_C
from .yield_validation import FEATURE_SETS, expanding_predictions, score_predictions

ISSUE_MONTHS = (8, 9, 10)
NOWCAST_FEATURE_SETS = {name: FEATURE_SETS[name] for name in ("points", "points_drought")}


def _dates(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True).dt.tz_localize(None).dt.normalize()


def weather_belt(power: pd.DataFrame, points: Sequence[str]) -> pd.DataFrame:
    """Require every configured corn point, value, and publication date per day.

    Publication is the last point's date. An unavailable point cannot silently
    turn the five-point mean into a four-point mean at an earlier issue date.
    """
    rows = power.loc[(power["crop"] == "corn") & power["point"].isin(points)].copy()
    rows["date"] = _dates(rows["date"])
    rows["publication_date"] = _dates(rows["publication_date"])
    rows[["tmax_c", "precip_mm"]] = rows[["tmax_c", "precip_mm"]].replace(
        [np.inf, -np.inf], np.nan)
    if rows.duplicated(["date", "point"]).any():
        raise ValueError("duplicate corn point-days in the POWER input")
    daily = rows.groupby("date").agg(
        tmax_c=("tmax_c", "mean"), precip_mm=("precip_mm", "mean"),
        publication_date=("publication_date", "max"),
        points=("point", "nunique"), temperatures=("tmax_c", "count"),
        precipitation=("precip_mm", "count"), publications=("publication_date", "count"),
    )
    complete = daily[["points", "temperatures", "precipitation", "publications"]].eq(
        len(points)).all(axis=1)
    return daily.loc[complete, ["tmax_c", "precip_mm", "publication_date"]].sort_index()


def drought_belt(drought: pd.DataFrame, states: Sequence[str]) -> pd.DataFrame:
    """Equal-weight states after requiring each state's reference county set.

    The earliest archived map defines the fixed county universe; later missing
    counties block that map. Future-added counties do not change past coverage.
    """
    rows = drought.loc[drought["state"].isin(states)].copy()
    rows["map_date"] = _dates(rows["map_date"])
    rows["publication_date"] = _dates(rows["publication_date"])
    rows["d2"] = rows["d2"].replace([np.inf, -np.inf], np.nan)
    if rows.empty:
        raise ValueError("no drought maps for the configured corn states")
    if rows.duplicated(["map_date", "fips"]).any():
        raise ValueError("duplicate county maps in the drought input")
    reference = rows.loc[rows["map_date"] == rows["map_date"].min()]
    if set(reference["state"]) != set(states):
        raise ValueError("the first drought map must cover every corn state")
    expected = reference.groupby("state")["fips"].nunique()
    rows = rows.loc[rows["fips"].isin(reference["fips"])]
    by_state = rows.groupby(["map_date", "state"]).agg(
        d2=("d2", "mean"), counties=("fips", "nunique"), values=("d2", "count"),
        publication_date=("publication_date", "max"), publications=("publication_date", "count"),
    ).reset_index()
    counts = by_state["state"].map(expected)
    complete = by_state[["counties", "values", "publications"]].eq(counts, axis=0).all(axis=1)
    belt = by_state.loc[complete].groupby("map_date").agg(
        d2=("d2", "mean"), states=("state", "nunique"),
        publication_date=("publication_date", "max"),
    )
    return belt.loc[belt["states"] == len(states), ["d2", "publication_date"]].sort_index()


def county_publications(counties: pd.DataFrame, states: Sequence[str]) -> pd.DataFrame:
    """Date-level publication audit only; this does not build county forecasts."""
    rows = counties.loc[counties["state"].isin(states)].copy()
    rows["date"] = _dates(rows["date"])
    rows["publication_date"] = _dates(rows["publication_date"])
    daily = rows.groupby("date").agg(
        publication_date=("publication_date", "max"),
        rows=("state", "size"), publications=("publication_date", "count"),
        states=("state", "nunique"),
    )
    complete = (daily["rows"] == daily["publications"]) & (daily["states"] == len(states))
    return daily.loc[complete, ["publication_date"]].sort_index()


def build_issue_table(
    power: pd.DataFrame, drought: pd.DataFrame, counties: pd.DataFrame,
    yields: pd.DataFrame, geography: Geography, *, power_lag_days: int = 5,
) -> pd.DataFrame:
    """One public-data feature/audit record per harvest and fixed issue date.

    A full April-to-cutoff weather grid and every scheduled released drought
    map are required. Gaps or unknown publication dates produce missing
    features, never a smaller accumulation masquerading as mild weather.
    """
    if power_lag_days < 0:
        raise ValueError("POWER lag must be nonnegative")
    crop = geography.crop("corn")
    states = tuple(state.postal for state in crop.states)
    points = tuple(point.name for point in crop.points)
    weather = weather_belt(power, points)
    maps = drought_belt(drought, states)
    county = county_publications(counties, states)
    harvests = annual.corn_yield(yields).dropna()
    if weather.empty:
        raise ValueError("no complete corn point-weather days")
    first_year = int(weather.index.year.min())
    last_year = min(int(weather.index.year.max()), int(harvests.index.max()))
    rows = []
    for year in range(first_year, last_year + 1):
        if year not in harvests.index:
            continue
        start = pd.Timestamp(year, crop.sensitive_months.start, 1)
        for month in ISSUE_MONTHS:
            issue = pd.Timestamp(year, month, 1)  # end-of-day date-level cutoff
            weather_end = issue - pd.Timedelta(power_lag_days, unit="D")
            expected_days = pd.date_range(start, weather_end)
            expected_maps = pd.date_range(
                start, issue - pd.Timedelta(USDM_RELEASE_LAG_DAYS, unit="D"), freq="W-TUE")
            known_weather = weather.loc[(weather.index >= start)
                                        & (weather.index <= weather_end)
                                        & (weather["publication_date"] <= issue)]
            known_maps = maps.loc[(maps.index >= start) & (maps.index <= issue)
                                  & (maps["publication_date"] <= issue)]
            known_county = county.loc[(county.index >= start) & (county.index <= issue)
                                      & (county["publication_date"] <= issue)]
            weather_complete = (len(known_weather) == len(expected_days)
                                and known_weather.index.isin(expected_days).all())
            drought_complete = (len(known_maps) == len(expected_maps)
                                and known_maps.index.isin(expected_maps).all())
            july = known_weather.loc[known_weather.index.month == 7]
            summer = known_weather.loc[known_weather.index.month.isin(annual.SUMMER_MONTHS)]
            row = {
                "year": year, "issue_date": issue, "issue_month": month,
                "yield_t_per_ha": float(harvests.loc[year]),
                "weather_days": len(known_weather), "weather_expected_days": len(expected_days),
                "weather_through": known_weather.index.max(),
                "weather_latest_publication": known_weather["publication_date"].max(),
                "weather_complete": weather_complete,
                "drought_maps": len(known_maps), "drought_expected_maps": len(expected_maps),
                "drought_through": known_maps.index.max(),
                "drought_latest_publication": known_maps["publication_date"].max(),
                "drought_complete": drought_complete,
                "county_published_days": len(known_county),
                "county_observation_through": known_county.index.max(),
                "heat_dd_july": float((july["tmax_c"] - HEAT_THRESHOLD_C).clip(lower=0).sum())
                if weather_complete and len(july) else np.nan,
                "tmax_july": float(july["tmax_c"].mean()) if weather_complete else np.nan,
                "precip_summer": float(summer["precip_mm"].sum()) if weather_complete else np.nan,
                "peak_d2": float(known_maps["d2"].max()) if drought_complete else np.nan,
            }
            rows.append(row)
    return pd.DataFrame(rows)


def evaluate_issues(
    issues: pd.DataFrame, *, min_train_years: int = 10,
) -> tuple[pd.DataFrame, pd.DataFrame, list[int]]:
    """Fit each issue separately using the same years across all three dates."""
    features = sorted({column for names in NOWCAST_FEATURE_SETS.values() for column in names})
    if issues.duplicated(["year", "issue_month"]).any():
        raise ValueError("duplicate year/issue pairs")
    complete = issues.dropna(subset=[*features, "yield_t_per_ha"])
    eligible = complete.groupby("year")["issue_month"].agg(set)
    common_years = eligible.index[eligible.map(lambda months: months == set(ISSUE_MONTHS))].tolist()
    predictions, results = [], []
    for sample in ("all_years", "exclude_2012"):
        years = [year for year in common_years if sample == "all_years" or year != 2012]
        for month in ISSUE_MONTHS:
            frame = issues.loc[issues["year"].isin(years) & (issues["issue_month"] == month)]
            pred = expanding_predictions(frame.set_index("year"), NOWCAST_FEATURE_SETS,
                                         min_train_years=min_train_years)
            if pred.empty:
                raise ValueError(f"{sample}/{month:02d}-01: insufficient common training seasons")
            scores = score_predictions(pred)
            pred.insert(0, "issue_month", month)
            pred.insert(0, "sample", sample)
            pred["issue_date"] = pd.to_datetime(
                pred["year"].astype(str) + f"-{month:02d}-01")
            scores.insert(0, "issue_month", month)
            scores.insert(0, "sample", sample)
            predictions.append(pred)
            results.append(scores)
    return pd.concat(predictions, ignore_index=True), pd.concat(results, ignore_index=True), common_years
