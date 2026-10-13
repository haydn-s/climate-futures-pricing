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
- **The dated-contract cocoa model does not demonstrate a robust edge.** Its
  preliminary ranking favored weather plus market features, but exact costs,
  non-overlapping execution, year-block uncertainty, a circular-shift null, and
  simple-rule comparisons reject it. Mean reversion performs better.
- **The dated-contract coffee avenue also fails validation.** Brazil weather
  improves nearby-minus-deferred curve models relative to market-only controls,
  but the best pre-2024 candidate is effectively flat after estimated costs. A
  frost-curve pattern in three selected episodes reverses in the newly added
  2000–2011 history and fails its season-matched placebo test.
- **Operational GEFS revisions do not survive their sealed holdout.** The frozen
  candidate looked strong in development (+42.0% net, 1.57 Sharpe), but lost
  7.3% with −0.98 Sharpe across 14 non-overlapping 2026 trades. It also trailed
  the matched market-only model and a fixed heat-minus-rain rule, so this bounded
  avenue is closed rather than retuned.

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
| Do operational weather-model revisions add information? | Not reliably: the locked GEFS candidate passed development but failed its one-time 2026 holdout. | Operational 00 UTC ensemble-mean runs, exact archived timestamps, dated LSEG next-open labels, 2023–2025 expanding tests, and a sealed 2026 assessment. |
| Can market-neutral targets and nonlinear models turn the null into a signal? | They produced an initial continuous-price candidate, but no predeclared model clears the final dated-contract execution gate. | Nested walk-forward selection, a consumed 2024–2026 diagnostic, LSEG execution labels, simple-rule comparisons, and block/null uncertainty tests. |
| Do cocoa and coffee behave like corn? | No. Their public agricultural statistics introduce crop-specific measurement problems. | Exploratory specification searches and case studies, not confirmatory tests. |
| Does West African weather improve a dated-contract cocoa signal? | It improves the preliminary model ranking, but the frozen candidate fails the fixed robustness gate. | 75 LSEG ICE-US contracts, nested forward selection through 2023, exact costs and overlap, year bootstrap, circular-shift null, and simple-rule controls. |
| Does Brazil weather improve a dated-contract Coffee C signal? | It improves the relative ranking of curve models, but the best bounded candidate is effectively flat and the frozen frost-curve rule fails the historical extension. | 135 LSEG ICE-US contracts, 155 independent baseline decisions, a new 2000–2011 extension, exact costs, and same-year seasonal placebos. |

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

### 4. Market-neutral modeling produces a candidate that fails final validation

The signal benchmark tests three five-day targets: raw corn returns, corn minus
wheat, and corn minus a trailing-beta soybean/wheat basket. It crosses those with
three feature sets—crop-stage weather, added price regimes, and added CPC
forecasts—and Ridge, logistic, and shallow gradient-boosting models.

Hyperparameters and buy/sell/hold thresholds are selected inside each expanding
training block. Outer folds move forward with an embargo, the original study
reserved 2024–2026 as its final holdout, and every trade pays five basis points
per leg. Market
features exclude the close used to enter the target return; CPC inputs are
delayed one session because the archive records release dates but not times, and
forecast-enhanced specifications begin when CPC coverage starts in 2012.

Two of the 27 configurations pass the explicit paper-trading gate. The primary
candidate is shallow boosting on the corn-minus-wheat target with the full public
feature set. After ten basis points per two-leg trade, it records development
Sharpe 0.81 across four positive forward folds, a 24.2% maximum development
drawdown, and holdout Sharpe 0.88 with a 9.1% drawdown. It improves materially on
the best raw-return strategy's risk-adjusted results even though their absolute
holdout returns are almost equal (11.3% versus 11.5%). A beta-adjusted spread
with Ridge also passes, but its 1.0% holdout return and 0.14 Sharpe make it a
marginal result rather than the preferred candidate.

Every regression model still has negative out-of-sample R², and the final
holdout has now been reviewed. At that stage, the supported deliverable was a
**candidate for prospective paper trading**, not evidence sufficient for live
capital. The later dated-contract studies below reject promotion while retaining
the frozen model only for prospective falsification.

