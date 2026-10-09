# ML Signal Baseline

## Decision

**A market-neutral configuration improves on the raw-return baseline and clears the explicit paper-trading gate.**

Best raw configuration: raw corn / stage_weather_market / ridge (5d).

Best market-neutral configuration: corn minus wheat / all_public / boosting (5d).

## Modeling contract

- Decisions occur once per Drought Monitor publication week, on the first
  tradable date on or after publication.
- Targets are raw corn, corn minus wheat, and corn minus a trailing-beta
  soybean/wheat basket.
- Crop-stage and weather features use point-in-time values. Price regimes
  exclude the target-entry close; CPC forecasts are delayed one session
  because the archive has issuance dates but not release times.
- Models are Ridge regression, multinomial logistic regression, and shallow
  histogram gradient boosting.
- Hyperparameters and buy/sell/hold thresholds are selected in an inner
  trailing validation block. Outer folds move forward with an embargo.
- Calendar years 2024 onward are excluded from all model and
  threshold selection, then evaluated once as a fixed holdout.
- Costs are 5 basis points per traded leg; raw
  signals carry one leg and spreads carry two.
- Longer horizons are thinned so adjacent decision labels do not overlap.

Coverage: 5d: 696 weekly decisions, 2000-07-17–2026-09-24.

Feature sets: stage_weather (15); stage_weather_market (23); all_public (32).
Forecast-enhanced configurations begin with CPC coverage in 2012; missing
cells inside the covered period are imputed within each training fold.

## Acceptance gate

A configuration passes only with at least 30 development trades, development
Sharpe above 0.50, positive net return in at least 60% of forward folds,
positive development return excluding 2012, and net returns above a constant
position chosen from the training mean in both development and holdout.
Development drawdown must be no worse than -25%, holdout drawdown no worse
than -15%, and the holdout must contain at least three trades with positive
net return.
Passing is necessary for paper trading, not
evidence sufficient for live capital.

## Comparison

