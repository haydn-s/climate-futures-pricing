"""Chart data for the proposal deck, computed from the analysis script's cached public data."""
import importlib.util, json, math, sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ia", REPO / "analysis" / "initial_analysis.py")
ia = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ia)

corn_weather = {n: ia.weather(n, lat, lon) for n, (lat, lon, _) in ia.CORN_BELT.items()}
tmax = pd.concat({n: f["tmax"] for n, f in corn_weather.items()}, axis=1).mean(axis=1)
precip = pd.concat({n: f["precip"] for n, f in corn_weather.items()}, axis=1).mean(axis=1)
state_drought = {n: ia.drought(n, fips) for n, (_, _, fips) in ia.CORN_BELT.items()}
drought_weekly = pd.concat(state_drought, axis=1).mean(axis=1)
corn = ia.prices("ZC=F")

july_heat = pd.Series({y: tmax[(tmax.index.year == y) & (tmax.index.month == 7)].mean() for y in ia.YEARS})
summer_rain = pd.Series({y: precip[(precip.index.year == y) & precip.index.month.isin([6, 7, 8])].sum() for y in ia.YEARS})
summer_drought = pd.Series({y: drought_weekly[(drought_weekly.index.year == y) & drought_weekly.index.month.isin([6, 7, 8])].max() for y in ia.YEARS})
stress = (ia.zscore(july_heat) - ia.zscore(summer_rain) + ia.zscore(summer_drought)) / 3

maize = ia.yields("maize-yields", "United States")
maize = maize[(maize.index >= 2000) & (maize.index <= 2025)]
shortfall = ia.detrended_pct(maize)

out = {}

# Measurement: stress index vs yield deviation
r_all, n_all, p_all = ia.correlate(stress, shortfall)
r_ex, n_ex, p_ex = ia.correlate(stress.drop(2012), shortfall.drop(2012))
r_heat, _, p_heat = ia.correlate(july_heat, shortfall)
points = sorted(((float(stress[y]), float(shortfall[y]) * 100, int(y)) for y in ia.YEARS if y in shortfall.index), key=lambda t: t[0])
out["scatter"] = {"x": [round(p[0], 3) for p in points], "y": [round(p[1], 2) for p in points], "year": [p[2] for p in points]}
out["measurement"] = {"r_all": r_all, "n_all": n_all, "p_all": p_all, "r_ex2012": r_ex, "p_ex2012": p_ex, "r_heat": r_heat, "p_heat": p_heat,
                      "worst": [[int(y), round(float(v) * 100, 1)] for y, v in shortfall.sort_values().head(3).items()]}

# Annual price link
def summer_return(series, year):
    w = series[(series.index >= f"{year}-06-01") & (series.index <= f"{year}-08-31")]
    return float(np.log(w.iloc[-1] / w.iloc[0])) if len(w) > 40 else float("nan")
corn_summer = pd.Series({y: summer_return(corn, y) for y in ia.YEARS})
etf = ia.prices("CORN")
etf_summer = pd.Series({y: summer_return(etf, y) for y in ia.YEARS})
out["annual"] = {"r_corn": ia.correlate(stress, corn_summer)[0], "r_corn_ex2012": ia.correlate(stress.drop(2012), corn_summer.drop(2012))[0],
                 "r_fund": ia.correlate(stress, etf_summer)[0], "r_fund_ex2012": ia.correlate(stress.drop(2012), etf_summer.drop(2012))[0],
                 "corn_2023_pct": float(np.expm1(corn_summer[2023]) * 100)}

# Timing: lead-lag correlations
release = drought_weekly.index + pd.Timedelta(days=2)
price_at_release = corn.reindex(corn.index.union(release)).ffill().reindex(release)
weekly_return = pd.Series(np.log(price_at_release.values), index=drought_weekly.index).diff()
drought_change = drought_weekly.diff()
season = drought_weekly.index.month.isin([5, 6, 7, 8, 9])
lags = []
for k in range(-3, 4):
    r, n, p = ia.correlate(drought_change[season], weekly_return.shift(-k)[season])
    lags.append({"k": k, "r": r, "n": n, "p": p, "stars": ia.stars(p)})
