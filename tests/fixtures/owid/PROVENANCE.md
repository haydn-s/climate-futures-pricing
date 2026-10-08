# Our World in Data crop yields — fixture provenance

Real bytes from Our World in Data's grapher CSV endpoint, trimmed by row selection
only. Nothing here is hand-written or re-serialised: every retained line was
checked to occur verbatim in the full response (see *Verification* below).

Endpoint:

    https://ourworldindata.org/grapher/{slug}.csv?v=1&csvType=full&useColumnShortNames=true

Request headers: `User-Agent: climate-futures-pricing/0.1 (academic research)`,
`Accept: text/csv`. Keyless.

## Fetch date

**Trim verified 2026-09-27** against a response fetched the same day. The fixture
bytes themselves predate that check — they were captured with the rest of the
`tests/fixtures/` tree on 2026-09-16 — and the verification confirms they are
still a verbatim subset of what the endpoint serves. If a future check fails, the
upstream figures have been restated, which is itself worth knowing.

| fixture | slug | fixture bytes | full response on 2026-09-27 |
|---|---|---|---|
| `maize-yields.csv` | `maize-yields` | 1,368 (44 lines) | 368,479 (12,478 data rows, 221 entities, 1866–2025) |
| `cocoa-bean-yields.csv` | `cocoa-bean-yields` | 1,927 (67 lines) | 177,204 (5,518 data rows, 91 entities, 1961–2024) |

## There is no publication date to record, and that is a finding

This endpoint offers no per-row release date, and FAO revises history years after
the fact. It **does** send an HTTP `Last-Modified`, and it is worthless: on
2026-09-27 it read 14 seconds before the response `Date`, alongside `age: 14` and
`cf-cache-status: HIT`. It records when the CDN edge cached the body, not when
anyone published anything, and it advances on every cache miss.

So `pipeline.ingest.owid_yields` records `publication_date` as null and keeps the
header in the manifest's params as evidence. `pipeline.calendar.as_of`
consequently drops every row of this table, which is correct: this source
validates the weather index against realised harvests and was never a
point-in-time input. Do not "fix" this by promoting the fetch date.

## What each fixture covers

Both files keep the same four things, which is what makes them useful:

1. **The header row verbatim**, because the value column is the point (below).
2. **The crop's own entities in full** from 1995: `United States` for maize (31
   rows, 1995–2025), `Cote d'Ivoire` and `Ghana` for cocoa (30 rows each,
   1995–2024). These are the rows `config/geography.yaml` names, so the entity
   match in `pipeline.clean.owid_yields` is exercised on real spellings.
3. **An FAO regional aggregate and `World`** at 2018–2020, which are the rows that
   must be *excluded* by that match. `Africa (FAO)` is the trap: it starts with
   the same letter as nothing in scope but is a plausible near-miss for a
   careless `contains` filter, and its `code` field is EMPTY, unlike a country's.
4. **Alphabetical neighbours** at 2018–2020 (`Afghanistan` and `Zimbabwe` for
   maize) so the retained slice is not all adjacent lines.

`maize-yields.csv` also preserves the 2012 US drought year (7.726 t/ha against
9.214 in 2011 and 9.923 in 2013) — the shortfall the whole project is built
around, available offline.

## Quirks these fixtures deliberately preserve

1. **THE VALUE COLUMN'S NAME IS DIFFERENT IN EVERY DATASET.** Maize has
   `maize_yield`; cocoa has
   `cocoa_beans__00000661__yield__005412__tonnes_per_hectare`. It can only be
   found BY EXCLUSION — whatever is not `entity`, `code` or `year`. Selecting it
   by name works until the next slug, which is why the two fixtures spell it so
   differently.
2. **No trailing newline.** `maize-yields.csv` ends `...11.705214` and
   `cocoa-bean-yields.csv` ends `...0.48650002`, both without a final `\n`, as
   served.
3. **An aggregate's `code` is empty**, giving a CSV field of zero length between
   two commas (`Africa (FAO),,2018,...`). A parser that requires every field to
   be non-empty drops the aggregates for the wrong reason.
4. **Values carry full float noise** — `1.9344001`, `0.48160002` — rather than the
   rounded figures the OWID site displays. Do not re-round them; a fixture that
   tidies its own numbers cannot catch a unit change.
5. **Both files are pure ASCII.** `config/sources.yaml` warns that entity spelling
   is unstable across datasets and that "Cote d'Ivoire" appears with and without
   diacritics. **That is not reproducible as of 2026-09-27**: both responses spell
   it `Cote d'Ivoire` without diacritics, and neither contains a single byte above
   127. The accent-folding in `pipeline.clean._common.fold` is therefore
   defensive, not currently load-bearing, and its accent path is exercised by a
   constructed string in `tests/test_owid_yields.py` rather than by these bytes.
   Treat the warning as a hazard that has not materialised, not as an observation.

## Verification

Row selection, and nothing else, is checkable — which is the point:

    live   = raw.latest("owid_yields", slug).path.read_bytes().split(b"\n")
    fixture = (FIXTURES / f"{slug}.csv").read_bytes().split(b"\n")
    assert fixture[0] == live[0]                       # header verbatim
    assert all(line in live for line in fixture[1:])   # every row verbatim
    kept = [live.index(line) for line in fixture[1:]]
    assert kept == sorted(kept)                        # and in the served order

Both fixtures passed all four assertions on 2026-09-27. `tests/test_owid_yields.py`
runs the first three against the fixtures on every test run; the comparison
against a *live* response is the manual step above, since the test suite is
offline by design.