Detailed assumptions and every comparison are in
[`analysis/ml_signal_baseline_report.md`](analysis/ml_signal_baseline_report.md);
machine-readable results are in
[`analysis/ml_signal_baseline_results.csv`](analysis/ml_signal_baseline_results.csv).
The frozen specification, archive hashes, intended use, and limitations are in
[`models/corn-wheat-all-public-boosting-v1/MODEL_CARD.md`](models/corn-wheat-all-public-boosting-v1/MODEL_CARD.md).

Run: `PYTHONPATH=src python analysis/ml_signal_baseline.py`

The production inference path rebuilds the point-in-time frame, trains only on
outcomes completed before the selected decision, and emits an auditable
`BUY`/`SELL`/`HOLD` paper-trading record. It never submits an order. By default,
the fitted artifact and JSON record are written beneath ignored `data/models/`
and `data/signals/` directories; use `--dry-run` to inspect without writing.

```bash
PYTHONPATH=src python -m pipeline signal --dry-run
PYTHONPATH=src python -m pipeline signal --json
PYTHONPATH=src python -m pipeline signal --as-of 2026-09-24 --dry-run
```

On the frozen archive, the latest eligible decision is September 24, 2026. As of
October 9 the raw model recommendation is `SELL`, but the input is 15 days old,
so the safe external action is forced to `HOLD` and marked `STALE_INPUTS`.

Contract-realistic execution is now being built separately from the research
target. The first execution layer selects dated ZC and ZW contracts outside a
configurable roll window, sizes integer legs for approximate dollar neutrality,
fills at the next shared session open, charges per-contract commission and
slippage, enforces dated maintenance margin, and suppresses overlapping
positions. It intentionally refuses to treat the existing continuous Yahoo
series as an orderable contract. The required input schema and current boundary
are documented in [`docs/contract_execution.md`](docs/contract_execution.md).
The vendor-neutral importer, provenance manifest, automated quality audit, and
direct LSEG Workspace download sequence are documented in
[`docs/lseg_extraction_checklist.md`](docs/lseg_extraction_checklist.md).

The licensed connector uses the signed-in local Workspace desktop session; it
does not require DSWS credentials or store secrets. Install its optional client
with `python -m pip install -r requirements-lseg.txt`, then inspect a request
without contacting LSEG:

```bash
PYTHONPATH=src python -m pipeline futures-workspace-download \
  --start 2024-01-01 --end 2024-12-31 \
  --delivery-years 2024 --dry-run
```

The October 12, 2026 licensed pull retained 73,635 daily observations for all
150 requested ZC/ZW contracts from October 3, 2011 through October 9, 2026. The
pricing audit passes. The query manifest records 23,990 rows lacking complete
OHLC and two rows with impossible open/high/low relationships that were excluded
from the import export. Reconstructed front-month returns have zero median
absolute daily-return difference from the existing Yahoo continuous histories;
correlations are 0.803 for corn and 0.942 for wheat. Official CME minimum-
performance-bond history now supplies dated margin for the public June 30, 2020
through June 30, 2026 window; earlier contract prices remain useful for the
price-only replay but are excluded from return-on-capital claims.

A post-freeze replay now tests v1 without retraining or changing its threshold.
Replacing the Yahoo target with same-date LSEG settlements preserves the market
relationship: across 285 out-of-sample decisions, target correlation is 0.901
and sign agreement is 97.2%. The stricter next-open replay tells a more cautious
story. It suppresses 49 of 149 signals under the frozen no-overlap policy and,
after $2.50 commission plus one tick of slippage per contract side, returns
−7.1% with Sharpe −0.12 across 83 development trades. The already-consumed
holdout remains positive at +7.3% with Sharpe 0.68 across 17 trades, but is too
small and no longer independent evidence. Zero-cost development returns are
positive across all three tested roll windows; base and stress costs erase them.

