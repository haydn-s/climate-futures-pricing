# Climate Futures Pricing

**Can public weather data measure agricultural supply shocks, and does it contain
information that commodity futures have not already priced?**

Duke AIPI 590 — Alternative Data

## Executive summary

This project builds a point-in-time archive of public weather, drought, forecast,
crop-yield, and commodity-price data. It maps weather to crop-producing regions,
checks whether the resulting measures explain physical harvest outcomes, and then
tests whether the same information predicts futures returns.

The central result is a useful asymmetry:

- **The physical measurement works for US corn.** County-level heat and drought
  predict held-out annual yield shortfalls with an out-of-sample R-squared of
  0.73, or 0.46 with the exceptional 2012 season removed.
- **The public-data trading signal does not.** Published observations and CPC
  6–10/8–14 day forecasts produce approximately zero or negative out-of-sample
  return R-squared across the tested horizons and model families.
- **Data construction becomes the limiting factor for tree crops.** A West
  African cocoa aggregate reveals a plausible dry-spell signal that individual
  country records obscure, while Brazil's national coffee statistic mixes crop
  types, regions, and a strong biennial-bearing cycle that hides a clearly
  observed 2021 frost.

The supported conclusion is not that weather is irrelevant to prices. It is that
free climate data can measure important physical shocks, while these particular
public releases, features, contracts, horizons, and models do not yield a
repeatable return forecast.

## Research questions and current answers

| Question | Current answer | Evidence level |
|---|---|---|
| Can free climate data measure crop supply shocks? | Yes for US corn; county weather plus drought materially outperforms five representative points. | Strongest result: forward, leave-one-year-out validation across 26 harvests. |
| Does the market move before or after drought data is published? | Both occur. Large events show more average return after publication, while the pre-publication move is concentrated in corn's pollination window. | Exploratory event evidence: only 3–22 usable events per cell, depending on the cut. |
| Does the risk score predict futures returns? | No detectable skill in the tested daily or weekly models. | Forward expanding-window backtests with a target-horizon embargo. |
| Do public forecasts add information? | CPC outlooks predict subsequent weather, but neither forecast levels nor revisions predict corn returns. | 2012–2026 forecast sample; best tradeable result is effectively zero. |
| Do cocoa and coffee behave like corn? | No. Their public agricultural statistics introduce crop-specific measurement problems. | Exploratory specification searches and case studies, not confirmatory tests. |

Agribusiness equities and NOAA Storm Events remain outside the implemented
analysis.

## Results

### 1. Corn weather measures predict harvest shortfalls

The annual model compares NASA POWER observations at five state-level points
with NOAA nClimGrid daily averages for 473 counties. All trend fitting is repeated
inside each held-out-year fold.

| Feature set | Held-out R² | Excluding 2012 |
|---|---:|---:|
| Five representative points | 0.304 | 0.166 |
| County averages | 0.613 | 0.409 |
| County averages plus drought | **0.730** | **0.464** |
| Five points plus drought | 0.451 | 0.027 |

County heat degree-days above 29 °C have a descriptive correlation of −0.87
with detrended yield, or −0.73 excluding 2012. These correlations explain the
relationship; the held-out scores are the stronger validation result.

Run: `PYTHONPATH=src python analysis/corn_yield_model.py`

### 2. Published observations do not forecast returns

The point-in-time panel joins each trading date only to information whose
`publication_date` had arrived by then. Expanding-window folds move forward in
time and remove the target horizon from the end of each training block.

For five-day corn returns, the best tradeable observational model scores
R² −0.016. Weekly sampling, which removes overlapping daily targets, also scores
at or below zero. Allowing models to peek up to 21 days into unpublished future
observations does not create meaningful skill, so publication latency is not a
sufficient explanation for the null.

Run:

```bash
PYTHONPATH=src python analysis/corn_peek_sweep.py
PYTHONPATH=src python analysis/corn_weekly_model.py
```

### 3. Public forecasts see weather, but do not predict corn returns

CPC 6–10 and 8–14 day outlooks correlate with the weather that subsequently
arrives, so they contain real meteorological information. They nevertheless
understated the exceptional 2012 heat: the forecast ranked that summer 10th of
15, while realized heat ranked first.

Across forecast levels, forecast revisions, temperature-only features, drought,
and combined feature sets, every tradeable return R-squared is negative. The
best value anywhere at peek zero is −0.0003 for a one-day drought-only ridge
model; hit rates stay near 50%. This supports a null for the tested information
sets rather than a universal claim about every weather forecast or trading rule.

Run:

```bash
PYTHONPATH=src python analysis/forecast_verification.py
PYTHONPATH=src python analysis/forecast_peek_sweep.py
```

### 4. Event timing is real but not cleanly attributable

The top drought jumps tell a more complicated story than the preliminary
lead-lag correlations did. Among 12 severe events with complete price windows,
seasonally adjusted corn returns average +2.80% in the ten trading days before
publication and +6.34% in the ten days after it. About 31% of the two-sided move
therefore precedes the official figure.

That does **not** identify a reaction to publication. Drought is persistent, so
the post window often contains worsening weather as well as the market response.
When events are split into continued and peaked droughts, the post-publication
move is much larger for continued episodes. Joint regressions and alternative
thresholds do not resolve attribution with the available sample.

Seasonal cuts locate the strongest early move in corn's July–August pollination
window: +7.76% before publication at the 90th-percentile event threshold. Wheat,
however, also moves during months when its crop is already harvested. That
falsification result cautions against interpreting the price response as purely
crop-supply news; common agricultural-market flows may contribute.

Run:

```bash
PYTHONPATH=src python analysis/corn_event_study.py
PYTHONPATH=src python analysis/corn_event_attribution.py
PYTHONPATH=src python analysis/cross_crop_events.py
PYTHONPATH=src python analysis/seasonal_timing.py
```

### 5. Cocoa and coffee expose target-data limitations

For cocoa, Côte d'Ivoire and Ghana's detrended yield shortfalls correlate at
−0.59 even though the countries are adjacent and share weather systems. A
plausible explanation is cross-border movement of beans under differing fixed
farmgate prices. Aggregating Côte d'Ivoire, Ghana, Nigeria, Cameroon, and Togo as
total production divided by total harvested area cancels transfers within the
bloc. Lagged main-season dry-spell length then correlates +0.55 with the bloc's
yield shortfall, and an exploratory leave-one-year-out model scores up to
R² 0.35. That score is optimistic because the window and variables were selected
after inspecting 56 candidate correlations.

The 2024 cocoa price spike is not accompanied by comparably exceptional weather
in this archive, pointing toward omitted disease, tree-age, and market-structure
drivers.

For coffee, NASA POWER identifies July 2021 as the coldest Brazilian winter in
the 26-year sample, and arabica prices rise sharply into 2022. The national FAO
series does not show a corresponding weather relationship: biennial bearing
alone explains 44% of detrended production variance, and the statistic combines
frost-exposed arabica with robusta and unaffected regions.

Run:

```bash
PYTHONPATH=src python analysis/cocoa_weather.py
PYTHONPATH=src python analysis/coffee_weather.py
```

## Evidence and inference guardrails

The analyses are deliberately not treated as equally conclusive.

1. **Validated measurement:** the corn yield result uses leave-one-year-out
   predictions, refits the yield trend within every fold, and reports the result
   with and without 2012.
2. **Predictive null:** return models use forward-only expanding windows,
   training-only scaling, and an embargo equal to the forward-return horizon.
   Weekly sampling checks that overlapping daily targets are not hiding skill.
3. **Event evidence:** events are aligned to publication dates, de-clustered,
   seasonally adjusted, and compared with same-size placebo samples. The cost is
   low power: many reported cells contain fewer than ten events.
4. **Exploratory evidence:** seasonal stages, cocoa windows, thresholds, and
   tree-crop measures involve specification search. Their full matrices and test
   counts are reported; isolated p-values are not treated as confirmation.

Important remaining threats:

- Event returns cannot fully separate publication effects from continuing
  weather, USDA reports, index flows, the dollar, or general risk appetite.
- Continuous futures contain contract-roll effects. The CORN fund provides a
  roll-resistant check only from 2010 onward.
- Historical US Drought Monitor requests return the current value for an old map,
  not necessarily its first-published vintage. Archiving prevents future
  overwrites but cannot recover revisions made before collection.
- County weather is equally weighted because USDA production weights still need
  a `NASS_API_KEY` and live validation.
- CPC coverage begins in 2012, making forecast tests shorter than observational
  tests and placing the exceptional 2012 episode at the start of the sample.
- The tree-crop samples contain roughly two dozen annual observations and many
  candidate specifications. Their reported models are hypotheses for validation,
  not final estimates.

## Data pipeline