out["lags"] = lags

# 2012 week by week in Iowa, on each Drought Monitor release date (Thursday)
iowa = state_drought["Iowa"]
summer_2012 = corn[(corn.index >= "2012-06-01") & (corn.index <= "2012-09-30")]
base = summer_2012.iloc[0]
maps = iowa[(iowa.index >= "2012-06-01") & (iowa.index <= "2012-09-30")]
weeks = []
for map_date, d2 in maps.items():
    rel = map_date + pd.Timedelta(days=2)
    weeks.append({"map": str(map_date.date()), "release": str(rel.date()), "label": rel.strftime("%b %-d"),
                  "corn_pct": round(float(corn.asof(rel) / base - 1) * 100, 1), "iowa_d2": round(float(d2), 1)})
# Same weeks on the map's valid date (Tuesday), with corn at that day's close
weeks_map = []
for map_date, d2 in maps.items():
    weeks_map.append({"map": str(map_date.date()), "label": map_date.strftime("%b %-d"),
                      "corn_pct": round(float(corn.asof(map_date) / base - 1) * 100, 1), "iowa_d2": round(float(d2), 1)})
half_done = summer_2012[summer_2012 - base >= 0.5 * (summer_2012.max() - base)].index[0]
out["y2012"] = {"weeks": weeks, "weeks_map": weeks_map, "base_date": str(summer_2012.index[0].date()), "base_price": float(base) / 100,
                "pct_jul3": float(corn.asof(pd.Timestamp("2012-07-03")) / base - 1) * 100, "iowa_jul3": float(iowa.asof(pd.Timestamp("2012-07-03"))),
                "half_done": str(half_done.date()), "peak_date": str(summer_2012.idxmax().date()), "peak_pct": float(summer_2012.max() / base - 1) * 100,
                "peak_price": float(summer_2012.max()) / 100,
                "first_half_iowa_map": str(maps[maps >= 50].index[0].date())}

(Path(__file__).parent / "chart_data.json").write_text(json.dumps(out, indent=1))
m = out["measurement"]; a = out["annual"]; t = out["y2012"]
print(f"measurement r={m['r_all']:+.2f} (p={m['p_all']:.4f}) ex2012={m['r_ex2012']:+.2f} (p={m['p_ex2012']:.3f}) heat={m['r_heat']:+.2f} worst={m['worst']}")
print(f"annual corn {a['r_corn']:+.2f} -> {a['r_corn_ex2012']:+.2f} | fund {a['r_fund']:+.2f} -> {a['r_fund_ex2012']:+.2f} | 2023 corn {a['corn_2023_pct']:+.0f}%")
print("lags", [(l['k'], round(l['r'], 3), l['stars']) for l in lags])
print(f"2012: base {t['base_date']} ${t['base_price']:.2f}; +{t['pct_jul3']:.0f}% by Jul 3 with Iowa {t['iowa_jul3']:.0f}%; half done {t['half_done']}; peak {t['peak_date']} +{t['peak_pct']:.0f}% ${t['peak_price']:.2f}; first map >=50%: {t['first_half_iowa_map']}")
print("weeks_map", [(w['label'], w['corn_pct'], w['iowa_d2']) for w in weeks_map])
print("scatter 2012 point", [(x, y) for x, y, yr in zip(out['scatter']['x'], out['scatter']['y'], out['scatter']['year']) if yr == 2012])

# Confirm the other futures in the README's data table return daily history from 2000
for sym in ["ZS=F", "ZW=F", "KC=F", "OJ=F"]:
    s = ia.prices(sym)
    gaps = s.index.to_series().diff().dt.days
    print(f"{sym}: {s.index.min().date()} -> {s.index.max().date()}, {len(s)} bars, median gap {gaps.median():.0f} day(s)")
