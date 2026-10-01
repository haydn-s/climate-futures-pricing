# US Drought Monitor county statistics — fixture provenance

Real response bytes from the National Drought Mitigation Center's US Drought
Monitor (USDM) web service, captured so the county ingest can be tested offline.
Nothing here is hand-written or re-serialised: see *Trimming method* below.

Endpoint (all fixtures):

    https://usdmdataservices.unl.edu/api/CountyStatistics/GetDroughtSeverityStatisticsByAreaPercent

Query parameters: `aoi`, `startdate`, `enddate`, `statisticsType`.
Request headers used: `User-Agent: climate-futures-pricing/0.1 (academic research)`
and `Accept: application/json` — except the one CSV fixture, noted below.

## Fetch date

**Fetched 2026-09-16, 11:18–11:24 UTC**, taken from the `Date` response header of
each capture rather than from the local clock.

The task that produced these fixtures specified a fetch date of **2026-09-15**.
That date is recorded here for traceability but is *not* what the server
reported, so the verified 2026-09-16 is used throughout. This matters more than
usual for this source: the newest map an USDM request can return is a function of
when you ask (see *Publication date* below), so a wrong fetch date would make the
`..._latest.json` fixture unfalsifiable. Reconcile before citing either date.

## Publication date is not in the response

Every row carries `mapDate`, `validStart` and `validEnd`. **None of them is a
publication date.** `mapDate` equals `validStart`, is always a Tuesday, and is
the date the map *describes*; `validEnd` is the following Monday at 23:59.

USDM maps have a Tuesday 8 a.m. ET data cutoff and are released the following
**Thursday at 8:30 a.m. ET** (stated on <https://droughtmonitor.unl.edu/CurrentMap.aspx>,
which on the fetch date read "Data valid: September 8, 2026" against "Map
released: September 10, 2026"). So the publication date a point-in-time study
needs is `mapDate + 2 days`, and the pipeline has to add it — the API will not.

`county_polk_2026-08-25_2026-09-30_latest.json` is the evidence: requested on
2026-09-16 with an `enddate` of 9/30/2026, it returns maps for 2026-08-25,
2026-09-01 and 2026-09-08 and **omits the 2026-09-15 map**, which was valid the
day before the fetch but not released until Thursday 2026-09-17. The service
exposes released maps only, so a future `enddate` is safe and silently clamps.

## Fixtures

| File | `aoi` | `startdate`–`enddate` | `statisticsType` | Status | Bytes | Verbatim? |
|---|---|---|---|---|---|---|
| `county_ia_2012-07_type1_multiweek.json` | `IA` | `7/1/2012`–`7/31/2012` | 1 | 200 | 36,559 | trimmed from 145,074 |
| `county_ia_2012-07-17_type1.json` | `IA` | `7/17/2012`–`7/17/2012` | 1 | 200 | 24,247 | yes |
| `county_ia_2012-07-17_type2.json` | `IA` | `7/17/2012`–`7/17/2012` | 2 | 200 | 23,929 | yes |
| `county_polk_2012-12-18_2013-01-15.json` | `19153` | `12/18/2012`–`1/15/2013` | 1 | 200 | 1,213 | yes |
| `county_polk_2026-08-25_2026-09-30_latest.json` | `19153` | `8/25/2026`–`9/30/2026` | 1 | 200 | 718 | yes |
| `county_polk_2012-07-17_default_accept.csv` | `19153` | `7/17/2012`–`7/17/2012` | 1 | 200 | 174 | yes |
| `empty_bad_aoi.json` | `ZZ` | `7/17/2012`–`7/17/2012` | 1 | **200** | 2 | yes |
| `error_400_bad_date.json` | `19153` | `banana`–`banana` | 1 | 400 | 299 | yes |
| `error_400_reversed_range.json` | `19153` | `7/24/2012`–`7/17/2012` | 1 | 400 | 43 | yes |
| `error_500_missing_statisticstype.txt` | `19153` | `7/17/2012`–`7/17/2012` | *omitted* | 500 | 2,486 | yes |

Notes on the non-obvious ones:

- **`..._multiweek.json`** spans six map dates, not five. `startdate=7/1/2012`
  pulls in the 2012-06-26 map because the range filters on the *valid week*
  overlapping the range, not on `mapDate`. Keep this fixture as the regression
  test for that.
- **`..._default_accept.csv`** was fetched with `Accept: */*`. The service
  defaults to **CSV, not JSON**, and the CSV spells dates differently
  (`MapDate` as `20120717`, `ValidStart`/`ValidEnd` as bare `2012-07-17` with no
  time). Always send `Accept: application/json`.
- **`empty_bad_aoi.json`** is a nonsense `aoi` returning HTTP **200** with `[]`.
  A bad, misspelled or numeric-state-FIPS `aoi` never errors on this endpoint, so
  an empty array cannot be read as "no drought" or "no maps" — it may equally
  mean "your `aoi` was wrong". Treat zero rows as suspect.
- **`error_400_reversed_range.json`** parses as a JSON **string**
  (`"-end date is greater than start date.\r\n"`), not an object or array. Any
  `json.loads` that assumes a list will get a `str` and fail somewhere further
  down. The message also has its comparison backwards.
- **`error_500_missing_statisticstype.txt`** is `text/plain`: a raw .NET stack
  trace, including internal UNC server paths. Omitting `statisticsType` is a 500,
  not a 400, so the parameter is effectively required.

## Trimming method

Only `county_ia_2012-07_type1_multiweek.json` was trimmed; everything else was
already under the 50 KB budget and was copied byte-for-byte.

The trim never re-serialises JSON, so the service's own formatting (compact, no
whitespace, fixed two-decimal numbers, original key order) survives intact:

