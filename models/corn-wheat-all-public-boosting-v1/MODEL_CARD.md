# Corn–Wheat Public-Data Signal v1

## Status

This is the frozen primary candidate from `ml-signal-baseline-v1`. It passed the
project's explicit research gate and may be used for prospective paper trading.
It is **not approved for live orders or capital allocation**.

The 2024–2026 holdout has been reviewed and is consumed. It must not be reused
to select features, models, parameters, or thresholds. A changed specification
requires a new model version and genuinely unseen evaluation data.

## Intended use

Once per eligible US growing-season week, estimate the next five-session return
of corn minus wheat and emit one of three research signals:

- `BUY`: paper long corn and short wheat.
- `SELL`: paper short corn and long wheat.
- `HOLD`: no position.

The command records a recommendation only. It has no brokerage integration and
sets `execution.authorized` to `false` in every output.

## Frozen specification

| Item | Value |
|---|---|
| Model version | `corn-wheat-all-public-boosting-v1` |
| Target | Five-session corn return minus wheat return |
| Model | Shallow histogram gradient boosting |
| Features | 32 crop-stage, weather, drought, CPC, and lagged price-regime variables |
| Decision cadence | Non-overlapping US Drought Monitor publication weeks, April–October |
| Costs | 5 bps per leg; 10 bps for the two-leg spread |
| Model/threshold selection | Trailing validation block inside permitted training history |
| Data timing | Market features exclude the entry close; CPC inputs are delayed one session |

The exact feature list, research commit, data hashes, candidate grid, and
evaluation metrics are frozen in [`baseline.json`](baseline.json).

## Evidence

The selected configuration produced development Sharpe 0.81 across four
positive forward folds, with a 24.2% maximum drawdown after costs. In the fixed
2024–2026 holdout it made 17 trades, returned 11.3%, achieved Sharpe 0.88, and
had a 9.1% maximum drawdown.

Those strategy metrics do not imply broad predictive accuracy. Development
out-of-sample R² is −0.074, and every regression configuration in the benchmark
has negative R². The candidate appears useful only after thresholding its tails.

## Inference and audit trail

Run from an existing processed archive:

```bash
PYTHONPATH=src python -m pipeline signal --dry-run
PYTHONPATH=src python -m pipeline signal --json
```

The default command trains on every eligible outcome that ended strictly before
the selected decision date. A saved run writes an immutable JSON record and a
serialized fitted model under the ignored `data/signals/` and `data/models/`
directories. The record contains:

- decision and market-data dates;
- model score, selected threshold, action, and spread legs;
- training range and the last known outcome date;
- feature names and any imputed inputs;
- hashes and coverage metadata for the local archive;
- a hash of the serialized model artifact; and
- an explicit statement that no orders were submitted.

If the latest eligible decision is more than ten calendar days old, the command
preserves the model's raw recommendation for diagnosis but forces the external
action to `HOLD` and marks the record `STALE_INPUTS`.

## Current frozen-archive behavior

With `--as-of 2026-10-09`, the latest eligible decision is September 24, 2026,
using market data through September 23. The fitted model reads `SELL`, but the
input is 15 days old, so the safe emitted action is `HOLD`.

## Limitations and prohibited uses

- Yahoo continuous series do not model executable contracts, rolls, margins,
  liquidity, or fills. Do not infer live P&L from these returns.
- The holdout contains only 17 candidate trades and has already influenced the
  decision to advance this model.
- Public source histories can be revised. Each inference record fingerprints its
  local archive, but a later rebuild may not reproduce old provider bytes unless
  the raw archive is preserved.
- CPC coverage begins in 2012. Earlier observations do not belong to this model.
- Missing inputs are imputed from permitted training history and are disclosed in
  the record. Stale or structurally missing inputs should block paper execution.
- This model must not place orders, provide personalized financial advice, or be
  represented as validated for live trading.

## Prospective validation

Freeze v1 during its initial paper-trading period. Log every eligible signal
before its outcome is known, investigate operational failures without changing
the specification, and defer performance-driven tuning until a meaningful unseen
sample—preferably a complete growing season—has accumulated.
