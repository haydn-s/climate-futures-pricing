# nClimGrid-Daily county averages — fixture provenance

Real bytes from NOAA NCEI, trimmed by row selection only. Nothing here is synthesised:
every line is byte-for-byte as served, so a parser exercised against these fixtures meets
the same quirks it meets in production.

## Source

    https://www.ncei.noaa.gov/data/nclimgrid-daily/access/averages/{YYYY}/{var}-{YYYYMM}-cty-{status}.csv
    https://www.ncei.noaa.gov/data/nclimgrid-daily/access/averages/{YYYY}/ncdd-{YYYYMM}-version.txt

`var` is one of `prcp`, `tmax`, `tmin`, `tavg`; `status` is `scaled` for a finished month
and `prelim` for the month in progress. Product: nClimGrid-Daily v1.0.0, NOAA NCEI.
Documentation: `https://www.ncei.noaa.gov/data/nclimgrid-daily/doc/` (readme, user guide,
NCEI-to-FIPS state cross-reference).

## Fetch date

**Retrieved 2026-09-16, 11:17–11:26 UTC.** The task brief said to record 2026-09-15; the
bytes were actually pulled on the 16th, and this file records the real date because a
provenance record that rounds its own dates is worth nothing. (2026-09-15 04:36:22 GMT is
a real and relevant date here — it is the `Last-Modified` of the `202609` prelim files,
i.e. when NOAA last published them, not when we fetched them.)

Each source file's `Last-Modified` is listed below. That header, not the fetch date, is
the publication date: record it per file or a later revision is undetectable.

| fixture | source `Last-Modified` (publication) | source bytes | fixture bytes |
|---|---|---|---|
| `tmax-201207-cty-scaled.csv` | Thu, 01 Sep 2022 14:33:46 GMT | 1093490 | 3510 |
| `prcp-201207-cty-scaled.csv` | Thu, 01 Sep 2022 19:47:54 GMT | 1093490 | 3510 |
| `prcp-201209-cty-scaled.csv` | Thu, 01 Sep 2022 19:49:33 GMT | 1093490 | 3510 |
| `tmax-201202-cty-scaled.csv` | Thu, 01 Sep 2022 14:31:54 GMT | 1093490 | 3510 |
| `tmax-201302-cty-scaled.csv` | Thu, 01 Sep 2022 14:36:37 GMT | 1093490 | 3510 |
| `tmax-202609-cty-prelim.csv` | Tue, 15 Sep 2026 04:36:22 GMT | 1093490 | 3510 |
| `ncdd-195107-version.txt` | Tue, 27 Sep 2022 21:25:37 GMT | 124 | 124 |
| `ncdd-201202-version.txt` | Tue, 27 Sep 2022 18:31:21 GMT | 124 | 124 |
| `ncdd-201207-version.txt` | Tue, 27 Sep 2022 18:31:21 GMT | 124 | 124 |
| `ncdd-201209-version.txt` | Tue, 27 Sep 2022 18:31:21 GMT | 124 | 124 |
| `ncdd-201302-version.txt` | Tue, 27 Sep 2022 18:29:01 GMT | 124 | 124 |
| `ncdd-202608-version.txt` | Sun, 06 Sep 2026 10:47:10 GMT | 224 | 224 |
| `ncdd-202609-version.txt` | Tue, 15 Sep 2026 04:36:22 GMT | 222 | 222 |

## How they were trimmed

The national file is 1,093,490 bytes: 3,107 county rows, one per line. Trimming kept ten
rows and dropped the other 3,097. No other edit — no reformatting, no re-rounding, no
added header, and the source convention of **no trailing newline** is preserved.

The ten counties are corn-belt, spanning the five states `analysis/initial_analysis.py`
already tracks. They appear in source order, which sorts on the NCEI state code, so
Illinois (11) and Indiana (12) precede Iowa (13):

    11019  IL: Champaign County      21091  MN: Martin County
    11113  IL: McLean County         21129  MN: Renville County
    12007  IN: Benton County         25041  NE: Custer County
    12181  IN: White County          25185  NE: York County
    13109  IA: Kossuth County
    13153  IA: Polk County    <- Des Moines; the unit-verification anchor

Version files are copied verbatim; they are 124–224 bytes and have nothing to trim.

## What each fixture covers

| fixture | why it is here |
|---|---|
| `tmax-201207-cty-scaled.csv` | 31-day month; all 31 day columns real, no sentinel. Units anchor: Polk County, IA peaks 39.78 °C on 24 July 2012. |
| `prcp-201207-cty-scaled.csv` | 31-day month, precipitation. Polk County totals 37.28 mm against a ~115 mm July normal — the 2012 drought deficit. |
| `prcp-201209-cty-scaled.csv` | **30-day month**: day-31 column is `-999.99` in every row. |
| `tmax-201202-cty-scaled.csv` | **Leap February**: days 1–29 real, days 30–31 `-999.99`. |
| `tmax-201302-cty-scaled.csv` | **Common-year February**: days 1–28 real, days 29–31 `-999.99`. |
| `tmax-202609-cty-prelim.csv` | **Incomplete current month, `prelim` status**: days 1–12 real, days 13–31 `-999.99`. The file still has all 31 day columns. |
| `ncdd-2012*/2013*-version.txt` | Old-format version files: coverage window plus the GHCNd download date (2017), no `created on` line. |
| `ncdd-195107-version.txt` | Same old format at the start of the archive (GHCNd downloaded 2017-04-30). |
| `ncdd-202608-version.txt` | Current-format version file for a finished month: `complete`, `created on`, software version, GHCN-Daily build id. |
| `ncdd-202609-version.txt` | Current-format file for a month in progress: says `prelim` and names the real coverage window `2026-09-01 through 2026-09-12`. |

## Quirks these fixtures deliberately preserve

1. **37 columns always.** Six metadata fields plus exactly 31 day columns, in every month.
   Short months are padded with `-999.99`; the column count never tells you the month length.
2. **The identifier is not a FIPS code.** It is a 2-digit *NCEI* state code followed by the
   3-digit FIPS county code. Iowa is NCEI `13`, not FIPS `19`, so Polk County is `13153`;
   `19153` does not exist in the file and `19***` is Massachusetts.
3. **`-999.99` is the only sentinel**, written as the 9-character field `  -999.99`.
4. **No trailing newline**, no CRLF, ASCII only.
5. **Value fields are 9 characters wide**, right-aligned, two decimals — the readme says
   `%8.2f`, but the served fields are 9 wide.
6. **Rows are not fixed width overall** (344–364 bytes): the region-name field varies.

## Reproducing

Row selection against the national file, keeping the ten ids above in file order:

    ids = {"11019","11113","12007","12181","13109","13153","21091","21129","25041","25185"}
    kept = [l for l in national.split(b"\n") if l and l.split(b",")[1].decode() in ids]
    fixture = b"\n".join(kept)   # no trailing newline

Do not re-fetch to refresh these fixtures without also updating the `Last-Modified` column:
the whole point of the table is that a changed publication date is visible.
