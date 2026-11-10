# Corn Supply-Risk Estimator v1

## Status

This is the project's positive result and its defensible deliverable: a
estimator of the US corn yield deviation from trend, built from free public
weather and drought data. It is **a measurement model, not a trading model**,
and it carries no return forecast of any kind.

Unlike [`../corn-wheat-all-public-boosting-v1`](../corn-wheat-all-public-boosting-v1/MODEL_CARD.md),
this is not a frozen fitted artifact awaiting prospective falsification. It is a
fixed *specification* — feature sets, estimator, and hyperparameter were settled
in the original yield study and have not been retuned since — evaluated forward
across held-out harvests. Every reported number refits the whole pipeline inside
each fold.

No sealed holdout exists for this model. All sixteen scored harvests have
already informed the project, so its scores describe calibration on this sample
rather than guaranteeing it on the next one.

## Intended use

Estimate how far a US corn harvest will fall below or above its trend, from
weather and drought observed over the growing season, and express that estimate
as a calibrated distribution rather than a point.

Supported uses:

- **Post-harvest attribution.** Explaining how much of a given season's
  shortfall is attributable to observed heat and drought.
- **Provisioning and risk sizing** by a party who already holds physical corn
  exposure — a buyer, feed mill, ethanol plant, or lender — where the payoff is
  a lower chance of being caught short rather than a trading profit.

Unsupported uses are listed under *Limitations* and include every trading use.

## Frozen specification

| Item | Value |
|---|---|
| Model version | `corn-supply-risk-counties-drought-v1` |
| Target | Fractional deviation of US corn yield from a training-only linear trend |
| Estimator | Standardised Ridge, alpha = 1.0 |
| Headline features | County July heat degree-days above 29 °C, county mean July maximum temperature, peak growing-season D2-or-worse drought coverage |
| Geography | 473 counties across five corn states, equally weighted |
| Validation | Expanding-window forward folds, minimum 10 training harvests |
| Trend handling | Refitted inside every fold, and again inside every leave-one-out calibration fit |
| Intervals | Jackknife+, calibrated only on earlier harvests |
| Scored harvests | 16 (2010–2025), or 15 with 2012 removed and every fold refitted |

The specification is in [`../../src/pipeline/yield_validation.py`](../../src/pipeline/yield_validation.py);
intervals in [`../../src/pipeline/yield_intervals.py`](../../src/pipeline/yield_intervals.py).
Source fingerprints accompany every committed report.

## Evidence

### It beats a trend forecast, and not because of one season

| Sample | R² vs trend | Range withholding any single harvest | Sign flips |
|---|---:|---|---:|
| All years | +0.748 | +0.666 to +0.775 | 0 |
| 2012 excluded, refitted | +0.476 | +0.333 to +0.525 | 0 |

Mean absolute error is 0.314 t/ha against the trend forecast's 0.580. Removing
a harvest from the score is not the same experiment as the `exclude_2012`
sample, which also drops it from training; both are reported because they answer
different questions. Neither produces a sign change, so the result is not an
artifact of a single harvest.

### But the skill is concentrated in the seasons that went wrong

| Stratum | R² vs trend |
|---|---:|
| Third of seasons furthest from trend | +0.815 |
| Calmest third | +0.336 |
| Calmest third, 2012 refitted out | **−0.452** |

The estimator is a damage function. It earns its score where the crop is
failing and adds little — or actively hurts — in a normal year. For a
supply-risk measure that is the expected shape, but the aggregate R² must not be
read as accuracy in a typical season.

It is close to symmetric in direction (+0.735 on harvests below trend against
+0.777 above), so it behaves as a two-sided weather-to-yield measure rather than
a drought detector only.

### Calibrated uncertainty

Jackknife+ intervals, with coverage measured rather than assumed:

| Specification | Nominal | Covered | Mean width (% of trend) |
|---|---:|---:|---:|
| Full season, counties + drought | 90% | 16/16 | 17.4 |
| Full season, counties + drought | 80% | 15/16 | 11.8 |
| August 1 nowcast, points + drought | 90% | 15/16 | 20.5 |

