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

## Post-freeze LSEG validation

The October 12, 2026 validation replaces the continuous-series outcome with
licensed dated ZC/ZW contracts while keeping the model, features, threshold,
signals, and evaluation dates frozen. Same-date LSEG settlement targets have
0.901 correlation and 97.2% sign agreement with the Yahoo targets across all
285 out-of-sample decisions, so the original result is not explained solely by
a continuous-series direction or roll artifact.

The more conservative execution replay enters at the next shared session open,
exits after five further sessions, suppresses overlapping positions, and charges
$2.50 commission plus one tick of slippage per contract side. Development
performance is −7.1% net with Sharpe −0.12 across 83 executed trades; the
already-consumed holdout is +7.3% with Sharpe 0.68 across 17 trades. Development
gross returns are positive under every tested roll window, but base and stress
costs erase them. This is evidence against stable executable effectiveness, not
a reason to tune v1 against the consumed history.

The original replay uses average leg notional so its full 2015-2026 history is
not a return-on-capital backtest. A separate dated-margin replay covers the
official CME public window from June 30, 2020 through June 30, 2026. Every tested
development capital path is negative; the least-negative result is −3.7% on
$100,000 of starting capital. Detailed methods and the sensitivity grid are in
[`../../analysis/lseg_signal_replay_report.md`](../../analysis/lseg_signal_replay_report.md).
The capital results and source hashes are in
[`../../analysis/lseg_capital_simulation_report.md`](../../analysis/lseg_capital_simulation_report.md).

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

The enforced workflow is documented in
[`../../docs/prospective_paper_trading.md`](../../docs/prospective_paper_trading.md).
It writes an immutable same-day decision record before entry data exist, then
appends a separately hash-linked outcome after the five-session horizon. Late or
backfilled registrations are rejected rather than relabeled as prospective.

The final execution-aligned development round was locked and run on October 12,
2026. It compared ten finite model specifications with seven simple rules using
actual next-open LSEG outcomes. No candidate passed every development-only gate,
so no v2 was promoted. The strongest diagnostic candidate was a wheat elastic
net, but it was positive in only two of four forward folds. V1 remains frozen
only so future observations can falsify or reinforce the negative retrospective
conclusion; it has not regained deployment status.

The campaign is recorded in [`prospective_campaign.json`](prospective_campaign.json).
Its first registration is an ineligible `OPERATIONAL_HOLD`: the latest archived
decision was already 18 days old. This verifies that the ledger refuses to call
a late decision prospective. The first evidentiary observation must be a future
same-day registration.