1. Scan the original body's brace depth to cut the top-level array into the
   substrings of its 594 element objects.
2. Sort the 99 distinct county FIPS and keep **every 4th** (`fips[::4]`), giving
   25 counties, and keep all 6 map dates for each — 150 rows.
3. Rejoin the retained substrings with `,` inside the original `[` and `]`.
4. Assert each retained substring still occurs verbatim in the original body.

A regular stride was chosen over "the first 25 counties" so the fixture keeps the
full severity spread of the July 2012 Iowa drought: retained rows still reach
`d3 = 100.00` and `none = 100.00`, and Polk County (`19153`) is included, which
ties this fixture to the single-county ones above.

## Properties these fixtures are expected to satisfy

Verified across all 4,998 rows captured during the probe, and preserved in the
fixtures:

- `statisticsType=1` is **cumulative**: `d0 >= d1 >= d2 >= d3 >= d4` holds for
  every row, `none + d0 == 100.00` exactly, and `d2` therefore means "D2 or
  worse". The parts do *not* sum to 100 (they sum to 200 or 300 where categories
  nest).
- `statisticsType=2` is **category-exclusive**: `none + d0 + ... + d4 == 100.00`
  exactly, and monotonicity fails in every row.
- The two agree exactly: `type1.dK == sum(type2.dJ for J >= K)` for all 99
  counties on 2012-07-17, with no rounding drift.
- Each row echoes its setting as `statisticFormatID`, a JSON **number** (`1` /
  `2`, unquoted — the CSV spells it the same way), so a stored row can be checked
  without reference to the request that produced it.
- All values are in `[0, 100]` and formatted to exactly two decimals.

Row order is **county-major, not date-major**: rows are grouped by county and
run newest map date first *within* each county. For a single state the group
order happens to be ascending FIPS, which is what
`county_ia_2012-07_type1_multiweek.json` shows — but that is a coincidence of
Iowa's county names, and the real key is the county *name* under a server-side
collation that does not match an ASCII sort ("De Witt" before "DeKalb",
"Baltimore City" before "Baltimore County", Virginia's independent cities
interleaved with its counties). Multi-state responses are blocked by state
abbreviation alphabetically. **Do not rely on the returned order** — sort by
`(fips, mapDate)` after parsing.
