# USDA Crop Progress Release Path

## Why this is the next bounded corn test

The operational GEFS specification passed development and failed its sealed 2026
holdout. It is closed rather than retuned. The next test asks a different
question: whether the market response to a scheduled, widely followed USDA
release is related to the deterioration or advancement reported in that release.

This path uses the original USDA NASS Crop Progress text reports in the
[Economics, Statistics, and Market Information System](https://esmis.nal.usda.gov/publication/crop-progress).
NASS describes Crop Progress as a weekly growing-season survey and releases the
report at 4:00 p.m. Eastern on the first business day of the week. That timestamp
is the information boundary. A report released Monday afternoon can first enter
a dated-contract label at Tuesday's open; a holiday or disruption can move the
release day, and the archive date is used rather than an assumed Monday schedule.

## Why the archived reports are preferable to a current database query

Each text report carries the values actually published that week, including the
contemporaneous previous-week, previous-year, and five-year comparisons. The
pipeline archives those source bytes and hashes them by release date. It does not
reconstruct history from today's Quick Stats database, where later revisions
could weaken the point-in-time claim.

The source pipeline performs four checks before a report becomes data:

1. discover the text link from the official ESMIS catalog rather than guessing a
   migrated file path;
2. allow only USDA ESMIS and its Cornell archive hosts;
3. match the release date printed in the report to the catalog date; and
4. validate percentages, table identities, and exact corn-table boundaries.

Official delay/cancellation notices and valid late-season reports with no corn
table are archived as distinct document types. A dash is zero and `(NA)` remains
missing. The selected-states condition aggregate must sum to 100. One October
2015 Tennessee row sums to the source-published 102%; those five state cells are
retained with `source_condition_sum_102`, while the valid national aggregate is
unaffected.

## Current archive

The October 13, 2026 pull covers 2012 through the latest report then available,
October 5, 2026:

- 515 catalog items and 515 distinct source bodies;
- 488 releases containing usable corn rows;
- 6 release notices and 21 valid reports without corn rows;
- 100,216 normalized long-form corn observations;
- 488 release-level national feature rows and 43 fixed columns; and
- exact UTC availability timestamps derived from 4:00 p.m. America/New_York,
  including daylight-saving transitions.

The feature table uses the official acreage-weighted selected-states aggregate.
It contains good-plus-excellent, poor-plus-very-poor, their balance, weekly
changes, and prior-year differences. For planted, emerged, silking, dough,
dented, mature, and harvested progress it contains the published level, weekly
change, prior-year difference, and difference from the contemporaneous five-year
average. Missing stages remain missing rather than being filled across the crop
calendar.

The archive is not perfectly rectangular. It contains 28 corn-bearing releases
for 2025 and 27 through October 5 for the still-in-progress 2026 season, versus
roughly 31–36 in most complete years. Gaps are not imputed.

## Commands

```bash
PYTHONPATH=src python -m pipeline fetch \
  --source nass_crop_progress --years 2012-2026
PYTHONPATH=src python -m pipeline clean --source nass_crop_progress
PYTHONPATH=src python -m pipeline crop-progress-features
```

Raw reports, the append-only manifest, and processed tables live under ignored
`data/`. No API key or licensed source is needed for the USDA side.

## Frozen modeling boundary

No candidate return model had been run when
[`config/nass_corn_progress_validation.yaml`](../config/nass_corn_progress_validation.yaml)
was written. The execution join produces 487 unique decisions from April 2,
2012 through September 28, 2026. Each release maps to the first later LSEG ZC
open and a five-session exit under the existing roll and cost policy. Same-day
settlement controls are usable because they precede the 4:00 p.m. report; they
are available for 482 decisions, with the other five falling back to the prior
session. No entry date is duplicated.

The protocol locks market-only, condition-plus-market, progress-plus-market, and
all-USDA-plus-market feature sets crossed with Ridge, logistic, and one shallow
boosting model. Candidate identity may use the 2017–2023 outer predictions;
2024–2025 are final development confirmation years. The model-specific 2026
holdout cannot enter selection or the gate. Because 2026 prices have appeared in
other project studies, that holdout is a sealed specification diagnostic, not
project-wide independent evidence.

The fixed comparison buys corn when the published good-minus-poor condition
balance deteriorates, sells when it improves, and holds when condition is absent
or unchanged. The gate also requires matched-model market improvement, positive
base and doubled-cost results, broad year/fold support, and both one-release
shift placebos to underperform.

This test is about scheduled USDA information. It will not reopen GEFS feature or
threshold search, and a failed result will be reported as another bounded null.
