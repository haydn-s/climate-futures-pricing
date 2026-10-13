# Operational GEFS Corn Revision Validation

## Decision

**The predeclared development gate passes. The sealed 2026 holdout may now be evaluated once, without changing the specification.**

The development-only headline is `all_gefs_plus_market_shallow_boosting`: 42 non-overlapping trades, +42.0% net, Sharpe +1.57, and 3/3 positive years.

## Locked candidate comparison

| Candidate | Features | Trades | Hit | Net | Sharpe | +folds | +years |
|---|---:|---:|---:|---:|---:|---:|---:|
| `all_gefs_plus_market_shallow_boosting` | 31 | 42 | +59.5% | +42.0% | +1.57 | 3/3 | 3/3 |
| `revision_plus_market_ridge` | 19 | 43 | +62.8% | +40.3% | +1.55 | 3/3 | 3/3 |
| `market_only_ridge` | 7 | 43 | +60.5% | +40.7% | +1.55 | 2/3 | 2/3 |
| `market_only_logistic` | 7 | 41 | +53.7% | +27.6% | +1.44 | 3/3 | 3/3 |
| `absolute_plus_market_ridge` | 19 | 43 | +51.2% | +33.7% | +1.27 | 3/3 | 3/3 |
| `all_gefs_plus_market_ridge` | 31 | 42 | +52.4% | +27.1% | +1.14 | 2/3 | 2/3 |
| `absolute_plus_market_logistic` | 19 | 42 | +59.5% | +27.4% | +1.13 | 3/3 | 3/3 |
| `absolute_plus_market_shallow_boosting` | 19 | 43 | +51.2% | +20.1% | +0.75 | 2/3 | 2/3 |
| `market_only_shallow_boosting` | 7 | 43 | +48.8% | +18.0% | +0.65 | 3/3 | 3/3 |
| `revision_plus_market_logistic` | 19 | 42 | +50.0% | +10.7% | +0.48 | 2/3 | 2/3 |
| `all_gefs_plus_market_logistic` | 31 | 42 | +47.6% | +3.6% | +0.17 | 2/3 | 2/3 |
| `revision_plus_market_shallow_boosting` | 19 | 43 | +51.2% | -1.6% | -0.06 | 2/3 | 2/3 |

## Gate diagnostics

- Headline at doubled costs: +36.4%.
- Fixed heat-minus-rain rule: +7.1% net, Sharpe +0.26.
- Revision shifted forward one operational day: +28.7% net.
- Revision shifted backward one operational day: +18.8% net.
- Calendar-year block probability of positive net: 100.0%.

All locked development checks passed.

## Information boundary

- Development feature coverage: 2021-05-02 through 2025-08-31.
- Candidate identity is ranked only on the 2023–2024 outer predictions; 2025 is the final development confirmation fold.
- Hyperparameter selection remains nested inside each earlier training block.
- 2026 is not scored, returned, or used by this script.
- Entries use the first dated LSEG open after the 07 UTC model cutoff; overlapping positions are suppressed.
- Row-level licensed inputs and predictions remain under ignored `data/`; only aggregate results are written here.

## Reproducibility

- Protocol SHA-256: `851875c0e667fc2f9fb3204fbbfb0188e931bf4d3771763a0372f6e8021d35f2`
- Model-frame SHA-256: `3cf1699e4a7231e3e599631aec312473faa2c516c475620796c03735b94ebb26`
- Orders authorized or submitted: **none**