The result supports the integrity of the continuous-series research target but
does not establish stable executable effectiveness. Full results, the
predeclared sensitivity grid, data hashes, and interpretation are in
[`analysis/lseg_signal_replay_report.md`](analysis/lseg_signal_replay_report.md).
The aggregate tables can be committed; the row-level licensed ledger remains
ignored under `data/analysis/`.

Run: `PYTHONPATH=src python analysis/lseg_signal_replay.py`

The fixed-specification attribution study shows where development performance
is lost. Replacing Yahoo with dated LSEG settlement reduces aggregate net by
32.7 percentage points; shifting to next-open execution removes another 2.8
points. Applying the frozen no-overlap rule is the largest operational change,
removing 49 clustered signals and 30.7 points. Replacing the equivalent 10-bps
bridge with per-contract commission and slippage removes the final 13.8 points,
leaving −7.1%. Confidence is not monotonically related to performance, so the
diagnostic does not support a defensible retrospective threshold adjustment.
The year-block development interval is wide (−42.5% to +37.1%), emphasizing
uncertainty rather than a stable negative or positive edge.

Full attribution, cost capacity, aggregate direction/confidence/season/roll
strata, and uncertainty are in
[`analysis/lseg_execution_attribution_report.md`](analysis/lseg_execution_attribution_report.md).
Run: `PYTHONPATH=src python analysis/lseg_execution_attribution.py`

The final bounded modeling round learns dated-contract next-open outcomes
directly and tests corn-minus-wheat, corn-only, and wheat-only targets. Ten
predeclared elastic-net, shallow-boosting, and cost-aware three-way models are
compared with seven simple rules over identical embargoed forward folds. Model
selection ends in 2023; the already consumed 2024–2026 history is diagnostic
only. Calendar-year bootstraps, within-year circular-shift nulls, doubled-cost
stress, direction/year/regime strata, and an explicit acceptance gate are all
reported.

No candidate clears the complete gate. The spread models are approximately flat
to negative after execution, and the best corn model is materially weaker than
a simple momentum rule. A wheat elastic net is the strongest diagnostic result
(+52.2% arithmetic development net on average-leg notional, Sharpe 0.70,
circular-shift p=2.9%), but it is positive in only two of four forward folds,
so it is not promoted. Its feature set is still the corn-centered public panel,
so this is a control target rather than a wheat-specific climate model. The
supported final retrospective conclusion is **NO ROBUST EDGE DEMONSTRATED**.

The locked protocol and results are in
[`docs/corn_wheat_final_validation_protocol.md`](docs/corn_wheat_final_validation_protocol.md)
and [`analysis/corn_wheat_final_validation_report.md`](analysis/corn_wheat_final_validation_report.md).

Run: `PYTHONPATH=src python analysis/corn_wheat_final_validation.py`

The final corn follow-up tests point-in-time operational GEFS revisions rather
than published CPC categories. Candidate identity was selected using 2023–2024
only; the resulting all-GEFS-plus-market shallow boosting model remained
positive in the 2025 development confirmation and reached +42.0% net with 1.57
Sharpe across the three development years. The code, model class, costs, and six
holdout checks were committed before the one-time 2026 reveal.

The sealed result did not replicate. The frozen candidate made 14
non-overlapping trades, lost 7.3% after modeled costs, and recorded −0.98 Sharpe.
The matched market-only model lost 6.7%, while the fixed heat-minus-rain rule
gained 0.5%; doubled costs reduced the candidate result to −9.1%. Five of six
predeclared checks failed. The result closes this GEFS specification and supports
the broader conclusion that the retrospective development edge was unstable.
Aggregate evidence and hashes are in
[`analysis/gefs_corn_holdout_report.md`](analysis/gefs_corn_holdout_report.md),
with construction details in
[`docs/gefs_forecast_revisions.md`](docs/gefs_forecast_revisions.md).