**The guarantee is weaker than the label.** Jackknife+ guarantees coverage of at
least `2 × level − 1`, so the 90% interval carries an 80% worst-case guarantee,
and that guarantee assumes an exchangeability an ordered yield series does not
satisfy. The intervals are validated by observed coverage, not by the theorem.

2012 is covered at 90% by 0.018 t/ha. Every narrower construction tested — plain
jackknife and a Gaussian residual baseline — leaves it outside. The usable
resolution of this estimator is about **±8.7% of trend yield**.

### Decision value, in a price-free test

At a 4:1 under-provisioning penalty, choosing a buffer from this model's
predictive distribution rather than from climatology saves 53.1% of provisioning
cost across all years, and 14.4% with 2012 refitted out. The model is the
*narrower* of the two distributions (0.76× climatology's spread), so the saving
is not conservatism under an asymmetric loss.

The whole-harvest bootstrap excludes zero only at the most risk-averse
asymmetry tested. Without 2012 it straddles zero at every asymmetry
(P(saving > 0) = 66% at 4:1). **The estimator behaves as insurance: it pays in
bad years, charges a premium in good ones, and outside a catastrophic season its
advantage is a point estimate this sample cannot establish.**

Full results: [`../../analysis/corn_yield_forward_validation_report.md`](../../analysis/corn_yield_forward_validation_report.md),
[`../../analysis/corn_yield_strata_report.md`](../../analysis/corn_yield_strata_report.md),
[`../../analysis/corn_yield_intervals_report.md`](../../analysis/corn_yield_intervals_report.md),
[`../../analysis/corn_yield_decisions_report.md`](../../analysis/corn_yield_decisions_report.md).

## Reproduce

```bash
PYTHONPATH=src python analysis/corn_yield_forward_validation.py
PYTHONPATH=src python analysis/corn_yield_nowcast.py
PYTHONPATH=src python analysis/corn_yield_strata.py       # reads the two above
PYTHONPATH=src python analysis/corn_yield_intervals.py
PYTHONPATH=src python analysis/corn_yield_decisions.py
```

Each writes committed CSVs, a report, and source SHA-256 fingerprints, and each
reproduces its outputs byte-identically on rerun.

## Limitations and prohibited uses

- **This model must not be used to take or size a futures position.** The
  project's separate efficiency test found the new-crop December contract
  already explains the harvest about as well as this estimator does, with the
  estimator adding +0.120 on top of the price across all years and +0.001
  without 2012. Any apparent trading edge from these estimates is far more
  likely to be a cost or sample artifact than information the market lacks.
- **The headline specification is not an in-season forecast.** County weather is
  not published in time: the availability audit found current-season county data
  in only 3 of 26 harvest years. The in-season variant falls back to five state
  points, scores −0.073 against trend at August 1 without 2012, and its
  intervals both widen and under-cover, missing 2011 at every issue date.
- Sixteen scored harvests make every number here small-sample. One season is
  worth 6.25 points of observed coverage.
- The target is national US yield while the weather covers five states with
  equal state weighting, not production weighting. USDA county production
  weights remain unimplemented.
- Inputs are revised historical values, not the vintages a decision would have
  faced. POWER may use revised reanalysis and USDM serves revised historical
  maps; OWID/FAO yield labels have no original release vintages here.
- The provisioning result assumes a stated penalty asymmetry and omits storage
  economics, basis exposure, and contractual obligations. It is a demonstration
  of decision value, not a procurement system.
- No sealed holdout exists. A genuinely new harvest would be the first
  independent test, and the 2026 US corn yield will not be final until roughly
  January 2027.

## Versioning

Changing a feature set, the estimator, the alpha, the trend specification, or
the interval method requires a new model version. The scores above may not be
carried across such a change, because every one of them was produced by refitting
this exact specification inside each fold.
