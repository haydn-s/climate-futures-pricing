# Frozen v1 LSEG Execution Attribution

## Decision

**The dated-contract target is valid, but the frozen signal does not show a
stable edge after its operational policy and contract costs are applied.**

This is a post-freeze diagnostic. No feature, fitted model, threshold, action,
holding period, or production rule is selected from these results.

## One-change-at-a-time waterfall

| Sample | Stage | Trades | Hit | Net | Change | Sharpe | Drawdown |
|---|---|---:|---:|---:|---:|---:|---:|
| development | Yahoo continuous close, fixed 10 bps | 132 | 56.1% | 72.8% | — | +0.81 | -24.2% |
| development | LSEG dated settlement, fixed 10 bps | 132 | 55.3% | 40.1% | -32.7% | +0.52 | -21.9% |
| development | LSEG next open, overlap allowed, fixed 10 bps | 132 | 55.3% | 37.4% | -2.8% | +0.49 | -22.0% |
| development | LSEG next open, no overlap, fixed 10 bps | 83 | 47.0% | 6.7% | -30.7% | +0.11 | -18.5% |
| development | LSEG next open, no overlap, contract costs | 83 | 47.0% | -7.1% | -13.8% | -0.12 | -25.1% |
| holdout | Yahoo continuous close, fixed 10 bps | 17 | 64.7% | 11.3% | — | +0.88 | -9.1% |
| holdout | LSEG dated settlement, fixed 10 bps | 17 | 64.7% | 11.6% | 0.2% | +0.99 | -8.2% |
| holdout | LSEG next open, overlap allowed, fixed 10 bps | 17 | 64.7% | 9.8% | -1.8% | +0.90 | -8.6% |
| holdout | LSEG next open, no overlap, fixed 10 bps | 17 | 64.7% | 9.8% | 0.0% | +0.90 | -8.6% |
| holdout | LSEG next open, no overlap, contract costs | 17 | 64.7% | 7.3% | -2.4% | +0.68 | -9.3% |

Net and Change are arithmetic sums of per-decision returns, matching the
original benchmark. The first stage has no incremental change by definition.
The 10-bps next-open counterfactual is a measurement bridge, not a claim that
futures commissions are proportional to notional.

## Cost capacity and strata

| Sample | Dimension | Bucket | Trades | Gross hit | Gross | Net | Median break-even/side | Covers base cost |
|---|---|---|---:|---:|---:|---:|---:|---:|
| development | overall | all | 83 | 47.0% | 15.0% | -7.1% | $-9.03 | 45.8% |
| holdout | overall | all | 17 | 64.7% | 11.5% | 7.3% | $42.19 | 58.8% |
| development | direction | SELL | 48 | 45.8% | 18.4% | 5.7% | $-9.63 | 45.8% |
| development | direction | BUY | 35 | 48.6% | -3.4% | -12.8% | $-8.33 | 45.7% |
| holdout | direction | BUY | 6 | 66.7% | 4.7% | 3.3% | $57.55 | 50.0% |
| holdout | direction | SELL | 11 | 63.6% | 6.8% | 4.0% | $42.19 | 63.6% |
| development | confidence | Q4 highest | 21 | 52.4% | 3.5% | -1.8% | $7.95 | 47.6% |
| development | confidence | Q3 | 20 | 35.0% | -6.0% | -11.8% | $-19.57 | 35.0% |
| development | confidence | Q1 lowest | 21 | 52.4% | 14.9% | 9.3% | $64.29 | 52.4% |
| development | confidence | Q2 | 21 | 47.6% | 2.6% | -2.8% | $-57.14 | 47.6% |
| holdout | confidence | Q1 lowest | 5 | 60.0% | -0.0% | -1.3% | $40.62 | 60.0% |
| holdout | confidence | Q3 | 4 | 50.0% | 1.3% | 0.4% | $19.70 | 50.0% |
| holdout | confidence | Q4 highest | 4 | 75.0% | 8.3% | 7.3% | $176.22 | 75.0% |
| holdout | confidence | Q2 | 4 | 75.0% | 1.9% | 0.9% | $22.14 | 50.0% |
| development | roll_selection | nearest contracts | 55 | 43.6% | 10.7% | -4.1% | $-30.11 | 41.8% |
| development | roll_selection | rolled beyond nearest | 28 | 53.6% | 4.3% | -3.0% | $40.27 | 53.6% |
| holdout | roll_selection | nearest contracts | 11 | 63.6% | 7.6% | 5.0% | $40.62 | 54.5% |
| holdout | roll_selection | rolled beyond nearest | 6 | 66.7% | 3.8% | 2.4% | $77.60 | 66.7% |

Break-even is the signed gross P&L divided by round-trip contract sides.
The base all-in assumption is $15 per contract side: $2.50 commission plus
one $12.50 tick. A negative break-even means the trade lost before costs.
Year and seasonal aggregates are retained in the machine-readable strata file.

## Year-block uncertainty

| Sample | Years | Observed net | 95% block interval | Probability positive |
|---|---:|---:|---:|---:|
| development | 9 | -7.1% | -42.5% to 37.1% | 34.5% |
| holdout | 3 | 7.3% | 3.2% to 13.4% | 100.0% |

The block bootstrap resamples calendar-year clusters and quantifies how fragile
the observed path is. It does not repair the consumed holdout or create a new
independent test.
The holdout's 100% positive bootstrap frequency is mechanical: each of its only
three observed calendar-year blocks is positive, so their resamples are too.

## Audit boundary

- Decisions: 285; original signals: 149; executed trades: 100.
- Suppressed overlaps: 49.
- Median notional imbalance: 0.57%.
- Contract panel SHA-256: `03162fe8ead6dc7b216d4d36483a61ea0f2364575e7ec1f53eead27ecbc1aa8a`.
- Row-level LSEG-derived ledgers remain ignored under `data/analysis/`.
- This attribution remains normalized by average leg notional; the separate
  dated-margin study reports return on capital for the public CME window.

## Next evidence

Freeze v1 and log decisions before outcomes exist. The prospective ledger—not
another retrospective threshold or model search—is the next valid effectiveness
test. Any v2 specification should be pre-registered and evaluated on a later,
untouched growing season.
