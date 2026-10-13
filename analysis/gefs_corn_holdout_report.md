# Operational GEFS Corn: Sealed 2026 Holdout

## Decision

**The frozen GEFS candidate fails the one-time 2026 holdout assessment.**

`all_gefs_plus_market_shallow_boosting` made 14 non-overlapping trades, returned -7.3% net after modeled costs, recorded +42.9% hit rate, and had Sharpe -0.98.

## Frozen comparisons

| Specification | Trades | Hit | Net | Sharpe |
|---|---:|---:|---:|---:|
| Frozen GEFS candidate | 14 | +42.9% | -7.3% | -0.98 |
| Same-model market-only | 14 | +57.1% | -6.7% | -0.83 |
| Fixed heat-minus-rain rule | 14 | +50.0% | +0.5% | +0.07 |

At doubled costs, the frozen candidate returns -9.1%.

Failed predeclared checks:

- positive holdout net
- positive holdout net at doubled costs
- improves on market-only holdout net
- improves on market-only holdout Sharpe
- beats fixed revision rule holdout net

## Freeze and information boundary

- Frozen headline: `all_gefs_plus_market_shallow_boosting`
- Final training-selected parameter: `{learning_rate: 0.1, max_iter: 100, max_leaf_nodes: 15}`
- Pre-reveal Git commit: `b4661fb88add57ffe9fb5fc9681d814e1135ed31`
- Protocol SHA-256: `ab5f198f9d3441da240456b3a3cfe4e79319ad94a62fe202427c31f1498b2dc1`
- Model-frame SHA-256: `3cf1699e4a7231e3e599631aec312473faa2c516c475620796c03735b94ebb26`
- 2026 was excluded from candidate ranking, hyperparameter evaluation, development gating, and every prior aggregate report.
- This report is a one-time diagnostic. The specification must not be retuned against this holdout.
- Orders authorized or submitted: **none**