The dated-margin layer parses CME's public corn and wheat PDFs, maps each roll
code to the expiry-ordered LSEG contract strip, and creates an ignored,
execution-ready panel. All 28,387 contract rows inside the public window match
an exact business-date/roll-position rate. The capital replay resets each sample
to $10k, $25k, $50k, or $100k and lets realized P&L change the next trade's
budget. All four development paths lose money; the least-negative is −3.7% on
$100k. The already-consumed holdout remains positive but is descriptive only.
The full aggregate results and source hashes are in
[`analysis/lseg_capital_simulation_report.md`](analysis/lseg_capital_simulation_report.md).

```bash
PYTHONPATH=src python -m pipeline futures-margin-enrich \
  --contracts data/processed/futures_contracts.parquet \
  --corn-pdf /path/to/C-2020-to-present.pdf \
  --wheat-pdf /path/to/W-2020-to-present.pdf
PYTHONPATH=src python analysis/lseg_capital_simulation.py
```

Prospective validation now uses separate immutable decision and outcome
records. A decision must be registered on its decision date before the LSEG
panel contains a possible next-session entry. The later resolver applies the
same contract selection, holding period, costs, and no-overlap policy and links
the outcome to hashes of the original decision, config, and resolving contract
snapshot. Neither command has order authority. The weekly operating procedure
is documented in
[`docs/prospective_paper_trading.md`](docs/prospective_paper_trading.md).

```bash
PYTHONPATH=src python -m pipeline paper-register \
  --signal-record data/signals/corn-wheat-all-public-boosting-v1/RECORD.json \
  --contracts data/processed/futures_contracts.parquet
PYTHONPATH=src python -m pipeline paper-resolve \
  --contracts data/processed/futures_contracts.parquet
PYTHONPATH=src python -m pipeline paper-status
```

### 5. Event timing is real but not cleanly attributable

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

### 6. Cocoa and coffee expose target-data limitations

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

The first execution-aligned cocoa baseline uses 28,802 licensed LSEG rows across
75 ICE-US contracts from October 2011 through October 2026. Weekly decisions
enter the selected dated contract at the next open and exit five sessions later.
The best pre-2024 candidate combines weather and market features: it posts a
0.48 development Sharpe and 67.5% summed net return across 189 trades, versus
0.20 Sharpe for the best market-only candidate. It nevertheless earns less net
return than the long-only development baseline and loses 259.5% in the consumed
2024+ diagnostic. A weather-only boosting model behaves differently across that
diagnostic, but was not the development winner and cannot be selected after the
fact. The supported conclusion remains **no robust cocoa edge demonstrated**.

The follow-up freezes that development-selected candidate and applies exact
row-specific costs plus the same no-overlap policy used in the final grain
validation. That leaves 109 trades with 20.4% summed development net return,
Sharpe 0.18, and only 5 of 11 positive years. The calendar-year bootstrap gives
a 76.0% probability of positive total performance and a 95% interval from
−33.7% to +77.5%; the within-year circular-shift null has p=0.163. Both miss the
locked thresholds. A deterministic mean-reversion rule on the identical dates
earns 62.3% with Sharpe 0.33. The model stays positive under doubled costs, but
that isolated pass does not overcome its instability or baseline inferiority.

The consumed 2024+ diagnostic remains negative after exact execution treatment:
−88.5% summed net return with Sharpe −0.71. It does not enter the gate.

Run the baseline after importing the ignored licensed contract panel:

```bash
PYTHONPATH=src python analysis/cocoa_model_baseline.py
PYTHONPATH=src python analysis/cocoa_fixed_validation.py
```

See [`analysis/cocoa_model_baseline_report.md`](analysis/cocoa_model_baseline_report.md)
and [`analysis/cocoa_fixed_validation_report.md`](analysis/cocoa_fixed_validation_report.md).
The two locked protocols are in
[`config/cocoa_model_validation.yaml`](config/cocoa_model_validation.yaml) and
[`config/cocoa_fixed_validation.yaml`](config/cocoa_fixed_validation.yaml).

For coffee, NASA POWER identifies July 2021 as the coldest Brazilian winter in
the 26-year sample, and arabica prices rise sharply into 2022. The national FAO
series does not show a corresponding weather relationship: biennial bearing
alone explains 44% of detrended production variance, and the statistic combines
frost-exposed arabica with robusta and unaffected regions.