| Source | Contents | Coverage used | Publication treatment |
|---|---|---|---|
| NASA POWER | Daily point temperature and precipitation | 2000–present; global crop points | Conservative five-day lag |
| NOAA nClimGrid-Daily | Daily county temperature and precipitation | 2000–present; US crop belts | Monthly finalization, unavailable intramonth |
| US Drought Monitor | Weekly county drought coverage | 2000–present | Thursday release for Tuesday map |
| CPC Outlooks | 6–10 and 8–14 day temperature/precipitation probabilities | 2012–present | Issuance date; known before valid weather |
| Yahoo Finance | Corn, soy, wheat, coffee, cocoa, orange juice, and CORN fund closes | 2000–present where available | Session close |
| Our World in Data / FAO | Annual yield and production | Crop-dependent | Excluded from point-in-time return features |
| USDA NASS Quick Stats | County crop production for future weighting | Module and synthetic tests only | Requires `NASS_API_KEY`; live behavior unverified |

Source definitions and known traps live in
[`config/sources.yaml`](config/sources.yaml); crop geography and sensitive
windows live in [`config/geography.yaml`](config/geography.yaml).

Each fetch is stored by content hash and appended to `data/manifest.jsonl`.
Revised responses sit beside older bytes rather than replacing them. Cleaning is
offline and writes `data/processed/*.parquet`; point-in-time tables retain a
`publication_date` used by `pipeline.calendar.as_of`.

## Reproduce

### Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The current suite is exercised on Python 3.14 with pandas 3.0 and NumPy 2.5.

### Complete current-data workflow

```bash
python reproduce.py --dry-run  # inspect every command without network access
python reproduce.py            # fetch, clean, build features, analyze, test
```

The full keyless archive is roughly 5 GB. The CPC backfill alone contains more
than 12,000 small files and can take considerable time. The workflow is resumable:
already archived keys are skipped, while changed content creates a new version.
`nass_production` is excluded because it is not used by the reported results and
requires a key; opt in with `--include-nass` after setting `NASS_API_KEY`.

To rerun the research and tests from existing processed tables without contacting
any source:

```bash
python reproduce.py --stages analysis test
```

Individual stages can be resumed explicitly:

```bash
python reproduce.py --stages fetch clean
python reproduce.py --stages features
python reproduce.py --stages analysis
python reproduce.py --stages test
```

Use `--refresh` only when intentionally collecting a new source vintage. It
forces downloads but retains previous content-addressed versions.

### Exact versus current-data reproduction

`reproduce.py` reproduces the workflow against the data currently served by each
provider. It cannot guarantee byte-identical historical results on a fresh clone:
public endpoints revise files, Yahoo continuous contracts can change, and USDM
does not expose every first-published vintage. Exact archival reproduction
therefore requires preserving the ignored `data/` directory—or distributing a
versioned snapshot and its manifest—alongside the commit. The repository alone
provides computational reproducibility, not a frozen external-data snapshot.

The older `analysis/initial_analysis.py` remains as the small proposal preview;
it downloads about 6 MB and does not reproduce the current results above.

### Direct pipeline commands

```bash
PYTHONPATH=src python -m pipeline check-config
PYTHONPATH=src python -m pipeline fetch --source usdm --states IA --years 2012
PYTHONPATH=src python -m pipeline clean --source usdm
PYTHONPATH=src python -m pipeline features --crop corn --peek 0 --growing-season
PYTHONPATH=src python -m pipeline status
python -m pytest tests -q
```

`fetch --source nclimgrid` with no scope downloads county-wide files because NCEI
provides no geographic subset. Use `--dry-run` and narrow years or months when
probing a source.

## Repository layout

```text
config/                  source definitions and crop geography
src/pipeline/            clients, ingestion, cleaning, storage, features, tests
analysis/                research scripts; each prints its assumptions and result
tests/                   offline unit tests and captured-response fixtures
tests/fixtures/*/        fixture provenance and upstream traps
presentation/            proposal deck and its generator
reproduce.py             resumable end-to-end workflow
```

## Current status

- [x] Content-addressed, publication-aware pipeline for six populated public sources
- [x] County and point weather, drought, price, yield, and CPC forecast features
- [x] Corn yield validation with honest trend fitting
- [x] Daily and weekly forward return backtests
- [x] Drought event, attribution, cross-crop, and seasonal falsification studies
- [x] Cocoa and coffee measurement case studies
- [x] One-command current-data reproduction workflow
- [ ] Live USDA NASS validation and county production weights
- [ ] USDA report-day and explicit contract-roll controls
- [ ] Frozen, distributable raw-data snapshot for byte-identical reproduction
- [ ] Agribusiness equities and NOAA Storm Events extension

## References

- Schlenker, W. and Roberts, M. J. (2009). [Nonlinear temperature effects indicate severe damages to U.S. crop yields under climate change](https://www.pnas.org/doi/10.1073/pnas.0906865106). *Proceedings of the National Academy of Sciences*, 106, 15594–15598.
