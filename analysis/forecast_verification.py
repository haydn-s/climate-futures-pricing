"""Did the forecasts see 2012 coming?

Run from the repository root, after fetch and clean:

    PYTHONPATH=src python analysis/forecast_verification.py

The belt-mean July forecast anomaly puts 2012 at about +10 while 2022 reads
+21 and 2014 reads -13. 2014 being coolest and 2022 hottest is plausible, but
2012 was the worst drought in the sample and ranking it mid-pack is not
obviously right -- so either the CPC probability anomaly is a weak proxy for
heat, or the forecasts genuinely under-anticipated that summer. Those are very
different findings and the question matters: "the market already knew" is a much
weaker claim if the public forecasts did not know either.

This is forecast verification, not a price study. Every forecast is compared
against the weather that actually arrived IN ITS OWN VALID WINDOW -- the 6-10 or
8-14 day period it was issued about -- using NASA POWER at the same five points
the forecast is read at, so nothing is compared across geographies.

TWO SCALES THAT DO NOT SHARE UNITS. The forecast anomaly is in percentage points
of probability above the climatological third; the realised anomaly is in degrees
Celsius. They cannot be subtracted, so they are compared by rank and by
correlation, and both are standardised before anything is plotted against the
other. A regression of one on the other in raw units would produce a slope with
no meaning.

Climatology is the same calendar window in STRICTLY EARLIER years, which is the
same rule features.panel uses, so no year is judged against a normal that
includes itself. The forecast record starts in April 2012 and POWER reaches back
to 2000, so every verification year has at least a decade of prior years behind
its baseline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.features.panel import HEAT_THRESHOLD_C  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

CROP = "corn"
# The two temperature horizons and the length of the window each one describes:
# 610 covers days 6-10 after issuance (five days), 814 covers 8-14 (seven).
PRODUCTS = {"610temp": 5, "814temp": 7}
PEAK_MONTHS = (7, 8)


def realised(power: pd.DataFrame, point: str, length: int) -> pd.DataFrame:
    """Mean tmax over the `length` days starting at each date, for one point.

    Forward-looking on purpose: a forecast issued for days 6-10 is verified
    against the mean of exactly those days, so the window is anchored at
    valid_start and runs forward.
    """
    series = (power.loc[power["point"] == point, ["date", "tmax_c"]]
              .dropna().sort_values("date", ignore_index=True).set_index("date"))
    # reversed rolling == forward-looking window
    mean_tmax = (series["tmax_c"][::-1].rolling(length, min_periods=length).mean())[::-1]
    # TWO MEASURES, because they do not rank the same years. Mean tmax dilutes
    # exactly the extremes that kill a corn crop: it put 2012 at +0.27 C against
    # 2022's +2.29, which cannot be right for the worst drought in the sample.
    # Degree-days above 29 C is the measure that correlates -0.87 with yield
    # shortfalls (analysis/corn_yield_model.py), so it is the crop-relevant one
    # and the one the year table is ranked on.
    excess = (series["tmax_c"] - HEAT_THRESHOLD_C).clip(lower=0.0)
    heat_dd = (excess[::-1].rolling(length, min_periods=length).sum())[::-1]
    return pd.DataFrame({"valid_start": series.index,
                         "realised_tmax": mean_tmax.to_numpy(),
                         "realised_dd": heat_dd.to_numpy()})


def prior_year_anomaly(frame: pd.DataFrame, column: str) -> pd.Series:
    """Departure from the same calendar window in strictly earlier years."""
    work = frame.copy()
    work["month_day"] = work["valid_start"].dt.month * 100 + work["valid_start"].dt.day
    work["year"] = work["valid_start"].dt.year
    work = work.sort_values(["point", "month_day", "year"])
    mean = (work.groupby(["point", "month_day"], sort=False)[column]
            .transform(lambda s: s.expanding().mean().shift(1)))
    return (work[column] - mean).reindex(frame.index)


def main() -> None:
    store = ProcessedStore(default_data_root())
    forecasts = store.read("cpc_outlook_point_daily")
    power = store.read("nasa_power_point_daily")
    power = power.loc[power["crop"] == CROP]

    forecasts = forecasts.loc[(forecasts["crop"] == CROP)
                              & forecasts["product"].isin(PRODUCTS)]
    if forecasts.empty:
        raise SystemExit("no corn temperature forecasts in the table; clean cpc_outlook first")

    print(__doc__.splitlines()[0])
    print(f"\n{len(forecasts)} forecasts at {forecasts['point'].nunique()} points, "
          f"{forecasts['issued'].min().date()}..{forecasts['issued'].max().date()}")

    merged = []
    for product, length in PRODUCTS.items():
        rows = forecasts.loc[forecasts["product"] == product]
        for point in sorted(rows["point"].unique()):
            actual = realised(power, point, length)
            part = (rows.loc[rows["point"] == point]
                    .merge(actual, on="valid_start", how="left"))
            merged.append(part)
    table = pd.concat(merged, ignore_index=True).dropna(subset=["realised_tmax"])
    table["realised_anomaly"] = prior_year_anomaly(table, "realised_tmax")
    table["dd_anomaly"] = prior_year_anomaly(table, "realised_dd")
    table = table.dropna(subset=["realised_anomaly", "dd_anomaly"])
    table["year"] = table["issued"].dt.year

    print(f"{len(table)} verified against POWER at the same point "
          f"({table['year'].min()}..{table['year'].max()})")

    # Does the forecast anomaly track the heat that arrived? One number per
    # forecast, pooled, then standardised so the two scales are comparable.
    peak = table.loc[table["issued"].dt.month.isin(PEAK_MONTHS)]
    print(f"\n{'measure':24s} {'all forecasts':>14s} {'Jul-Aug':>10s}")
    for label, column in (("mean tmax", "realised_anomaly"),
                          ("degree-days above 29C", "dd_anomaly")):
        print(f"  {label:22s} r = {table['anomaly'].corr(table[column]):+.3f}   "
              f"r = {peak['anomaly'].corr(peak[column]):+.3f}")
    print(f"  (n = {len(table)} all, {len(peak)} peak)")

    print("\nPEAK SEASON BY YEAR  (July-August, both horizons, five corn points)")
    print("  realised is degree-days above 29C in each forecast's own valid window")
    print(f"  {'year':>5s} {'forecast pp':>12s} {'realised dd':>12s} "
          f"{'fc rank':>8s} {'real rank':>10s} {'gap':>6s}")
    by_year = peak.groupby("year").agg(forecast=("anomaly", "mean"),
                                       realised=("dd_anomaly", "mean"),
                                       n=("anomaly", "size"))
    # Standardised so the two scales can be differenced at all; the gap is in
    # standard deviations, positive meaning the heat beat the forecast.
    for column in ("forecast", "realised"):
        by_year[f"z_{column}"] = ((by_year[column] - by_year[column].mean())
                                  / by_year[column].std())
    by_year["gap"] = by_year["z_realised"] - by_year["z_forecast"]
    by_year["fc_rank"] = by_year["forecast"].rank(ascending=False).astype(int)
    by_year["real_rank"] = by_year["realised"].rank(ascending=False).astype(int)
    total = len(by_year)
    for year, row in by_year.iterrows():
        flag = "  <-- 2012" if year == 2012 else ""
        print(f"  {year:5d} {row['forecast']:+11.2f} {row['realised']:+10.2f} "
              f"{int(row['fc_rank']):4d}/{total:<3d} {int(row['real_rank']):5d}/{total:<3d} "
              f"{row['gap']:+6.2f}{flag}")

    print("\n" + "-" * 70)
    if 2012 in by_year.index:
        row = by_year.loc[2012]
        print(f"2012: forecast ranked {int(row['fc_rank'])} of {total} for heat, "
              f"the weather itself ranked {int(row['real_rank'])}.")
        print(f"      gap {row['gap']:+.2f} standard deviations "
              f"({'heat beat the forecast' if row['gap'] > 0 else 'forecast ran ahead of the heat'})")
        biggest = by_year["gap"].idxmax()
        print(f"      largest under-forecast in the sample: {biggest} "
              f"({by_year.loc[biggest, 'gap']:+.2f})")
    print("\nReading this: a positive r means the forecasts carry real information")
    print("about the heat that followed. A large positive gap for 2012 would mean the")
    print("public forecasts understated that summer -- which weakens 'the market")
    print("already knew', because the market's public information did not know either.")


if __name__ == "__main__":
    main()
