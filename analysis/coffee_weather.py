"""Does frost explain arabica? Coffee, where the weather event is a cold night.

Run from the repository root, after fetch and clean:

    PYTHONPATH=src python analysis/coffee_weather.py

The third crop, and structurally the opposite of cocoa in two ways that decide
how it has to be analysed.

NOT A BLOC. Cocoa's five West African producers are contiguous, so a bean
smuggled across an internal border cancels in their total and aggregating
RECOVERS the weather. Arabica's producers sit on three continents with
independent weather, so aggregating them averages unrelated signals and DILUTES
whatever is there. Brazil alone is 30% of world coffee and dominates arabica
price formation, so this is Brazil-centred and the bloc is the control rather
than the headline -- if the pooled result beats Brazil's, something is wrong
with the reasoning rather than right with the pooling.

THE EVENT IS A MINIMUM, NOT A MAXIMUM. Every other crop here is hurt by heat and
drought. Arabica's signature disaster is frost: a cold night in the Brazilian
winter scorches leaves and kills branches, and because the tree is a perennial
the damage lands on the FOLLOWING season's crop. That is why NASA POWER now
fetches T2M_MIN, and why the frost window is read one year ahead of the harvest
it should explain.

July 2021 is the clean case. tmin reached 4.44 C at a Minas Gerais point on
2021-07-20 and KC=F went from a June mean of 156 to 235 by December, then
averaged 214 through 2022 -- the price responding across the turn of the year,
which is what a lagged supply shock on a perennial looks like.

WHAT WOULD MAKE THIS A NULL, stated in advance so it is not rationalised after:
frost_days and min_tmin in the winter window, lagged one year, should correlate
with the following harvest's shortfall. If they do not, either three points
cannot represent Brazil's coffee belt, or a screen-height reanalysis cannot see
leaf-level frost, or FAO's national yield is too coarse to carry a regional
event. All three are live possibilities and none of them is "frost does not
matter", which is not in question.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.config import load_geography  # noqa: E402
from pipeline.features import windows  # noqa: E402
from pipeline.features.annual import detrend_pct  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

CROP = "coffee"
LEAD = "Brazil"

# Brazil's arabica calendar. Flowering and cherry fill drive the crop of the
# season they fall in; the winter frost window damages the NEXT one.
WINDOWS = {
    "winter FROST Jun-Aug": windows.months(start=6, end=8),
    "flowering Sep-Oct": windows.months(start=9, end=10),
    "cherry fill Nov-Feb": windows.months(start=11, end=2),
    "harvest May-Aug": windows.months(start=5, end=8),
}
FROST_FIRST = ("frost_days", "min_tmin", "max_dry_spell", "rain_mm", "heat_dd")


def correlate(x: pd.Series, y: pd.Series) -> tuple[float, int, float]:
    pair = pd.concat([x, y], axis=1).dropna()
    if len(pair) < 8:
        return float("nan"), len(pair), float("nan")
    # A constant series has no correlation to report, and asking numpy produces a
    # divide-by-zero warning and a NaN rather than saying so.
    if pair.iloc[:, 0].std() == 0 or pair.iloc[:, 1].std() == 0:
        return float("nan"), len(pair), float("nan")
    r = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
    t = r * math.sqrt((len(pair) - 2) / max(1e-12, 1 - r * r))
    return r, len(pair), math.erfc(abs(t) / math.sqrt(2))


def stars(p: float) -> str:
    if np.isnan(p):
        return ""
    return "***" if p < 0.01 else "**" if p < 0.05 else "*" if p < 0.10 else ""


def country_frames(power: pd.DataFrame, geography, country: str) -> dict[str, pd.DataFrame]:
    """One daily frame PER POINT for a country, not an average of them.

    Kept separate on purpose. features.windows.across_points combines a frost by
    taking the coldest point and a dry spell by taking the worst, which cannot be
    recovered once the points have been averaged into one series -- averaging
    Brazil's three arabica points first made frost_days a constant zero over 27
    years while the coldest point crossed the threshold in six of them.
    """
    rows = power.loc[(power["crop"] == CROP) & (power["country"] == country)]
    frames = {}
    for point, group in rows.groupby("point"):
        columns = ["date", "tmax_c", "precip_mm"]
        if "tmin_c" in group.columns:
            columns.append("tmin_c")
        frames[str(point)] = group[columns].sort_values("date", ignore_index=True)
    return frames


def shortfall_of(yields: pd.DataFrame, entity: str, first_year: int,
                 column: str = "yield_t_per_ha") -> pd.Series:
    """Deviation from trend for one country, of yield or of production.

    Production matters as well as yield because a frost kills trees, which
    removes them from harvested area too -- so a ratio could in principle hold
    while both collapse. Checked, and it does not here: area moves by a standard
    deviation of 0.035 and production and yield give the same correlation, so
    that mechanism is not what hides the frost.
    """
    series = (yields.loc[(yields["entity"] == entity) & (yields["year"] >= first_year)]
              .set_index("year")[column].sort_index().astype(float))
    return detrend_pct(series) if len(series) >= 3 else pd.Series(dtype="float64")


def shortfall(yields: pd.DataFrame, entity: str, first_year: int) -> pd.Series:
    return shortfall_of(yields, entity, first_year)


def main() -> None:
    store = ProcessedStore(default_data_root())
    geography = load_geography()
    power = store.read("nasa_power_point_daily")
    yields = store.read("owid_yields_annual")
    coffee = yields.loc[yields["crop"] == CROP]
    if coffee.empty:
        raise SystemExit("no coffee yields; fetch and clean owid_yields")

    rows = power.loc[power["crop"] == CROP]
    if rows.empty:
        raise SystemExit("no coffee weather in nasa_power_point_daily; "
                         "fetch nasa_power (the points are new)")
    has_tmin = "tmin_c" in power.columns and bool(rows["tmin_c"].notna().any())

    print(__doc__.splitlines()[0])
    print(f"\nweather: {rows['point'].nunique()} points, "
          f"{rows['date'].min().date()}..{rows['date'].max().date()}")
    print(f"tmin available: {has_tmin}"
          + ("" if has_tmin else "  <- frost cannot be measured; re-fetch "
                                 "nasa_power --force after adding T2M_MIN"))

    first_year = int(rows["date"].dt.year.min())
    years = range(first_year + 1, int(rows["date"].dt.year.max()) + 1)
    brazil = country_frames(power, geography, LEAD)
    target = shortfall(coffee, LEAD, first_year)
    print(f"{LEAD}: {len(brazil)} point(s), yield shortfall "
          f"{int(target.dropna().index.min())}..{int(target.dropna().index.max())}, "
          f"sd {target.std():.3f}")
    worst = target.dropna().nsmallest(4)
    print("  worst years: " + ", ".join(f"{int(y)} {v:+.0%}" for y, v in worst.items()))

    print(f"\n{LEAD.upper()} SHORTFALL vs ITS OWN WEATHER")
    print("  lagged = weather of year Y against the harvest reported for Y+1, which is")
    print("  the alignment a frost needs: the tree is damaged now and bears less next")
    print(f"\n  {'window':24s} {'measure':15s} {'same yr':>9s} {'':3s} {'lagged':>9s}")
    tests = 0
    for name, calendar in WINDOWS.items():
        table = windows.table_across_points(
            brazil, calendar, years,
            heat_threshold=windows.HEAT_THRESHOLD_COFFEE,
            frost_threshold=windows.FROST_THRESHOLD_COFFEE)
        if table.empty:
            print(f"  {name:24s} -- no covered years")
            continue
        for measure in FROST_FIRST:
            if measure not in table.columns or table[measure].notna().sum() < 8:
                print(f"  {name:24s} {measure:15s} -- not measurable")
                continue
            same_r, n, same_p = correlate(table[measure], target)
            lagged = table[measure].copy()
            lagged.index = lagged.index + 1
            lag_r, _, lag_p = correlate(lagged, target)
            tests += 2
            mark = "  <--" if (name.startswith("winter") and measure in
                               ("frost_days", "min_tmin")) else ""
            print(f"  {name:24s} {measure:15s} {same_r:+8.2f} {stars(same_p):3s} "
                  f"{lag_r:+8.2f} {stars(lag_p):3s}  n={n}{mark}")
    print(f"\n  {tests} tests, about {tests * 0.05:.0f} significant by chance.")
    print("  The two marked rows are the pre-specified hypothesis; the rest is context.")

    # ------------------------------------------------------- the 2021 frost itself
    print("\n" + "=" * 72)
    print("JULY 2021: the frost, and what the price did")
    if has_tmin:
        winter = windows.table_across_points(
            brazil, WINDOWS["winter FROST Jun-Aug"], years,
            heat_threshold=windows.HEAT_THRESHOLD_COFFEE,
            frost_threshold=windows.FROST_THRESHOLD_COFFEE)
        coldest = winter["min_tmin"].dropna().nsmallest(5)
        print("  coldest Brazilian winters in the record, by lowest nightly minimum:")
        for year, value in coldest.items():
            flag = "  <-- the 2021 frost" if int(year) == 2021 else ""
            frost = winter.loc[year, "frost_days"]
            print(f"    {int(year)}  min tmin {value:5.2f} C, "
                  f"{frost:.0f} night(s) below {windows.FROST_THRESHOLD_COFFEE} C{flag}")
        rank = int(winter["min_tmin"].rank().loc[2021]) if 2021 in winter.index else None
        if rank:
            print(f"  2021 ranks {rank} of {int(winter['min_tmin'].notna().sum())} "
                  f"for cold, where 1 is coldest")
    prices = store.read("yahoo_prices_daily")
    kc = prices.loc[prices["series"] == CROP].set_index("date")["close"]
    annual = kc.groupby(kc.index.year).mean()
    print("\n  KC=F annual mean, around the frost:")
    for year in (2019, 2020, 2021, 2022, 2023):
        if year in annual.index:
            print(f"    {year}: {annual.loc[year]:6.1f}")

    # ------------------------------------------------- what the target is made of
    print("\n" + "=" * 72)
    print("WHY THE FROST DOES NOT SHOW: what the yield statistic is actually made of")
    production = shortfall_of(coffee, LEAD, first_year, "production_t")
    cycle = pd.DataFrame({"shortfall": production})
    cycle["prev"] = cycle["shortfall"].shift(1)
    lagged_frost = {}
    winter_table = windows.table_across_points(
        brazil, WINDOWS["winter FROST Jun-Aug"], years,
        heat_threshold=windows.HEAT_THRESHOLD_COFFEE,
        frost_threshold=windows.FROST_THRESHOLD_COFFEE)
    for measure in ("min_tmin", "frost_days"):
        series = winter_table[measure].copy()
        series.index = series.index + 1
        cycle[measure] = series
    cycle = cycle.dropna()

    auto1 = production.dropna().autocorr(1)
    auto2 = production.dropna().autocorr(2)
    print(f"\n  BIENNIAL BEARING. Arabica alternates a heavy crop with a light one --")
    print(f"  the tree exhausts itself bearing and then rests -- and that shows up as")
    print(f"  lag-1 autocorrelation {auto1:+.3f} with lag-2 {auto2:+.3f} in detrended")
    print(f"  Brazilian production. It is plant physiology, not weather, and it is the")
    print(f"  dominant signal in the series a weather index has to compete against.")
    print(f"\n  {'model':44s} {'R2':>7s}   terms")
    for columns in (["min_tmin"], ["prev"], ["prev", "min_tmin"],
                    ["prev", "min_tmin", "frost_days"]):
        matrix = np.column_stack([np.ones(len(cycle))]
                                 + [cycle[c].to_numpy() for c in columns])
        target_values = cycle["shortfall"].to_numpy()
        beta, *_ = np.linalg.lstsq(matrix, target_values, rcond=None)
        residual = target_values - matrix @ beta
        dof = len(target_values) - matrix.shape[1]
        errors = np.sqrt(np.diag((residual @ residual / dof)
                                 * np.linalg.pinv(matrix.T @ matrix)))
        r2 = 1 - residual @ residual / ((target_values - target_values.mean()) ** 2).sum()
        terms = "  ".join(f"{c} t={beta[i + 1] / errors[i + 1]:+.2f}"
                          for i, c in enumerate(columns))
        print(f"  {' + '.join(columns):44s} {r2:+7.3f}   {terms}")
    print("\n  So the cycle carries the series and the frost adds almost nothing on top")
    print("  of it, with the right sign and no significance. Two reasons it can be real")
    print("  and still invisible here: FAO's Brazilian series is COFFEE, GREEN -- it")
    print("  aggregates arabica with the robusta grown in Espirito Santo and Bahia,")
    print("  which a Minas frost does not touch, while KC=F settles arabica alone. And")
    print("  a national total averages a frost that was regional. The frost is in the")
    print("  weather data and in the price; it is the STATISTIC that cannot see it.")

    # ------------------------------------------------------------- the bloc control
    print("\n" + "=" * 72)
    print("THE BLOC AS A CONTROL: pooling should make this WORSE, not better")
    shares = (coffee.loc[coffee["production_t"].notna()]
              .groupby("entity")["production_t"].mean())
    shares = (shares / shares.sum()).sort_values(ascending=False)
    print("  long-run production shares: "
          + ", ".join(f"{c} {v:.0%}" for c, v in shares.items()))
    print(f"\n  {'country':12s} {'share':>6s} {'winter min_tmin, lagged':>26s}")
    for country, share in shares.items():
        daily = country_frames(power, geography, country)
        if not daily:
            print(f"  {country:12s} {share:5.0%}   no points configured")
            continue
        table = windows.table_across_points(
            daily, WINDOWS["winter FROST Jun-Aug"], years,
            heat_threshold=windows.HEAT_THRESHOLD_COFFEE,
            frost_threshold=windows.FROST_THRESHOLD_COFFEE)
        own = shortfall(coffee, country, first_year)
        if table.empty or "min_tmin" not in table.columns or own.empty:
            print(f"  {country:12s} {share:5.0%}   not measurable")
            continue
        lagged = table["min_tmin"].copy()
        lagged.index = lagged.index + 1
        r, n, p = correlate(lagged, own)
        hemisphere = "southern" if country in ("Brazil", "Peru") else "northern"
        print(f"  {country:12s} {share:5.0%}   r {r:+.2f} {stars(p):3s} n={n:2d}  "
              f"({hemisphere} -- a June-August window is winter only in the south)")

    print("\n" + "-" * 72)
    print("Reading this: a northern-hemisphere country's June-August window is its")
    print("GROWING season, not its winter, so a frost measure there is meaningless and")
    print("is printed to show the pooling is unsound rather than to be interpreted.")
    print("That is the whole reason coffee is analysed Brazil-first while cocoa was")
    print("analysed bloc-first: the right aggregation follows the geography, and for")
    print("arabica there is no aggregation that shares a season.")


if __name__ == "__main__":
    main()
