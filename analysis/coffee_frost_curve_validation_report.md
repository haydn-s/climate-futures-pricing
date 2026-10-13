# Coffee frost-curve validation

## Decision

**The frozen frost-curve rule fails the historical-extension gate. Close the coffee modeling avenue as suggestive but operationally unsupported.**

The newly added 2000–2011 history contains only 3 eligible frost episodes, below the locked minimum of five. The long-nearby/short-deferred trade is positive after costs in 1/3 events, with mean net -0.93% and median net -1.71%.

The same-year June–August placebo test gives a one-sided p-value of 0.966. The extension therefore fails on sample size, return sign, consistency, and the null test.

## Evidence boundary

- Protocol: `coffee-frost-curve-validation-v1`; SHA-256 `be493035d3f4b6b5e9bdcf2322db000ff27ebf0968769428af317ed95761bd3a`.
- The fixed direction, horizon, threshold, geography, and gate were saved before the 2000–2011 LSEG contracts were downloaded.
- The three 2012–2023 episodes selected the rule and cannot confirm it.
- The 2024+ period was already consumed and remains diagnostic only.
- This is a retrospective historical extension, not prospective evidence.

## Results by evidence class

| Sample | Events | Mean net | Median net | Positive | Positive fraction |
|---|---:|---:|---:|---:|---:|
| new_2000_2011_extension | 3 | -0.93% | -1.71% | 1 | 33.33% |
| selected_2012_2023 | 3 | 0.48% | 0.38% | 3 | 100.00% |
| consumed_2024_plus | 1 | 0.19% | 0.19% | 1 | 100.00% |
| pooled_pre_2024_descriptive | 6 | -0.23% | 0.23% | 4 | 66.67% |

Pooling selected history with the new extension is descriptive only. Across all 6 pre-2024 episodes, mean net is -0.23%, with 4/6 positive. Its season-matched p-value is 0.823; it does not repair the failed independent extension.

## Season-matched null

| Sample | Events | Observed mean net | Null mean | Null 95% interval | p-value |
|---|---:|---:|---:|---:|---:|
| new_2000_2011_extension | 3 | -0.93% | 0.10% | -1.00% to 2.36% | 0.966 |
| pooled_pre_2024_descriptive | 6 | -0.23% | 0.17% | -0.57% to 1.34% | 0.823 |

## Locked gate

- FAIL — at least five newly added events
- FAIL — positive mean net return
- FAIL — positive median net return
- FAIL — at least 70% positive events
- FAIL — season-matched null p-value at most 10%

Overall gate: **FAIL**.

## Data and execution

- LSEG panel: 57,091 rows, 135 contracts, 2000-01-03–2026-10-12.
- Signal: coldest Brazil point below 4°C in June–August, episodes ten calendar days apart, available on NASA POWER publication date.
- Trade: long nearby and short deferred at the next shared open; exit at the open 20 shared sessions later.
- Net return subtracts one tick plus $2.50 commission per contract per side for both legs using row-specific entry notionals.
- Placebos are non-frost June–August decisions from each event's own year.
- Licensed row-level outcomes remain under ignored `data/`; committed files contain aggregates only.

## Interpretation

The initial 3/3 pattern was real in the observed sample, but it did not replicate backward. Additional algorithms would now be specification search, not validation. Coffee can remain a useful case study about mechanism, sparse extremes, and curve targets, but this rule is not a deliverable trading model.
