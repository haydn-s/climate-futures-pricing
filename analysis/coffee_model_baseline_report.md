# Coffee model baseline v1

## Research boundary

- Protocol: `coffee-model-baseline-v1`; SHA-256 `7a1c2d11a1aac6dde25b8ef4a05a235537277245358223872bf932c24e799f0f`.
- The protocol was written before forward-return ranking.
- Model selection ends in 2023; 2024+ is a consumed diagnostic only.
- Twenty-session decisions do not overlap. Sixty-session results are a frost event study, not a fitted model.

## Model comparison

The best development-ranked **Coffee C outright** candidate is `coffee_outright_market_only_logistic`: -5.93% net, Sharpe -0.09, and 2/3 positive forward folds.

The best development-ranked **nearby-minus-deferred curve** candidate is `coffee_curve_weather_market_logistic`: 0.49% net, Sharpe +0.05, and 2/3 positive forward folds.

The primary weather test is the within-model change from `market_only` to `weather_market`:

| Target | Model | Δ development net | Δ development Sharpe |
|---|---|---:|---:|
| coffee_outright | ridge | 40.64% | +0.57 |
| coffee_outright | logistic | -107.16% | -1.54 |
| coffee_curve | ridge | 2.35% | +0.81 |
| coffee_curve | logistic | 14.58% | +1.38 |

| Candidate | Dev trades | Dev net | Dev Sharpe | Positive folds | Consumed 2024+ net | Consumed Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| coffee_curve_weather_market_logistic | 41 | 0.49% | +0.05 | 2/3 | 0.72% | +0.12 |
| coffee_curve_weather_market_ridge | 43 | -3.78% | -0.39 | 1/3 | -0.61% | -0.10 |
| coffee_curve_market_only_ridge | 17 | -6.13% | -1.20 | 0/3 | -2.72% | -0.41 |
| coffee_curve_market_only_logistic | 61 | -14.09% | -1.33 | 0/3 | 7.83% | +1.50 |
| coffee_outright_market_only_logistic | 44 | -5.93% | -0.09 | 2/3 | -9.57% | -0.23 |
| coffee_outright_weather_market_ridge | 62 | -53.90% | -0.71 | 1/3 | -55.45% | -1.17 |
| coffee_outright_market_only_ridge | 54 | -94.54% | -1.28 | 0/3 | -142.80% | -3.27 |
| coffee_outright_weather_market_logistic | 58 | -113.10% | -1.62 | 0/3 | -6.95% | -0.22 |

## Frost event study

Frost means the coldest of the three Brazil points fell below 4°C in June–August. Days within ten calendar days are one episode. These tiny samples are descriptive and cannot establish a trading rule.

| Sample | Horizon | Target | Events | Mean gross | Mean net | +net |
|---|---:|---|---:|---:|---:|---:|
| development | 20 | outright | 3 | -0.91% | -0.99% | 1 |
| development | 20 | curve | 3 | 0.63% | 0.48% | 3 |
| development | 60 | outright | 3 | 4.46% | 4.38% | 1 |
| development | 60 | curve | 3 | -0.34% | -0.49% | 1 |
| consumed_2024_plus | 20 | outright | 1 | -0.25% | -0.29% | 0 |
| consumed_2024_plus | 20 | curve | 1 | 0.26% | 0.19% | 1 |
| consumed_2024_plus | 60 | outright | 1 | 27.23% | 27.19% | 1 |
| consumed_2024_plus | 60 | curve | 1 | 4.51% | 4.43% | 1 |

## Data and execution

- Dated contracts: 75 across 2011-10-03–2026-10-12 (37,720 rows).
- Independent model decisions: 155; development through 2023: 128; consumed 2024+: 27.
- Entry/exit: next shared open through the open 20 shared sessions later; 25-business-day roll buffer.
- Development median estimated round-trip cost: 8.02 bps outright and 15.85 bps for both curve legs.
- Weather uses only Sul de Minas, Cerrado Mineiro, and Mogiana, joined by NASA POWER publication date.

## Interpretation guardrails

- This round ranks a bounded preliminary candidate set; it cannot promote a model.
- The frost sample is too small for significance or machine learning.
- Curve returns use equal percentage-return legs; an integer-contract capital replay requires dated ICE margin and explicit spread sizing.
- Any candidate worth retaining must next beat deterministic baselines under exact row costs, uncertainty tests, doubled costs, and a frozen gate.
- Licensed row-level outcomes remain under ignored `data/`; committed outputs contain aggregates only.
