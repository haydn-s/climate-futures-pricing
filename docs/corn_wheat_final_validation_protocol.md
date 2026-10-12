# Corn/Wheat Final Validation Protocol

## Purpose

This is the last bounded retrospective model-development round before the
project either advances a new corn/wheat paper candidate or concludes that the
tested public-weather strategy did not demonstrate a robust tradable edge.

The protocol was locked on October 12, 2026. Earlier model results and the
2024–2026 outcome history were already visible, so this is not a new pristine
holdout. Its protection comes from a finite search space, development-only
selection, explicit null tests, and a prospective campaign after the result.
The machine-readable contract is
[`config/corn_wheat_final_validation.yaml`](../config/corn_wheat_final_validation.yaml).

## Selection boundary

- Development and every selection decision end in calendar year 2023.
- Calendar years 2024 onward are a consumed diagnostic sample. They are printed
  only after ranking and cannot make a candidate pass or fail.
- Outer evaluation uses expanding forward folds with a one-decision embargo.
- Hyperparameters and action thresholds are selected in an inner trailing block
  with its own embargo.
- Features retain their point-in-time construction. Market features stop before
  the decision close, and CPC fields remain delayed one session where release
  time is unknown.

## Trading-aligned outcomes

Targets come from dated LSEG ZC and ZW contracts, not continuous Yahoo returns.
Each decision selects contracts outside the configured roll window, enters at
the next shared session open, exits after five additional shared sessions, and
charges the configured commission and slippage. A second decision cannot open
while the first is still active.

The candidate set covers the corn-minus-wheat spread, corn alone, and wheat
alone. Regression models predict the next-open gross return and are thresholded
against training-only validation economics. Three-way classifiers learn BUY,
HOLD, and SELL labels whose neutral region includes the realized round-trip cost
plus a fixed 25-basis-point buffer.

The raw-wheat target is a control using the same corn-centered weather and crop-
stage information set. It is not a wheat-specific agronomic model and must not
be described as evidence that these features explain wheat production shocks.

Only elastic net, shallow histogram boosting, and multinomial logistic
regression are allowed. Adding features, model families, thresholds, or a new
target after reviewing results requires a new protocol version.

## Reference strategies and uncertainty

Every target is compared with no-trade, long-only, short-only, trailing-mean,
seasonal-mean, momentum, and mean-reversion rules under the same contracts,
costs, and overlap policy.

Uncertainty is evaluated two ways:

- a calendar-year block bootstrap measures dependence on a small number of
  seasons; and
- a within-year circular-shift null preserves the number and clustering of
  out-of-sample actions while breaking their alignment with subsequent returns.

## Acceptance gate

A candidate must pass every development-only condition in the YAML protocol:
at least 30 executed trades, Sharpe above 0.50, positive net performance,
positive performance in at least 60% of folds and years, superiority to the
best same-target simple rule in both net return and Sharpe, positive performance
with doubled costs, at least 80% positive year-block bootstrap draws, a
circular-shift p-value no greater than 10%, and drawdown no worse than 25%.

Passing would qualify a versioned model for prospective paper observation. It
would not authorize live orders or capital. If nothing passes, the correct
result is `NO ROBUST EDGE DEMONSTRATED`; the existing frozen v1 signal may still
be observed prospectively to test whether that negative conclusion changes on
genuinely unseen decisions.

## Prospective evidence

The prospective campaign begins October 12, 2026 and uses the immutable
decision/outcome ledger documented in
[`docs/prospective_paper_trading.md`](prospective_paper_trading.md). The model
cannot be tuned for performance until at least 30 eligible decisions and one
complete growing season have been recorded. Operational defects may stop the
campaign, but they must not be silently converted into backfilled prospective
observations.

