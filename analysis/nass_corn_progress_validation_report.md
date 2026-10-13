# USDA Crop Progress Corn Validation

## Decision

**The predeclared development gate fails. The USDA Crop Progress avenue does not advance, and the model-specific 2026 holdout remains sealed.**

The development-only headline is `progress_plus_market_logistic`: 131 non-overlapping trades, -52.7% net, Sharpe -0.81, and 3/9 positive years.

## Locked candidate comparison

| Candidate | Features | Trades | Hit | Net | Sharpe | +folds | +years |
|---|---:|---:|---:|---:|---:|---:|---:|
| `market_only_ridge` | 7 | 132 | +48.5% | -9.6% | -0.16 | 5/9 | 5/9 |
| `progress_plus_market_shallow_boosting` | 35 | 144 | +51.4% | -16.5% | -0.26 | 4/9 | 4/9 |
| `market_only_shallow_boosting` | 7 | 144 | +49.3% | -34.0% | -0.46 | 3/9 | 3/9 |
| `all_usda_plus_market_shallow_boosting` | 44 | 149 | +52.3% | -37.9% | -0.53 | 5/9 | 5/9 |
| `progress_plus_market_ridge` | 35 | 145 | +51.0% | -52.7% | -0.76 | 4/9 | 4/9 |
| `progress_plus_market_logistic` | 35 | 131 | +44.3% | -52.7% | -0.81 | 3/9 | 3/9 |
| `all_usda_plus_market_logistic` | 44 | 137 | +49.6% | -53.5% | -0.83 | 4/9 | 4/9 |
| `condition_plus_market_ridge` | 16 | 139 | +48.9% | -58.6% | -0.87 | 4/9 | 4/9 |
| `condition_plus_market_logistic` | 16 | 117 | +50.4% | -56.8% | -0.97 | 3/9 | 3/9 |
| `market_only_logistic` | 7 | 116 | +51.7% | -53.5% | -0.98 | 3/9 | 3/9 |
| `condition_plus_market_shallow_boosting` | 16 | 147 | +49.0% | -78.4% | -1.16 | 3/9 | 3/9 |
| `all_usda_plus_market_ridge` | 44 | 147 | +44.9% | -94.7% | -1.35 | 2/9 | 2/9 |

## Gate diagnostics

- Headline at doubled costs: -70.9%.
- Fixed condition-deterioration rule: -75.5% net, Sharpe -2.09.
- USDA features shifted forward one within-year release: -49.3% net.
- USDA features shifted backward one within-year release: -39.5% net.
- Calendar-year block probability of positive net: 4.3%.

Failed checks:

- positive development net
- positive at doubled costs
- positive in enough outer folds
- positive in enough calendar years
- year-block probability positive meets threshold
- beats forward-shift placebo net
- beats backward-shift placebo net

## Information boundary

- Development feature coverage: 2012-04-02 through 2025-11-24.
- Candidate identity is ranked only on the 2017–2023 outer predictions; 2024–2025 are final development confirmation years.
- Hyperparameter selection remains nested inside each earlier training block.
- 2026 is not scored, returned, or used by this script.
- Entries use the first dated LSEG ZC session open strictly after the 4:00 p.m. Eastern USDA release; overlapping positions are suppressed.
- Row-level licensed inputs and predictions remain under ignored `data/`; only aggregate results are written here.

## Reproducibility

- Protocol SHA-256: `c5da9642e3da8098735d3400ab51b49fadfd227f0ede280e3afc6d39149c460b`
- Model-frame SHA-256: `6a99a16a7ece36e8e39370e3481f7dd1644f465cca004714e9246506a737aa2f`
- Orders authorized or submitted: **none**
