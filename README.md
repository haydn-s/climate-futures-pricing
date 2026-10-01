# Climate Futures Pricing

**Does the futures market price weather before the data does?**

Duke AIPI 590 — Alternative Data

---

## The problem

Severe weather breaks physical supply chains. Crops fail in heat and drought, rivers run too low for barges, and storms close ports. Those disruptions reach commodity futures, food company costs and grocery prices.

Weather data is free, global and decades deep, which makes it an obvious candidate for alternative data. Two things make it harder to use than it looks:

- **Exposure is geographic, but supply chains are opaque.** Companies rarely publish where their supply comes from, so exposure has to be mapped from where commodities are *produced*.
- **Markets trade forecasts, not recorded weather.** Observations are published days to weeks after the fact, while futures prices respond to forecasts continuously.

This project measures weather stress on agricultural supply from public data, validates it against actual harvests, and tests when — and whether — futures prices respond.

## Research questions

**Core**

1. **Can free climate data, mapped to where crops grow, measure supply shocks?** Validated against actual crop yields.
2. **Do futures prices respond to weather stress — and before or after the data is published?**
3. **Does a climate risk score tell the market anything it does not already know?**

**Exploratory — as the timeline allows**

4. **Do slower-moving tree crops price weather more slowly?** Cocoa and coffee, where damage surfaces in harvests months later.
5. **Does the effect reach agribusiness stocks?**

## Preliminary evidence

Everything below is reproduced by `python analysis/initial_analysis.py` from free public data. Scope is deliberately narrow: US corn, one representative point in each of five major corn-producing states, equally weighted, 2000–2025. **These are previews, not results.**

### 1. The measurement works

A simple stress index — July heat, summer rainfall and peak drought coverage across the corn belt — tracks US corn yield shortfalls closely.

| Weather measure | Correlation with yield vs trend | Excluding 2012 |
|---|---|---|
| Stress index | −0.73 *** | −0.43 ** |
| July maximum temperature | −0.78 *** | −0.57 *** |
| Peak severe drought | −0.69 *** | |
| Summer rainfall | +0.49 *** | |

The largest shortfalls were 2012 (−22%), 2002 (−8%) and 2011 (−6%). Heat matters most, consistent with the established finding that corn yields fall sharply above 29 °C (Schlenker & Roberts, 2009). The relationship survives removing 2012, the most extreme year.

### 2. The annual price link rests on one year

Across all years, more stressful summers line up with summer corn rallies: r = +0.42 (p = 0.03), and +0.57 on a fund whose price does not jump at contract rolls. **Excluding 2012, both fall to near zero** — r = +0.07 and +0.04, neither significant. In 2023, the second most stressful summer on the index, corn fell 22%.

### 3. Prices move before the drought data

Week by week through the growing season, corn returns line up with drought increases published one to three weeks *later*, rather than earlier.

| Corn return, relative to publication of the drought change | Correlation |
|---|---|
| 3 weeks before | +0.07 * |
| 2 weeks before | +0.14 *** |
| 1 week before | +0.10 ** |
| Publication week | +0.00 |
| 1 week after | +0.07 |
| 2 weeks after | +0.07 * |

Drought builds over several weeks, which can make prices appear to lead by accident, so every lead and lag was also estimated jointly. Returns stay significant one to two weeks before publication, nothing after publication is significant, and the pattern holds with 2012 excluded.

2012 shows it plainly. Corn was up 30% by July 3, when 1% of Iowa was in severe drought. Half of its summer rally was complete by July 2 — two weeks before the Drought Monitor showed half the state in severe drought.

### 4. Cocoa does not follow the same simple story

For Côte d'Ivoire and Ghana, the two largest cocoa producers, a simple rainfall index explains neither yields (r between +0.01 and +0.17, none significant) nor subsequent prices (|r| no larger than 0.12). Cocoa is shaped by disease, tree age and rainfall that does damage in both directions, so it would need a far richer index than rainfall alone.

### What this suggests

**The measurement succeeds; the trading signal does not.** Public weather data captures real supply shocks, but corn futures price them one to two weeks before the drought data arrives. The most likely conclusion of the full project is that the market already knows — a defensible finding, but not a new source of predictive power.

Directions that could still produce one: measuring *how early* the market knows, testing forecasts rather than observations, heat measures above crop temperature thresholds, and properly modelling disease and rainfall for cocoa.

## Stakeholders

- **Commodity traders and funds** — price risk and trading signals
- **Food and beverage manufacturers** — procurement and hedging
- **Agribusiness and logistics** — grain handlers, barge and rail operators, ports facing physical disruption
- **Farmers and cooperatives** — hedging and marketing decisions
- **Insurers and lenders** — crop insurance and agricultural credit risk
- **Policymakers and consumers** — food security and food prices

## Data

