# CPC 6-10 and 8-14 day outlook fixtures

Real captured responses from
`https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/`, fetched 2026-10-01.
Each is a zipped ESRI shapefile set, exactly as the archive serves it.

| file | what it is | the trap it exists to catch |
|---|---|---|
| `610temp_20230711.zip` | unified schema, 15 contours | The post-2012-09 schema: typed `Fcst_Date`/`Start_Date`/`End_Date`, numeric `Prob`, and a `Cat` field carrying `Above`/`Below`/**`Normal`**. The third category is the one a two-way reader drops. |
| `610tempabv_20120710.zip` | early schema, 6 contours, above-normal | The pre-2012-10 schema. **The category is in the FILENAME, not in a field.** Probability is the `label` attribute as a space-padded *string* (`"      33"`), `Fcst_Date` is `MM/DD/YYYY` text, and the valid period is one combined `"MM/DD/YYYY - MM/DD/YYYY"` string in `Valid_Per`. |
| `610tempbel_20120710.zip` | early schema, below-normal, same day | The other half of one early issuance. An early forecast is split across two files, so a point gets two rows for the same product-day and the clean step has to collapse them. Keeping both double-counts; keeping the wrong one reads a cold forecast as a hot one. |
| `814prcp_20230711.zip` | unified, 8-14 day precipitation | A different horizon and variable, to check the lead-time assertion is read from the product rather than assumed: this file's valid period must start 8 days after issuance, not 6. |

## Why these dates

`2012-07-10` is the peak of the 2012 drought, the year every result in this
project turns on, and it is inside the early-schema era — so the awkward schema
and the important year are the same file. Against the five corn points it reads
40% above-normal for Iowa and Illinois and 50% for Minnesota and Indiana, which
is the agronomic sanity check that the point-in-polygon read is not silently
matching nothing.

`2023-07-11` is in the unified era and reads `Normal` 36% across most of the
belt with Nebraska at 40% above — a deliberately unremarkable forecast, which is
what makes it useful: a point inside no contour is a real forecast of
near-climatological odds and must not be read as missing data.

## Not captured here

The directory listing (~42,600 entries, several MB) is not archived as a
fixture. `clients.cpc_outlook.index` is tested against a small hand-written
listing instead, because what matters about it is which filename shapes it
accepts and rejects — `_latest.zip` is a moving pointer and must never be
archived as a vintage — and that is clearer in six lines than in 42,600.
