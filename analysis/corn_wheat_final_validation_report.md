# Corn/Wheat Final Execution-Aligned Validation

## Decision

**No predeclared candidate clears the development-only acceptance gate. The final retrospective conclusion is NO ROBUST EDGE DEMONSTRATED.**

This result is narrower than saying weather never matters. It says that none
of the finite, point-in-time strategies in protocol
`corn-wheat-final-validation-v1` established a repeatable edge after dated-contract
entry, costs, and the no-overlap policy.

## Locked protocol and audit boundary

- Protocol locked: 2026-10-12; SHA-256 `db33f7f30e1545707c003cb92bcea9bb1f6233394767575c436fe52df837469c`.
- Execution-label coverage: 2012-04-05 through 2026-09-17.
- Selection sample: calendar years through 2023 only.
- Calendar years 2024 onward are consumed diagnostics and never enter ranking
  or any acceptance condition.
- Labels use next-shared-session LSEG opens, dated ZC/ZW contracts, five
  additional holding sessions, exact row costs, and no overlapping positions.
- Contract-panel SHA-256: `03162fe8ead6dc7b216d4d36483a61ea0f2364575e7ec1f53eead27ecbc1aa8a`.
- Row-level licensed outcomes remain in ignored `data/analysis/`; committed
  outputs are aggregates only.

## Candidate results — development selection sample

| Candidate | Target | Trades | Hit | Net | Sharpe | +years | 2× cost | Bootstrap + | Null p | Gate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `wheat_public_elastic_net` | wheat | 72 | 56.9% | 52.2% | +0.70 | 6/9 | 44.3% | 93.8% | 2.9% | fail |
| `wheat_public_boosting` | wheat | 80 | 56.2% | 37.7% | +0.57 | 7/9 | 28.9% | 93.2% | 19.5% | fail |
| `wheat_public_three_way` | wheat | 74 | 54.1% | 17.5% | +0.22 | 4/9 | 9.1% | 69.0% | 24.4% | fail |
| `corn_public_elastic_net` | corn | 61 | 57.4% | 14.6% | +0.22 | 5/9 | 5.6% | 77.4% | 23.6% | fail |
| `spread_public_elastic_net` | corn minus wheat | 62 | 50.0% | 2.6% | +0.05 | 4/9 | -14.4% | 53.9% | 20.6% | fail |
| `spread_public_three_way` | corn minus wheat | 81 | 53.1% | -5.6% | -0.09 | 4/9 | -25.8% | 39.4% | 31.4% | fail |
| `spread_public_boosting` | corn minus wheat | 55 | 49.1% | -6.5% | -0.15 | 5/9 | -20.7% | 40.1% | 25.5% | fail |
| `spread_weather_elastic_net` | corn minus wheat | 51 | 54.9% | -7.3% | -0.16 | 4/9 | -21.2% | 34.4% | 48.3% | fail |
| `corn_public_three_way` | corn | 98 | 49.0% | -21.4% | -0.28 | 3/9 | -34.9% | 30.3% | 54.5% | fail |
| `corn_public_boosting` | corn | 84 | 41.7% | -42.9% | -0.81 | 3/9 | -54.8% | 3.1% | 85.0% | fail |

Net is the arithmetic sum of per-decision returns. The null p-value compares
the observed alignment with 2,000 within-year circular shifts of the same raw
actions. Bootstrap + is the share of calendar-year block resamples above zero.
No candidate is selected from the diagnostic columns below.
The raw-wheat candidates are controls using the same corn-centered weather and
crop-stage panel; they are not wheat-specific agronomic models.

## Best simple rule for each target

| Target | Best rule | Trades | Net | Sharpe | +years |
|---|---|---:|---:|---:|---:|
| corn minus wheat | `mean_reversion` | 115 | 19.0% | +0.26 | 4/9 |
| corn | `momentum` | 114 | 86.3% | +1.14 | 6/9 |
| wheat | `seasonal_mean` | 103 | 48.5% | +0.58 | 5/9 |

The gate compares every model only with the strongest same-target rule ranked
by development Sharpe and then net return. All seven rules remain in the CSV.

## Stability of headline candidate `wheat_public_elastic_net`

| Dimension | Bucket | Trades | Hit | Net | Mean/trade |
|---|---|---:|---:|---:|---:|
| year | 2015 | 1 | 100.0% | 4.9% | 4.9% |
| year | 2016 | 13 | 61.5% | 12.8% | 1.0% |
| year | 2017 | 13 | 46.2% | -4.1% | -0.3% |
| year | 2018 | 6 | 66.7% | 5.5% | 0.9% |
| year | 2019 | 6 | 33.3% | -6.6% | -1.1% |
| year | 2020 | 2 | 100.0% | 5.9% | 2.9% |
| year | 2021 | 5 | 20.0% | -9.5% | -1.9% |
| year | 2022 | 13 | 69.2% | 15.0% | 1.2% |
| year | 2023 | 13 | 61.5% | 28.3% | 2.2% |
| direction | BUY | 36 | 55.6% | 15.0% | 0.4% |
| direction | HOLD | 0 | — | 0.0% | — |
| direction | SELL | 36 | 58.3% | 37.2% | 1.0% |
| market_regime | negative momentum | 37 | 59.5% | 28.4% | 0.8% |
| market_regime | positive momentum | 35 | 54.3% | 23.8% | 0.7% |
| market_regime | flat | 0 | — | 0.0% | — |

The headline is the best passing candidate when one exists; otherwise it is
the highest-development-Sharpe diagnostic candidate. That fallback is not a
promotion. Its failed gate conditions are:

- positive in at least 60% of folds

## Consumed 2024+ diagnostic

| Candidate | Trades | Net | Sharpe | Drawdown |
|---|---:|---:|---:|---:|
| `wheat_public_elastic_net` | 19 | 14.7% | +0.84 | -4.1% |
| `wheat_public_boosting` | 10 | -10.5% | -0.64 | -19.9% |
| `wheat_public_three_way` | 34 | 5.2% | +0.25 | -11.3% |
| `corn_public_elastic_net` | 5 | 2.2% | +0.22 | -6.6% |
| `spread_public_elastic_net` | 6 | 1.6% | +0.16 | -4.6% |
| `spread_public_three_way` | 4 | 9.1% | +1.48 | 0.0% |
| `spread_public_boosting` | 15 | 9.0% | +0.72 | -4.6% |
| `spread_weather_elastic_net` | 28 | -1.6% | -0.08 | -12.1% |
| `corn_public_three_way` | 34 | -17.2% | -0.86 | -26.4% |
| `corn_public_boosting` | 7 | -7.9% | -0.87 | -11.7% |

These rows answer whether behavior persisted, but the dates were already seen
during earlier work. They cannot rescue or disqualify a candidate and are not
described as out-of-sample confirmation.

## Prospective campaign

No v2 model is promoted. The already frozen v1 specification remains an observation-only falsification candidate for the prospective ledger.
The campaign starts October 12, 2026, authorizes no orders, and prohibits
performance-driven tuning until at least 30 eligible decisions and one complete
growing season have been recorded. Same-day registration remains mandatory; a
late record is operational evidence only, never prospective evidence.
