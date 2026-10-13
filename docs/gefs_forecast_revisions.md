# Corn forecast-revision experiment

## Research question

Do changes in operational numerical weather forecasts contain corn-futures
information that is absent from market-only controls?

This is a test of a different **information set**, not another algorithm search
over the realized-weather panel. The existing work establishes a physical
corn-weather relationship but finds no executable edge in late observations or
coarse CPC categories. Operational GEFS revisions are closer to the change in
expectations that the futures market can trade.

## Point-in-time policy

- Use dated operational GEFS v12 cycles, never retrospective reforecasts.
- Begin no earlier than the v12 implementation at 12 UTC on 2020-09-23.
- Use one 00 UTC run per date. Compare it only with the preceding 00 UTC run.
- Treat a run as available at initialization plus seven hours. Files absent at
  that cutoff are missing; they are not filled from a later archive copy.
- Record the archive object's `Last-Modified`, but do not automatically treat it
  as the original market timestamp. A delay over 24 hours is labeled an archive
  republication.
- Enter no earlier than the first LSEG ZC session open after the cutoff. The
  target return therefore begins after every input was knowable.

The seven-hour rule is predeclared from archive probes rather than optimized
against returns. Sampled 2021, 2024, and 2026 objects were written approximately
5.5 to 6.4 hours after initialization. The first v12 cycle was re-uploaded in
April 2021, demonstrating why `Last-Modified` cannot be used blindly.

## Bounded first stage

The initial extraction deliberately omits ensemble spread and the 11-15 day
horizon. They can be added only after the mean-revision pipeline is correct.

| Choice | First-stage value |
|---|---|
| Dates | May-August, 2021-2026 |
| Cycle | 00 UTC only |
| Product | `geavg`, the precomputed ensemble mean |
| Leads | f006 through f240 in six-hour steps |
| Fields | 2 m TMAX, 2 m TMIN, surface APCP |
| Geography | Five configured Corn Belt points initially; production weights later |
| Price | Dated LSEG ZC contract selected by the existing roll policy |
| Entry | First eligible session open after initialization + 7 hours |

Daily forecast summaries will combine four consecutive six-hour fields. A lone
f024 APCP message is only the 18-24 hour accumulation, not daily precipitation.
Revision features will be aggregated into days 1-3, 4-7, and 8-10 and will
include forecast heat exposure, precipitation, and their change from yesterday's
run. Absolute forecast levels remain controls rather than the primary signal.

## Feasibility findings

`PYTHONPATH=src python -m pipeline gefs-audit --date DATE` samples NOAA's small
GRIB indexes and downloads no GRIB payload.

| Operational run | Index result | Estimated full mean+spread f006-f360 transfer | Archive timestamp |
|---|---:|---:|---|
| 2020-09-23 12 UTC | Required fields present | 90.2 MiB/run | Re-uploaded 2021-04-05 |
| 2021-07-15 00 UTC | Required fields present | 93.8 MiB/run | Same day, 06:25 UTC |
| 2024-07-15 00 UTC | Required fields present | 89.0 MiB/run | Same day, 06:19 UTC |
| 2026-10-01 00 UTC | Required fields present | 101.4 MiB/run | Same day, 06:23 UTC |

The bounded mean-only f006-f240 plan estimates 30.9 MiB and 40 coalesced range
requests per run. Across May-August 2021-2026, that is roughly 23 GiB before
local spatial reduction. The three selected fields themselves are approximately
13.2 MiB per run; the difference is nearby unneeded messages included to reduce
HTTP request count. Range coalescing is configurable and the date-range command
is resumable: selections whose manifest record and content file both exist are
skipped before any NOAA request.

## Implemented pipeline and pilot

Install the optional decoder with `python -m pip install -r
requirements-gefs.txt`. The commands are deliberately separate so the large
archive operation is inspectable before it is started:

```bash
# No GRIB download: inspect one run and estimate bytes.
PYTHONPATH=src python -m pipeline gefs-audit --date 2024-07-15 --through-hour 240

# No network: enumerate the locked seasonal backfill.
PYTHONPATH=src python -m pipeline gefs-backfill \
  --start 2021-05-01 --end 2026-08-31 --dry-run

# Start or resume the actual archive, then derive the feature table offline.
PYTHONPATH=src python -m pipeline gefs-backfill \
  --start 2021-05-01 --end 2026-08-31
PYTHONPATH=src python -m pipeline clean --source gefs_forecast
PYTHONPATH=src python -m pipeline gefs-features
PYTHONPATH=src python -m pipeline gefs-model-frame
```

A live seven-run pilot for July 14-20, 2024 archived all 280 f006-f240 mean
selections: 223.4 MiB total. ecCodes decoded 4,200 point-fields—three variables,
five Corn Belt points, 40 leads, and seven runs—with a farthest grid match of
23.8 km. All seven runs produced complete 10-day feature rows, and six produced
all 12 exact prior-day revisions. The July 15 revision included a 2.47 °C cooler
days 1-3 maximum-temperature forecast and 3.94 mm more days 1-3 precipitation.
Those values are pipeline validation, not a return result.

Raw GRIB selections and processed point/revision tables remain beneath ignored
`data/`. Every selection records its content hash, byte ranges, ETag when
available, initialization, conservative availability cutoff, and archive
timestamp classification. Cleaning admits only `plausible_operational_upload`;
missing timestamps, same-day uploads after the seven-hour cutoff, and archive
republications are excluded rather than treated as contemporaneously tradable
forecasts.

`gefs-model-frame` then admits only rows with an exact prior-day revision. It
uses ZC settlement-derived controls strictly before the forecast date, enters at
the first dated LSEG session open strictly after that date, holds five sessions,
and applies the existing dated-contract roll and cost assumptions. Weekend and
holiday runs that map to the same entry are reduced to the latest available run,
so a realized return is not duplicated in model training. The live pilot yielded
five unique entry decisions from six revision runs. The July 15 run aligned to
controls through Friday July 12 and to a ZCU24 entry on Tuesday July 16; the
Saturday July 20 run replaced Friday's run for the shared Monday July 22 entry.
No same-date settlement or open entered any row.

The evaluation choices were saved before the multi-year return panel was built
in [`config/gefs_corn_validation.yaml`](../config/gefs_corn_validation.yaml).
They lock expanding test seasons 2023-2025, an untouched 2026 holdout, four
feature families, Ridge/logistic/shallow-boosting models, exact base and doubled
costs, a fixed heat-minus-rain revision rule, one-day-shift placebos, and the
acceptance gate. The 2026 rows cannot enter model selection or the gate.

## Locked comparisons

The historical evaluation will compare, over identical walk-forward folds:

1. Market-only controls using information available before entry.
2. Absolute GEFS forecast levels plus the same controls.
3. GEFS day-over-day revisions plus the same controls.
4. A simple predeclared heat/rain revision rule.

Model classes remain deliberately small: regularized linear/logistic models and
one shallow boosting model. Every result uses dated contracts, the existing roll
policy, next-open execution, non-overlapping positions, and exact commission and
slippage. Model selection cannot use the last historical fold. Because all past
price history is now observable, even a held-out historical fold is bounded
evidence; a successful candidate still requires prospective paper validation.

## Acceptance gate

A candidate advances only if it:

- improves on the market-only benchmark out of sample;
- is positive after base costs and remains positive at double costs;
- is positive in a majority of calendar years and forward folds;
- has a year-block confidence interval that does not imply dependence on one
  season;
- beats the simple revision rule rather than merely fitting it less transparently;
- survives timestamp, one-day-shift, and forecast-cycle placebo checks.

Failure is a valid result. If forecast revisions do not pass this gate, the next
bounded test is the scheduled USDA Crop Progress release path; it is not another
round of model or threshold search on GEFS.

## Implementation sequence

1. **Complete:** range-download the bounded `geavg` messages with content hashes
   and run-level provenance.
2. **Complete:** decode GRIB, validate units and lead intervals, and extract the
   five configured points.
3. **Complete:** assemble six-hour values into daily forecasts and day-over-day
   revisions.
4. **Complete:** join to dated LSEG ZC decisions under the seven-hour cutoff.
5. **Complete:** freeze folds, features, costs, and acceptance checks before
   inspecting multi-year model returns.
