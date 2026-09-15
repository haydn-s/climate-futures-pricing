"""Initial analysis: does weather stress reach agricultural futures, and which moves first?

Run from the repository root:
    python analysis/initial_analysis.py

Every input is free and needs no API key; pulls are cached under data/raw/ on
the first run. Four previews, each answering a question that decides whether
the full project is worth doing:

  1. Mechanism   Do the worst summers for the corn belt line up with corn rallies?
  2. Timing      Do prices move before or after drought data is published?
  3. Validation  Does the weather index explain actual corn yield shortfalls?
  4. Contrast    Does cocoa -- a tree crop damaged over months -- price weather more slowly?

These are previews, not results: locations are equally weighted, nothing is
controlled for, and continuous futures prices jump when contracts roll. Every
headline is re-tested without 2012, the single most extreme year.
"""

from __future__ import annotations

import io
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
USER_AGENT = "climate-futures-pricing/0.1 (academic research)"
YEARS = range(2000, 2026)

# One representative farming point in each of five of the largest corn-producing
# states, with the state FIPS code the Drought Monitor expects. Equal weights for
# now; USDA production weights are a planned refinement.
CORN_BELT = {
    "Iowa": (42.0, -93.6, 19),
    "Illinois": (40.1, -88.9, 17),
    "Nebraska": (40.8, -98.4, 31),
    "Minnesota": (44.1, -94.0, 27),
    "Indiana": (40.3, -86.5, 18),
}
# Cocoa-growing areas of Cote d'Ivoire and Ghana, the two largest cocoa producers.
COCOA_BELT = {
    "Soubre": (5.8, -6.6),
    "Daloa": (6.9, -6.4),
    "Kumasi": (6.7, -1.6),
    "Sunyani": (7.3, -2.3),
}


# --------------------------------------------------------------------------- data


def _fetch(url: str, accept: str | None = None, user_agent: str = USER_AGENT) -> bytes:
    headers = {"User-Agent": user_agent}
    if accept:
        headers["Accept"] = accept
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def cached(name: str, url: str, **kwargs) -> bytes:
    path = RAW / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_fetch(url, **kwargs))
    return path.read_bytes()


def weather(name: str, lat: float, lon: float) -> pd.DataFrame:
    """Daily maximum temperature (C) and precipitation (mm) from NASA POWER."""
    params = {
        "parameters": "T2M_MAX,PRECTOTCORR", "community": "AG", "latitude": lat, "longitude": lon,
        "start": "20000101", "end": "20251231", "format": "JSON",
    }
    url = "https://power.larc.nasa.gov/api/temporal/daily/point?" + urllib.parse.urlencode(params)
    values = json.loads(cached(f"power_{name}.json", url))["properties"]["parameter"]
    frame = pd.DataFrame({"tmax": values["T2M_MAX"], "precip": values["PRECTOTCORR"]})
    frame.index = pd.to_datetime(frame.index, format="%Y%m%d")
    return frame.replace(-999.0, np.nan)


def drought(name: str, fips: int) -> pd.Series:
    """Weekly share of a state's area in severe drought or worse (D2+).

    The API needs the numeric state FIPS code; state abbreviations return nothing.
    """
    params = {"aoi": fips, "startdate": "1/1/2000", "enddate": "12/31/2025", "statisticsType": 1}
    url = ("https://usdmdataservices.unl.edu/api/StateStatistics/"
           "GetDroughtSeverityStatisticsByAreaPercent?" + urllib.parse.urlencode(params))
    rows = json.loads(cached(f"drought_{name}.json", url, accept="application/json"))
    return pd.Series({pd.Timestamp(r["mapDate"][:10]): float(r["d2"]) for r in rows}).sort_index()


def prices(symbol: str) -> pd.Series:
    """Daily closes. Explicit period bounds are required: range=max silently returns monthly bars."""
    period1 = int(datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp())
    period2 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?period1={period1}&period2={period2}&interval=1d")
    body = json.loads(cached(f"prices_{symbol.replace('=', '_')}.json", url, user_agent="Mozilla/5.0"))
    result = body["chart"]["result"][0]
    index = pd.to_datetime(result["timestamp"], unit="s").normalize()
    return pd.Series(result["indicators"]["quote"][0]["close"], index=index).dropna()


