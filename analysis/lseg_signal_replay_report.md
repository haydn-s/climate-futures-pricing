# Frozen v1 LSEG Contract Replay

## Preliminary decision

**The frozen v1 signal survives in only one evaluation segment under the base LSEG next-open assumptions; effectiveness is not stable.**

This is an execution and data-source validation of the already selected
`corn-wheat-all-public-boosting-v1` specification. The model family, 32 features, five-session
target, nested folds, fitted thresholds, and actions are unchanged. The
2024–2026 holdout is already consumed; replacing Yahoo outcomes with LSEG
contracts does not create a new independent holdout.

## Base comparison

| Sample | Outcome | Obs. | Trades | Hit | Net | Sharpe | Max drawdown |
|---|---|---:|---:|---:|---:|---:|---:|
| development | Yahoo continuous close | 212 | 132 | 56.1% | 72.8% | +0.81 | -24.2% |
| development | LSEG dated settlement | 212 | 132 | 55.3% | 40.1% | +0.52 | -21.9% |
| development | LSEG next-open execution | 212 | 83 | 47.0% | -7.1% | -0.12 | -25.1% |
| holdout | Yahoo continuous close | 73 | 17 | 64.7% | 11.3% | +0.88 | -9.1% |
| holdout | LSEG dated settlement | 73 | 17 | 64.7% | 11.6% | +0.99 | -8.2% |
| holdout | LSEG next-open execution | 73 | 17 | 64.7% | 7.3% | +0.68 | -9.3% |

Yahoo rows reproduce the original baseline. LSEG dated settlement keeps the
same decision and outcome dates and charges the original 10 bps two-leg cost.
LSEG next-open execution enters on the next shared session, exits after five
further shared sessions, sizes an integer approximately dollar-neutral spread,
and charges $2.50 commission plus one tick of slippage per contract side.
Returns use average leg notional, not unavailable historical margin.
Net is the arithmetic sum of per-decision returns, matching the baseline;
the aggregate CSV also reports the compounded path.

## Target agreement

| Sample | Obs. | Close corr. | Close sign | Signal close sign | Next-open corr. | Signal next-open sign |
|---|---:|---:|---:|---:|---:|---:|
| all | 285 | +0.901 | 97.2% | 98.0% | +0.884 | 94.0% |
| development | 212 | +0.882 | 96.2% | 97.7% | +0.864 | 93.2% |
| holdout | 73 | +0.980 | 100.0% | 100.0% | +0.969 | 100.0% |

Close agreement isolates the effect of replacing the continuous series with
dated contracts. Next-open agreement also includes the deliberately conservative
one-session timing shift and integer contract selection.
Signal-sign columns include every frozen non-HOLD decision; performance rows
separately apply the configured no-overlap execution policy.

## Predeclared sensitivity

Cost cases are zero; base ($2.50 plus one tick per contract side); and stress
($5 plus two ticks). No row in this grid changes the model or its threshold.

| Roll days | Costs | Sample | Trades | Net | Sharpe | Drawdown |
|---:|---|---|---:|---:|---:|---:|
| 5 | zero | development | 83 | 16.2% | +0.26 | -14.2% |
| 5 | zero | holdout | 17 | 11.6% | +1.05 | -8.1% |
| 5 | base | development | 83 | -5.9% | -0.10 | -25.2% |
| 5 | base | holdout | 17 | 7.4% | +0.69 | -9.3% |
| 5 | stress | development | 83 | -28.0% | -0.46 | -35.8% |
| 5 | stress | holdout | 17 | 3.3% | +0.31 | -10.4% |
| 10 | zero | development | 83 | 15.0% | +0.25 | -16.0% |
| 10 | zero | holdout | 17 | 11.5% | +1.05 | -8.1% |
| 10 | base | development | 83 | -7.1% | -0.12 | -25.1% |
| 10 | base | holdout | 17 | 7.3% | +0.68 | -9.3% |
| 10 | stress | development | 83 | -29.1% | -0.48 | -35.7% |
| 10 | stress | holdout | 17 | 3.2% | +0.30 | -10.4% |
| 15 | zero | development | 83 | 20.7% | +0.35 | -14.4% |
| 15 | zero | holdout | 17 | 11.9% | +1.08 | -8.1% |
| 15 | base | development | 83 | -1.3% | -0.02 | -22.1% |
| 15 | base | holdout | 17 | 7.8% | +0.72 | -9.3% |
| 15 | stress | development | 83 | -23.3% | -0.39 | -33.2% |
| 15 | stress | holdout | 17 | 3.7% | +0.35 | -10.4% |

## Coverage and audit

- Frozen out-of-sample decisions: 285 (212 development; 73 consumed holdout).
- Signals: 149; signals suppressed by the no-overlap policy: 49.
- Unavailable dated-settlement outcomes: 0; unavailable next-open outcomes: 0.
- Median entry notional imbalance: 0.57%.
- Contract panel SHA-256: `03162fe8ead6dc7b216d4d36483a61ea0f2364575e7ec1f53eead27ecbc1aa8a`.
- Yahoo price panel SHA-256: `3503fe1ad763a36c540adf749c1727d59b434dd166f22bf3257a003ac76f7ffc`.
- The detailed licensed ledger remains beneath ignored `data/analysis/` and
  must not be committed or redistributed as row-level LSEG-derived data.

## Interpretation

This replay can falsify the hypothesis that v1's apparent edge is purely a
Yahoo continuous-contract or roll artifact. It cannot establish a fresh
out-of-sample edge because the dates and underlying market outcomes are the
same history already reviewed during model selection. Dated maintenance margin
is still required before reporting return on capital or enforcing a capital
budget. The next honest evidence after this study is prospective paper trading
on decisions logged before their outcomes are known.