The execution-aligned coffee baseline uses 37,720 licensed LSEG rows across 75
ICE-US Coffee C contracts. The locked comparison creates 155 non-overlapping
20-session decisions and tests outright returns plus nearby-minus-deferred curve
returns. Candidate selection ends in 2023; 2024+ is a consumed diagnostic.

The best curve specification is the weather-plus-market logistic model at 0.5%
summed net return and Sharpe 0.05—economically flat. Relative to the corresponding
market-only model, adding Brazil weather improves development net by 14.6
percentage points and Sharpe by 1.38, but improvement from a poor baseline is
not an edge. The best outright candidate loses 5.9% with Sharpe −0.09.

The predeclared frost study contains only three development episodes. All three
are followed by a positive 20-session nearby-minus-deferred move, averaging
0.63% gross and 0.48% after estimated two-leg costs, while outright and
60-session responses are inconsistent. One consumed 2025 episode has the same
positive short-horizon curve sign. This is a mechanistically interesting
observation, not enough data for significance or a fitted frost model.

The final bounded test freezes that apparent rule before adding LSEG history
from 2000–2011. The extension supplies only three eligible frost episodes; the
fixed long-nearby/short-deferred trade is positive after costs in 1/3, with
mean net −0.93% and median net −1.71%. Its same-year June–August placebo
p-value is 0.966. All five locked gate conditions fail. Even pooling the selected
and new pre-2024 episodes produces mean net −0.23% and p=0.823. The supported
conclusion is that the initial 3/3 pattern did not replicate.

Run:

```bash
PYTHONPATH=src python analysis/cocoa_weather.py
PYTHONPATH=src python analysis/coffee_weather.py
PYTHONPATH=src python analysis/coffee_model_baseline.py
PYTHONPATH=src python analysis/coffee_frost_curve_validation.py
```

See [`analysis/coffee_model_baseline_report.md`](analysis/coffee_model_baseline_report.md)
and [`analysis/coffee_frost_curve_validation_report.md`](analysis/coffee_frost_curve_validation_report.md).
The locked protocols are in [`config/coffee_model_validation.yaml`](config/coffee_model_validation.yaml)
and [`config/coffee_frost_curve_validation.yaml`](config/coffee_frost_curve_validation.yaml).

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

The ML signal benchmark adds a stricter decision layer: model and threshold
selection are nested within each training period, costs scale with the number of
traded legs, and a candidate must perform across forward folds and the fixed
holdout before it can be considered for paper trading.

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
| NOAA operational GEFS | Ensemble forecast temperature and precipitation revisions | GEFS v12, 2020-09-23 onward; bounded experiment uses May-August 2021-2026 | 00 UTC initialization + 7 hours; later archive republications excluded |
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
# Needed only to decode operational GEFS GRIB2 files:
pip install -r requirements-gefs.txt
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
PYTHONPATH=src python -m pipeline signal --dry-run
PYTHONPATH=src python -m pipeline gefs-audit --date 2026-10-01
PYTHONPATH=src python -m pipeline gefs-download \
  --date 2024-07-15 --through-hour 240
PYTHONPATH=src python -m pipeline gefs-backfill \
  --start 2021-05-01 --end 2026-08-31 --dry-run
PYTHONPATH=src python -m pipeline clean --source gefs_forecast
PYTHONPATH=src python -m pipeline gefs-features
PYTHONPATH=src python -m pipeline gefs-model-frame
PYTHONPATH=src python analysis/gefs_corn_validation.py
PYTHONPATH=src python -m pipeline futures-workspace-download \
  --start 2024-01-01 --end 2024-12-31 --delivery-years 2024 --dry-run
PYTHONPATH=src python analysis/lseg_signal_replay.py
PYTHONPATH=src python analysis/lseg_execution_attribution.py
PYTHONPATH=src python -m pipeline paper-register \
  --signal-record SIGNAL.json --contracts data/processed/futures_contracts.parquet --dry-run
PYTHONPATH=src python -m pipeline paper-resolve \
  --contracts data/processed/futures_contracts.parquet --dry-run