def yields(slug: str, entity_prefix: str) -> pd.Series:
    """Annual crop yields (FAO figures, republished by Our World in Data)."""
    url = f"https://ourworldindata.org/grapher/{slug}.csv?v=1&csvType=full&useColumnShortNames=true"
    frame = pd.read_csv(io.BytesIO(cached(f"owid_{slug}.csv", url)))
    value = [c for c in frame.columns if c not in ("entity", "code", "year")][0]
    rows = frame[frame["entity"].str.lower().str.startswith(entity_prefix.lower())]
    return pd.Series(rows[value].values, index=rows["year"].values).sort_index()


# --------------------------------------------------------------------------- stats


def correlate(x: pd.Series, y: pd.Series) -> tuple[float, int, float]:
    """Pearson r, sample size, and a normal-approximation two-sided p-value."""
    pair = pd.concat([x, y], axis=1).dropna()
    n = len(pair)
    if n < 8:
        return float("nan"), n, float("nan")
    r = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
    t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    return r, n, math.erfc(abs(t) / math.sqrt(2))


def zscore(series: pd.Series) -> pd.Series:
    return (series - series.mean()) / series.std()


def detrended_pct(series: pd.Series) -> pd.Series:
    """Deviation from a linear trend, as a fraction of the trend."""
    x = series.index.astype(float)
    fit = np.polyval(np.polyfit(x, series.values.astype(float), 1), x)
    return (series - fit) / fit


def stars(p: float) -> str:
    return "***" if p < 0.01 else "**" if p < 0.05 else "*" if p < 0.10 else ""


def joint_t_stats(frame: pd.DataFrame) -> dict[str, float]:
    """OLS t-statistics for every column except 'return', estimated together."""
    X = np.column_stack([np.ones(len(frame)), frame.drop(columns="return").values])
    y = frame["return"].values
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    sigma2 = resid @ resid / (len(y) - X.shape[1])
    se = np.sqrt(np.diag(sigma2 * np.linalg.inv(X.T @ X)))
    return {column: beta[i] / se[i] for i, column in enumerate(frame.columns.drop("return"), start=1)}


# --------------------------------------------------------------------------- previews