| Target | Features | Model | Dev R² | Dev Sharpe | Trades | Hit | Dev net | vs constant | Holdout net | vs constant | Holdout Sharpe | Gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| corn minus beta-adjusted ag basket | all_public | boosting | -0.146 | +0.21 | 116 | 53.4% | 13.7% | 22.9% | -4.3% | -4.3% | -0.20 | fail |
| corn minus beta-adjusted ag basket | all_public | logistic | — | +0.44 | 175 | 54.3% | 35.2% | 44.4% | 5.4% | 5.4% | +0.36 | fail |
| corn minus beta-adjusted ag basket | all_public | ridge | -0.113 | +0.51 | 106 | 58.5% | 32.8% | 42.0% | 1.0% | 1.0% | +0.14 | PASS |
| corn minus beta-adjusted ag basket | stage_weather | boosting | -0.179 | -0.41 | 405 | 49.1% | -86.2% | -82.2% | -13.2% | -3.9% | -0.59 | fail |
| corn minus beta-adjusted ag basket | stage_weather | logistic | — | -0.01 | 185 | 45.9% | -1.5% | 2.5% | -17.4% | -8.2% | -0.76 | fail |
| corn minus beta-adjusted ag basket | stage_weather | ridge | -0.025 | +0.10 | 303 | 49.8% | 18.7% | 22.7% | 10.5% | 19.8% | +0.66 | fail |
| corn minus beta-adjusted ag basket | stage_weather_market | boosting | -0.176 | -0.24 | 200 | 46.5% | -36.6% | -32.6% | 3.0% | 12.3% | +0.61 | fail |
| corn minus beta-adjusted ag basket | stage_weather_market | logistic | — | +0.42 | 314 | 50.3% | 76.9% | 80.9% | -14.8% | -5.6% | -0.65 | fail |
| corn minus beta-adjusted ag basket | stage_weather_market | ridge | -0.031 | +0.22 | 271 | 50.2% | 39.4% | 43.4% | 0.9% | 10.2% | +0.05 | fail |
| corn minus wheat | all_public | boosting | -0.074 | +0.81 | 132 | 56.1% | 72.8% | 96.8% | 11.3% | 15.2% | +0.88 | PASS |
| corn minus wheat | all_public | logistic | — | +0.35 | 119 | 51.3% | 36.0% | 60.0% | -5.6% | -1.8% | -0.29 | fail |
| corn minus wheat | all_public | ridge | -0.174 | +0.03 | 135 | 47.4% | 3.2% | 27.2% | -9.0% | -5.1% | -0.74 | fail |
| corn minus wheat | stage_weather | boosting | -0.134 | +0.05 | 227 | 51.1% | 11.4% | -17.2% | -9.2% | -5.3% | -0.34 | fail |
| corn minus wheat | stage_weather | logistic | — | -0.34 | 356 | 49.7% | -92.2% | -120.8% | 21.8% | 25.7% | +1.57 | fail |
| corn minus wheat | stage_weather | ridge | -0.022 | -0.21 | 325 | 48.6% | -51.1% | -79.7% | 22.4% | 26.3% | +1.72 | fail |
| corn minus wheat | stage_weather_market | boosting | -0.124 | +0.14 | 286 | 52.8% | 31.5% | 2.9% | 0.3% | 4.2% | +0.02 | fail |
| corn minus wheat | stage_weather_market | logistic | — | -0.13 | 326 | 50.6% | -32.0% | -60.6% | -0.5% | 3.4% | -0.02 | fail |
| corn minus wheat | stage_weather_market | ridge | -0.038 | +0.14 | 344 | 51.2% | 34.3% | 5.7% | 3.0% | 6.9% | +0.30 | fail |
| raw corn | all_public | boosting | -0.110 | +0.57 | 148 | 50.7% | 60.8% | 95.7% | 10.8% | 10.8% | +0.52 | fail |
| raw corn | all_public | logistic | — | +0.55 | 152 | 49.3% | 55.5% | 90.3% | 0.8% | 0.8% | +0.03 | fail |
| raw corn | all_public | ridge | -0.070 | +0.47 | 140 | 51.4% | 47.5% | 82.3% | -7.0% | -7.0% | -0.41 | fail |
| raw corn | stage_weather | boosting | -0.150 | -0.48 | 374 | 46.3% | -130.6% | -75.2% | 19.6% | 19.6% | +0.65 | fail |
| raw corn | stage_weather | logistic | — | +0.11 | 380 | 51.8% | 31.7% | 87.0% | 14.0% | 14.0% | +0.45 | fail |
| raw corn | stage_weather | ridge | -0.025 | +0.25 | 393 | 50.6% | 69.6% | 125.0% | -4.5% | -4.5% | -0.15 | fail |
| raw corn | stage_weather_market | boosting | -0.171 | -0.39 | 241 | 46.5% | -83.8% | -28.4% | -6.5% | -6.5% | -0.39 | fail |
| raw corn | stage_weather_market | logistic | — | +0.15 | 416 | 49.5% | 43.7% | 99.0% | -5.0% | -5.0% | -0.16 | fail |
| raw corn | stage_weather_market | ridge | -0.045 | +0.66 | 438 | 51.6% | 195.7% | 251.1% | 11.5% | 11.5% | +0.38 | fail |

## Interpretation limits

- This report compares a finite set of predeclared targets, features, and
  small models. It does not establish that no other data or strategy can work.
- Reviewing the holdout result consumes it for future model redesign. Any
  revised specification needs a new untouched period or paper-trading sample.
- Returns are research targets built from continuous Yahoo series. Before
  execution, contract-level rolls, margins, liquidity, and fill assumptions
  require separate validation.
- A PASS is a candidate for paper trading. A failure means the model's honest
  current output is HOLD / NO DEMONSTRATED EDGE.