| Source | Contents | Coverage | Status |
|---|---|---|---|
| [NASA POWER](https://power.larc.nasa.gov/) | Daily temperature and precipitation for any location | 1981 → present, 3–5 days behind | In the pipeline |
| [NOAA nClimGrid-Daily](https://www.ncei.noaa.gov/products/land-based-station/nclimgrid-daily) | Daily county-average temperature and precipitation, all 3,107 US counties | 1951 → current month | In the pipeline |
| [US Drought Monitor](https://droughtmonitor.unl.edu/) | Weekly share of area in each drought category, by county, state and nation | 2000 → present | In the pipeline |
| Futures prices (Yahoo Finance) | Daily corn, soybeans, wheat, coffee, cocoa and orange juice | 2000 → present (orange juice from 2001) | In the pipeline |
| Teucrium CORN fund | Corn prices without contract-roll jumps | 2010 → present | In the pipeline |
| [Crop yields](https://ourworldindata.org/grapher/maize-yields) (FAO, via Our World in Data) | Annual national yields for US corn and West African cocoa | 1866 → 2025 (cocoa 1961 → 2024) | In the pipeline |
| [NOAA Storm Events](https://www.ncdc.noaa.gov/stormevents/) | Severe weather events with property and crop damage | 1950 → 2026 | Verified, not yet used |
| [USDA NASS Quick Stats](https://quickstats.nass.usda.gov/) | County-level crop production, for weighting locations | Decades | Needs a free API key; module written but **unverified** |

Every source is free, and all but USDA's need no key. "In the pipeline" means
`python -m pipeline fetch` archives it and `clean` turns it into a table — see
[Reproduce](#reproduce). Two sources are deliberately overlapping: NASA POWER
serves any latitude and longitude, which is the only way to reach cocoa's West
African points, while nClimGrid gives proper county averages for the US corn belt
that a single representative point cannot.

**Every source publishes late, and by a different amount.** That gap is the
measurement this project rests on, so the pipeline records it per source rather
than assuming it: a Drought Monitor map is released two days after the Tuesday it
describes (verified), nClimGrid finalises a month early in the following month
(one observation), NASA POWER runs 3–5 days behind (observed, and the choice of
which end to use biases the timing test — see `config/sources.yaml`), a futures
close is public when the session ends, and the yield data publishes no date at
all, so it is excluded from anything point-in-time.

## Approach

1. **Map exposure.** Replace equal weights with county production shares from USDA.
2. **Measure stress.** Heat above crop temperature thresholds, rainfall and drought during each crop's sensitive window.
3. **Build the climate risk score** and validate it against yields before connecting it to prices.
4. **Link to prices.** Lead-lag regressions and event studies, controlling for seasonality, USDA report days and contract rolls.
5. **Extend** to forecasts, cocoa and coffee, and agribusiness stocks as time allows.

## Limitations

- **Five equally weighted points** stand in for the whole corn belt.
- **Continuous futures prices jump at contract rolls**, partly addressed with the CORN fund.
- **One extreme event dominates.** 2012 drives the annual price relationship on its own.
- **Observed weather, not forecasts** — and forecasts are what markets actually trade.
- **Cocoa needs more than rainfall**: disease and non-linear effects.
- **No GPS data** in the current design.

## Status

- [x] Data sources verified
- [x] Initial analysis: measurement, price link, timing, cocoa contrast
- [x] Robustness: results re-tested excluding 2012 and with a joint lead-lag estimate
- [x] Ingestion and cleaning pipeline: six sources, config-driven, every fetch archived with the date it became public
- [ ] Production weights from USDA county data (needs `NASS_API_KEY`)
- [ ] Heat measures above crop temperature thresholds
- [ ] Controls for USDA report days and contract rolls
- [ ] Forecast data
- [ ] Exploratory: cocoa, coffee, agribusiness stocks

## Reproduce

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python analysis/initial_analysis.py
```

Tested with Python 3.14, pandas 3.0 and NumPy 2.5. Public data (about 6 MB) is downloaded and cached under `data/raw/` on the first run.

### The pipeline

`analysis/initial_analysis.py` fetches and caches on its own, which was the right
size for a preview and does not scale to six sources with different publication
lags and revision behaviour. That is what the pipeline is for. Sources live in
`config/sources.yaml` and crop geography in `config/geography.yaml`, so adding
either needs no Python change:

```bash
PYTHONPATH=src python -m pipeline check-config
PYTHONPATH=src python -m pipeline fetch --source usdm --states IA --years 2012
PYTHONPATH=src python -m pipeline clean --source usdm
PYTHONPATH=src python -m pipeline status
```

Every fetch is content-addressed and appended to `data/manifest.jsonl`, so a
revised file lands beside the old numbers instead of overwriting them — which is
what makes a restatement visible. Cleaned tables are written to
`data/processed/*.parquet`, each carrying a `publication_date` column that
`pipeline.calendar.as_of` filters on, so a backtest can only see what had actually
been published.

Start narrow. `fetch --source nclimgrid` with no scope is about 820 MB, because
NCEI serves no geographic subset and every monthly file covers all 3,107 counties;
the command prints its plan and size before downloading anything, and `--dry-run`
shows it without fetching. Only `nass_production` needs a key.

```bash
python -m pytest tests/ -q
```

282 tests, all offline — every fixture under `tests/fixtures/` is a real captured
response, with a `PROVENANCE.md` recording where it came from and which trap it
exists to catch. The exception is `tests/fixtures/nass/`, which is synthetic
because Quick Stats needs a key, and says so in large letters.

## References

- Schlenker, W. and Roberts, M. J. (2009). [Nonlinear temperature effects indicate severe damages to U.S. crop yields under climate change](https://www.pnas.org/doi/10.1073/pnas.0906865106). *Proceedings of the National Academy of Sciences*, 106, 15594–15598.