def main() -> None:
    pd.set_option("display.width", 200)
    print("Fetching public data (cached after the first run)...\n", flush=True)

    corn_weather = {name: weather(name, lat, lon) for name, (lat, lon, _) in CORN_BELT.items()}
    tmax = pd.concat({n: f["tmax"] for n, f in corn_weather.items()}, axis=1).mean(axis=1)
    precip = pd.concat({n: f["precip"] for n, f in corn_weather.items()}, axis=1).mean(axis=1)
    state_drought = {n: drought(n, fips) for n, (_, _, fips) in CORN_BELT.items()}
    drought_weekly = pd.concat(state_drought, axis=1).mean(axis=1)
    corn = prices("ZC=F")

    # ---- 1. Mechanism ------------------------------------------------------
    july_heat = pd.Series({y: tmax[(tmax.index.year == y) & (tmax.index.month == 7)].mean() for y in YEARS})
    summer_rain = pd.Series({y: precip[(precip.index.year == y) & precip.index.month.isin([6, 7, 8])].sum() for y in YEARS})
    summer_drought = pd.Series({
        y: drought_weekly[(drought_weekly.index.year == y) & drought_weekly.index.month.isin([6, 7, 8])].max()
        for y in YEARS
    })
    stress = (zscore(july_heat) - zscore(summer_rain) + zscore(summer_drought)) / 3

    def summer_return(series: pd.Series, year: int) -> float:
        window = series[(series.index >= f"{year}-06-01") & (series.index <= f"{year}-08-31")]
        return float(np.log(window.iloc[-1] / window.iloc[0])) if len(window) > 40 else float("nan")

    corn_summer = pd.Series({y: summer_return(corn, y) for y in YEARS})

    print("=" * 88)
    print("1. MECHANISM  Do the worst corn-belt summers line up with corn rallies?  (2000-2025)")
    print("=" * 88)
    table = pd.DataFrame({
        "July max temp C": july_heat.round(1),
        "Jun-Aug rain mm": summer_rain.round(0),
        "peak severe drought %": summer_drought.round(0),
        "stress index": stress.round(2),
        "corn Jun-Aug %": (np.expm1(corn_summer) * 100).round(0),
    })
    print("Five most stressful summers:")
    print(table.sort_values("stress index", ascending=False).head(5).to_string())
    r, n, p = correlate(stress, corn_summer)
    print(f"\ncorrelation, stress index vs summer corn return: r = {r:+.2f} (n={n}, p={p:.3f}) {stars(p)}")
    r_ex, n_ex, p_ex = correlate(stress.drop(2012), corn_summer.drop(2012))
    print(f"  ...excluding 2012:                               r = {r_ex:+.2f} (n={n_ex}, p={p_ex:.3f}) {stars(p_ex)}")
    top = stress.sort_values(ascending=False).index[:5]
    print(f"average summer corn return: five worst summers {np.expm1(corn_summer[top]).mean():+.0%} | "
          f"all other years {np.expm1(corn_summer.drop(top)).mean():+.0%}")
    try:
        etf = prices("CORN")
        etf_summer = pd.Series({y: summer_return(etf, y) for y in YEARS})
        r2, n2, p2 = correlate(stress, etf_summer)
        print(f"robustness, Teucrium CORN fund (no contract-roll jumps, 2010+): r = {r2:+.2f} (n={n2}, p={p2:.3f}) {stars(p2)}")
        r3, n3, p3 = correlate(stress.drop(2012), etf_summer.drop(2012))
        print(f"  ...excluding 2012:                                             r = {r3:+.2f} (n={n3}, p={p3:.3f}) {stars(p3)}")
    except Exception as exc:  # the fund is a robustness check, not a dependency
        print(f"robustness check skipped: {exc}")

    # ---- 2. Timing ---------------------------------------------------------
    print("\n" + "=" * 88)
    print("2. TIMING  Do corn prices move before or after drought data is published?")
    print("=" * 88)
    # Drought Monitor maps are valid Tuesday and released Thursday.
    release = drought_weekly.index + pd.Timedelta(days=2)
    price_at_release = corn.reindex(corn.index.union(release)).ffill().reindex(release)
    weekly_return = pd.Series(np.log(price_at_release.values), index=drought_weekly.index).diff()
    drought_change = drought_weekly.diff()
    season = drought_weekly.index.month.isin([5, 6, 7, 8, 9])
    print("correlation of this week's change in corn-belt drought with corn returns k weeks away")
    print("(growing season, May-Sep; negative k = price moved BEFORE the data was published)\n")
    for k in range(-4, 5):
        r, n, p = correlate(drought_change[season], weekly_return.shift(-k)[season])
        label = "price moved first" if k < 0 else ("publication week" if k == 0 else "after publication")
        bar = "#" * int(round(abs(r) * 60))
        print(f"  k = {k:+d}  r = {r:+.3f} {stars(p):3s} n={n}  {label:18s} {bar}")

    # Drought builds over several weeks, so weekly changes are themselves
    # correlated. That alone can make prices appear to lead, so estimate every
    # lead and lag jointly: this week's return on drought changes published up to
    # three weeks later (price first) and three weeks earlier (data first).
    auto = drought_change[season].autocorr(lag=1)
    print(f"\nweekly drought changes are autocorrelated (lag-1 r = {auto:+.2f}), so estimate all leads and lags jointly:")
    design = pd.DataFrame({f"j={j:+d}": drought_change.shift(-j) for j in range(3, -4, -1)})
    design["return"] = weekly_return
    design = design[season].dropna()
    everything = joint_t_stats(design)
    without_2012 = joint_t_stats(design[design.index.year != 2012])
    print("  this week's corn return regressed on corn-belt drought changes published j weeks away")
    print(f"  {'':6s} {'all years (n=%d)' % len(design):>22s} {'excluding 2012 (n=%d)' % (design.index.year != 2012).sum():>26s}")
    for column in design.columns.drop("return"):
        j = int(column.split("=")[1])
        meaning = "published later (price first)" if j > 0 else ("same week" if j == 0 else "published earlier (data first)")
        a, b = everything[column], without_2012[column]
        pa, pb = (math.erfc(abs(v) / math.sqrt(2)) for v in (a, b))
        print(f"    {column}  t = {a:+.2f} {stars(pa):3s}            t = {b:+.2f} {stars(pb):3s}      {meaning}")

    # The single clearest case: the 2012 drought, week by week in Iowa.
    iowa = state_drought["Iowa"]
    summer_2012 = corn[(corn.index >= "2012-06-01") & (corn.index <= "2012-09-30")]
    base = summer_2012.iloc[0]
    half_done = summer_2012[summer_2012 - base >= 0.5 * (summer_2012.max() - base)].index[0]
    iowa_2012 = iowa[(iowa.index >= "2012-06-01") & (iowa.index <= "2012-09-30")]
    print("\n  2012, week by week in Iowa:")
    print(f"    corn was {corn.asof(pd.Timestamp('2012-07-03')) / base - 1:+.0%} since June 1 by July 3, "
          f"when {iowa.asof(pd.Timestamp('2012-07-03')):.0f}% of Iowa was in severe drought")
    print(f"    half the summer rally was complete by {half_done.date()}; "
          f"the peak came {summer_2012.idxmax().date()} at {summer_2012.max() / base - 1:+.0%}")
    print(f"    first week with half of Iowa in severe drought: {iowa_2012[iowa_2012 >= 50].index[0].date()}")

    # ---- 3. Validation -----------------------------------------------------
    print("\n" + "=" * 88)
    print("3. VALIDATION  Does the weather index explain actual US corn yield shortfalls?")
    print("=" * 88)
    maize = yields("maize-yields", "United States")
    maize = maize[(maize.index >= 2000) & (maize.index <= 2025)]
    shortfall = detrended_pct(maize)
    print(f"US corn yields (FAO via Our World in Data): {maize.index.min()}-{maize.index.max()}")
    for label, series in [("stress index", stress), ("July heat", july_heat), ("summer rain", summer_rain),
                          ("peak drought", summer_drought)]:
        r, n, p = correlate(series, shortfall)
        print(f"  {label:13s} vs yield vs trend: r = {r:+.2f} (n={n}, p={p:.3f}) {stars(p)}")
    r_ex, n_ex, p_ex = correlate(stress.drop(2012), shortfall.drop(2012))
    r_heat, _, p_heat = correlate(july_heat.drop(2012), shortfall.drop(2012))
    print(f"  ...excluding 2012: stress index r = {r_ex:+.2f} (p={p_ex:.3f}) {stars(p_ex)} | "
          f"July heat r = {r_heat:+.2f} (p={p_heat:.3f}) {stars(p_heat)}")
    worst = shortfall.sort_values().head(3)
    print("  largest shortfalls vs trend: " + ", ".join(f"{y} {v:+.0%}" for y, v in worst.items()))

    # ---- 4. Contrast -------------------------------------------------------
    print("\n" + "=" * 88)
    print("4. CONTRAST  Does cocoa price West African rainfall more slowly?")
    print("=" * 88)
    cocoa_weather = {name: weather(name, lat, lon) for name, (lat, lon) in COCOA_BELT.items()}
    rain = pd.concat({n: f["precip"] for n, f in cocoa_weather.items()}, axis=1).mean(axis=1)
    monthly_rain = rain.resample("MS").sum()
    by_month = monthly_rain.groupby(monthly_rain.index.month)
    anomaly = (monthly_rain - by_month.transform("mean")) / by_month.transform("std")
    rain_6m = anomaly.rolling(6).mean()
    cocoa = prices("CC=F")
    month_end = cocoa.resample("MS").last()
    print("correlation of the six-month rainfall anomaly with cocoa returns (monthly, 2000-2025)")
    print("(overlapping windows overstate significance -- read these as direction, not proof)\n")
    for label, horizon in [("past 6 months", -6), ("past 3 months", -3), ("next 3 months", 3),
                           ("next 6 months", 6), ("next 12 months", 12)]:
        if horizon < 0:
            ret = np.log(month_end / month_end.shift(-horizon))
        else:
            ret = np.log(month_end.shift(-horizon) / month_end)
        r, n, _ = correlate(rain_6m, ret)
        print(f"  {label:15s} r = {r:+.2f} (n={n})")

    annual_rain = anomaly.groupby(anomaly.index.year).mean()
    for country in ["Cote d", "Ghana"]:
        cocoa_yield = yields("cocoa-bean-yields", country)
        cocoa_yield = cocoa_yield[(cocoa_yield.index >= 2000) & (cocoa_yield.index <= 2025)]
        deviation = detrended_pct(cocoa_yield)
        same, _, p1 = correlate(annual_rain, deviation)
        lagged, _, p2 = correlate(annual_rain.shift(1), deviation)
        name = "Cote d'Ivoire" if country.startswith("Cote") else country
        print(f"  {name} cocoa yield vs trend: same-year rain r = {same:+.2f} (p={p1:.2f}) | "
              f"prior-year rain r = {lagged:+.2f} (p={p2:.2f}) | years {cocoa_yield.index.min()}-{cocoa_yield.index.max()}")

    print("\nsignificance: *** p<0.01  ** p<0.05  * p<0.10")


if __name__ == "__main__":
    main()
