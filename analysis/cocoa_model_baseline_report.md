# Cocoa model baseline v1

## Result

The development-ranked candidate is **weather_market_logistic**, with development Sharpe +0.48, net return 67.49%, and 4/5 positive forward folds.

Adding weather to the best market-only specification changes development Sharpe by +0.27. This comparison—not the largest individual model result—is the primary test of whether the climate data adds cocoa timing information.

The leading candidate still trails its expanding-training-mean long/short baseline in net return (67.49% versus 82.40%) and falls below the 0.50 Sharpe threshold used in the prior final-validation protocol.

No candidate is eligible for promotion from this round. It already misses those preliminary performance checks; the 2024+ period is also a consumed diagnostic, dated ICE margin history is not yet attached, and uncertainty tests have not yet been run.

## Data and execution

- Dated contracts: 75 across 2011-10-03–2026-10-12 (28,802 rows).
- Weekly decisions: 781; development through 2023: 637; consumed 2024+: 144.
- Entry/exit: next same-contract open to the open five sessions later; 10-business-day roll buffer.
- Cost used for selection: 10.00 bps round trip, the development-only median of one ICE tick plus $2.50 commission per side.
- Weather coverage at decisions: 5 countries, joined by publication date.

## Candidate comparison

| Candidate | Dev trades | Dev net | Dev Sharpe | Positive folds | Dev R² | Consumed 2024+ net | Consumed Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| weather_market_logistic | 189 | 67.49% | +0.48 | 4/5 | n/a | -259.47% | -1.37 |
| weather_only_boosting | 431 | 74.42% | +0.31 | 4/5 | -0.13 | 70.18% | +0.72 |
| market_only_logistic | 201 | 33.13% | +0.20 | 3/5 | n/a | -230.16% | -1.22 |
| weather_market_ridge | 242 | 20.20% | +0.13 | 3/5 | -0.62 | -148.24% | -0.83 |
| weather_only_logistic | 418 | 20.29% | +0.09 | 3/5 | n/a | -105.49% | -1.30 |
| weather_market_boosting | 218 | 9.44% | +0.06 | 3/5 | -0.21 | 96.75% | +0.90 |
| market_only_ridge | 164 | 0.65% | +0.00 | 3/5 | -0.06 | -87.41% | -0.55 |
| weather_only_ridge | 321 | -47.59% | -0.23 | 1/5 | -0.08 | 48.76% | +0.26 |
| market_only_boosting | 295 | -49.28% | -0.24 | 2/5 | -0.21 | -18.65% | -0.13 |

## Interpretation guardrails

- Candidate selection and threshold tuning use pre-2024 data only.
- The 2024 cocoa shock is not an untouched holdout and cannot validate the model.
- R² is secondary: the decision objective is net return after the stated cost.
- Missing dated margin prevents a capital-constrained performance claim.
- A follow-up must freeze one specification before bootstrap, circular-shift, double-cost, and margin-constrained tests.