PYTHONPATH=src python -m pipeline paper-status
PYTHONPATH=src python -m pipeline status
python -m pytest tests -q
```

`gefs-audit` downloads only small source indexes, verifies that the configured
corn-weather messages exist, and estimates the eventual byte-range transfer. It
does not download or decode GRIB data. `gefs-download` archives exact audited
GRIB byte ranges for one run; `gefs-backfill` resumes the locked May-August date
scope and skips complete selections without contacting NOAA. Cleaning decodes
the five configured Corn Belt points and excludes files whose archive timestamp
cannot establish same-day operational availability. `gefs-features` requires
all four six-hour intervals and all five points before producing days 1-3, 4-7,
and 8-10 level and prior-day revision features. The bounded design, point-in-time
rule, locked validation protocol, and acceptance gate are in
[`docs/gefs_forecast_revisions.md`](docs/gefs_forecast_revisions.md).
`fetch --source nclimgrid` with no scope
downloads county-wide files because NCEI
provides no geographic subset. Use `--dry-run` and narrow years or months when
probing a source.

## Repository layout

```text
config/                  source definitions and crop geography
src/pipeline/            clients, ingestion, cleaning, storage, features, tests
analysis/                research scripts and generated comparison reports
models/                  frozen model cards and versioned baseline metadata
tests/                   offline unit tests and captured-response fixtures
tests/fixtures/*/        fixture provenance and upstream traps
presentation/            proposal deck and its generator
reproduce.py             resumable end-to-end workflow
```

## Current status

- [x] Content-addressed, publication-aware public-data pipeline
- [x] County and point weather, drought, price, yield, and CPC forecast features
- [x] Corn yield validation with honest trend fitting
- [x] Daily and weekly forward return backtests
- [x] Raw and market-neutral ML signals with nested, cost-aware evaluation
- [x] Frozen v1 model card, data hashes, and auditable paper-signal command
- [x] Licensed LSEG Workspace contract downloader and validated 2011-2026 ZC/ZW panel
- [x] LSEG ICE-US cocoa adapter and validated 2011-2026 dated CC contract panel
- [x] LSEG ICE-US Coffee C adapter and validated 2000-2026 dated KC contract panel
- [x] Frozen-signal LSEG target and next-open execution replay with cost sensitivity
- [x] Fixed-specification execution attribution and cost-capacity diagnostics
- [x] Immutable, hash-linked prospective paper decision and outcome workflow
- [x] Official CME dated-margin ingestion and 2020-2026 capital-constrained replay
- [x] Drought event, attribution, cross-crop, and seasonal falsification studies
- [x] Cocoa and coffee measurement case studies
- [x] Preliminary execution-aligned cocoa weather/market model comparison
- [x] Fixed-specification cocoa uncertainty, null, baseline, and double-cost validation
- [x] Preliminary execution-aligned Coffee C outright, curve, and frost comparison
- [x] Fixed Coffee C frost-curve historical extension and season-matched null
- [x] Operational GEFS index parser and backfill feasibility audit
- [x] Operational GEFS Corn Belt extraction and forecast-revision feature panel
- [x] Locked GEFS development validation and one-time sealed 2026 holdout
- [ ] Dated ICE cocoa margin history and capital-constrained replay (deferred after gate failure)
- [x] One-command current-data reproduction workflow
- [ ] Live USDA NASS validation and county production weights
- [ ] USDA report-day and explicit contract-roll controls
- [x] A cost-aware market-neutral candidate that clears the paper-trading gate
- [ ] Prospective paper-trading validation on data not used in this study
- [ ] Frozen, distributable raw-data snapshot for byte-identical reproduction
- [ ] Agribusiness equities and NOAA Storm Events extension

## References

- Schlenker, W. and Roberts, M. J. (2009). [Nonlinear temperature effects indicate severe damages to U.S. crop yields under climate change](https://www.pnas.org/doi/10.1073/pnas.0906865106). *Proceedings of the National Academy of Sciences*, 106, 15594–15598.
