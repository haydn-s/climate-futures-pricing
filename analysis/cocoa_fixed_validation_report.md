# Cocoa Fixed-Specification Validation

## Decision

**The frozen cocoa candidate fails the acceptance gate. The validated conclusion is NO ROBUST EDGE DEMONSTRATED.**

With exact row-specific costs and the no-overlap policy, the selected model produces 20.4% summed development net return, Sharpe +0.18, and positive results in 5/11 scored years. Its 95% year-block bootstrap interval is -33.7% to 77.5%; the within-year circular-shift null p-value is 16.3%.

The strongest deterministic rule is `mean_reversion`. The 2024+ consumed diagnostic remains sharply negative at -88.5% and Sharpe -0.71; it is reported but does not enter a gate.

## Audit boundary

- Protocol: `cocoa-fixed-validation-v1`; SHA-256 `75cde933d4d34f989efe314623cd46371acff0712e5cb27b6bd721ec2fb51e28`.
- Candidate: `weather_market_logistic`, selected before this robustness run.
- Selection and all gate calculations end in 2023.
- Outcomes use dated LSEG ICE-US CC contracts, next-open entry, and the open
  five sessions later on the same contract.
- Costs are row-specific; one tick plus $2.50 commission is charged per side.
- The no-overlap policy skipped 80 otherwise active model decisions in the development ledger.
- Ignored contract-panel SHA-256: `66fcf598b8e59456a05cc9c65eefc3b6e2853623f7edf31a32c4446c684d9c27`.
- Licensed row-level results remain under ignored `data/`; committed outputs
  contain aggregates only.

## Cost sensitivity

| Sample | Cost | Trades | Hit | Net | Sharpe | Drawdown | +years |
|---|---:|---:|---:|---:|---:|---:|---:|
| development | 0× | 109 | 49.5% | 30.7% | +0.27 | -18.0% | 6/11 |
| development | 1× | 109 | 49.5% | 20.4% | +0.18 | -19.0% | 5/11 |
| development | 2× | 109 | 49.5% | 10.0% | +0.09 | -20.2% | 5/11 |
| consumed_2024_plus | 1× | 63 | 47.6% | -88.5% | -0.71 | -76.9% | 1/3 |

## Exact-date deterministic baselines — development only

| Rule | Trades | Hit | Net | Sharpe | Drawdown | +years |
|---|---:|---:|---:|---:|---:|---:|
| `mean_reversion` | 257 | 55.3% | 62.3% | +0.33 | -26.5% | 6/11 |
| `long_only` | 257 | 50.2% | -2.1% | -0.01 | -56.1% | 4/11 |
| `short_only` | 257 | 49.0% | -47.9% | -0.25 | -57.7% | 4/11 |
| `momentum` | 257 | 44.0% | -112.3% | -0.59 | -77.2% | 1/11 |
| `no_trade` | 0 | — | 0.0% | — | 0.0% | 0/11 |

Every rule uses the same 530 forward-fold dates, contracts, outcomes, costs,
and overlap policy as the model. There is no resampling or favorable date cut.

## Stability — development only

| Dimension | Bucket | Trades | Hit | Net | Sharpe |
|---|---|---:|---:|---:|---:|
| year | 2013 | 6 | 50.0% | -1.0% | -0.49 |
| year | 2014 | 25 | 40.0% | 3.7% | +0.23 |
| year | 2015 | 21 | 47.6% | -1.3% | -0.11 |
| year | 2016 | 8 | 37.5% | -2.1% | -0.22 |
| year | 2017 | 10 | 70.0% | 19.6% | +1.69 |
| year | 2018 | 11 | 63.6% | 8.5% | +0.56 |
| year | 2019 | 8 | 37.5% | -13.2% | -1.29 |
| year | 2020 | 7 | 57.1% | 8.9% | +0.80 |
| year | 2021 | 2 | 0.0% | -5.3% | -1.41 |
| year | 2022 | 5 | 60.0% | -5.2% | -0.52 |
| year | 2023 | 6 | 66.7% | 7.8% | +1.05 |
| season | shoulder Mar-May/Oct-Nov | 51 | 51.0% | 7.5% | +0.19 |
| season | Harmattan Dec-Feb | 24 | 45.8% | 7.0% | +0.24 |
| season | main-pod Jun-Sep | 34 | 50.0% | 6.0% | +0.13 |
| price_regime | positive 20-session momentum | 58 | 48.3% | -5.7% | -0.12 |
| price_regime | negative 20-session momentum | 51 | 51.0% | 26.1% | +0.41 |
| price_regime | flat | 0 | — | 0.0% | — |

## Gate checks

- PASS — at least 30 development trades
- PASS — positive development net
- FAIL — development Sharpe above 0.50
- PASS — positive in at least 60% of folds
- FAIL — positive in at least 60% of years
- FAIL — beats best simple baseline net
- FAIL — beats best simple baseline Sharpe
- PASS — positive with doubled costs
- FAIL — year-bootstrap probability positive at least 80%
- FAIL — circular-shift null p-value at most 10%
- PASS — development drawdown no worse than -25%

The failed conditions are:

- development Sharpe above 0.50
- positive in at least 60% of years
- beats best simple baseline net
- beats best simple baseline Sharpe
- year-bootstrap probability positive at least 80%
- circular-shift null p-value at most 10%

## Interpretation

Weather improved the original development ranking relative to market-only
features, but this fixed test asks a harder question: whether the chosen actions
are unusually well aligned with subsequent dated-contract returns and stable
across years and regimes. A gate failure means the evidence does not support
margin-history work or a capital-constrained cocoa simulation at this stage.
The appropriate deliverable is a negative result plus a genuinely prospective
observation plan, not another retrospective model search.
