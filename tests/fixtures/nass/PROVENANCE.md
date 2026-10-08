# USDA NASS Quick Stats — fixture provenance

> ## ⚠ THESE BYTES ARE SYNTHETIC. NOTHING HERE WAS CAPTURED FROM USDA.
>
> Every other directory under `tests/fixtures/` holds real response bytes, and
> their provenance files say so. **This one does not.** Quick Stats needs a free
> key in `NASS_API_KEY`, no key was available when `pipeline.clients.nass_production`
> was written, and these files were constructed from USDA's published API
> documentation to give that module's code paths something to run against.
>
> Every filename ends in `_synthetic` so this cannot be forgotten at a glance.
>
> **A test passing against these proves the parser is self-consistent. It does not
> prove the parser is right.** Only a captured response does that.

Endpoint the fixtures imitate:

    https://quickstats.nass.usda.gov/api/api_GET/?key={key}&source_desc=SURVEY&sector_desc=CROPS
      &commodity_desc=CORN&statisticcat_desc=PRODUCTION&agg_level_desc=COUNTY
      &state_alpha={state}&year={year}&format=JSON

**THE RESOLVED URL IS A SECRET**: the key travels in the query string.
`pipeline.http.redact_url` replaces it before a URL reaches an error message or
`data/manifest.jsonl`, and no fixture here contains a key, real or invented.

## Replacing these with real captures

Do this as soon as a key exists. In rough order of what it settles:

1. **Fetch one small state-year** and keep the body verbatim:

       PYTHONPATH=src python -m pipeline fetch --source nass_production --states IA --years 2024

   The archived bytes land under `data/raw/nass_production/IA/2024/`. Copy that
   file here, trim it by **row selection only** (keep the counties listed below so
   the assertions still mean something), and rewrite this file describing what was
   actually served — including the fetch date and the response's own `Date` header,
   the way `tests/fixtures/usdm/PROVENANCE.md` does.
2. **Delete every `_synthetic` file it replaces**, and drop the warning above.
3. **Work through the five-item checklist** at the top of
   `src/pipeline/clients/nass_production.py`, which names each assumption these
   fixtures encode. Anything that turns out wrong is a change to that module, not
   to the fixtures.

## What each fixture encodes, and how confident it is

| File | Shape | Confidence |
|---|---|---|
| `county_ia_2024_synthetic.json` | `{"data": [ ...records... ]}` | Envelope **documented**; field set **documented**; the particular counties and figures are **invented** |
| `error_unauthorized_synthetic.json` | `{"error": ["unauthorized"]}` | Envelope **documented**; exact wording **plausible, unverified** |
| `error_exceeds_limit_synthetic.json` | `{"error": ["exceeds limit=50000..."]}` | The 50,000-record cap is **documented**; wording **unverified** |
| `empty_data_synthetic.json` | `{"data": []}` | **Assumed**: that an impossible filter combination returns zero records rather than an error |
| `wrong_envelope_synthetic.json` | `{"records": [], "count": 0}` | **Deliberately wrong.** Not a claim about USDA — it exists to prove the parser rejects an unexpected envelope with a message naming what it found, rather than raising `KeyError` |

## The traps these encode

These are the reasons the fixtures exist at all. Each is documented by USDA, and
each would silently corrupt the county production weights:

1. **`Value` IS A STRING, AND SOMETIMES NOT A NUMBER.** Real figures carry
   thousands separators (`"12,345,678"`), so a bare `float()` raises. Suppressed
   figures are a flag in parentheses: `(D)` withheld to avoid disclosing an
   individual operation, `(Z)` less than half the rounding unit, and `(S)`, `(NA)`,
   `(X)` besides. `county_ia_2024_synthetic.json` holds one `(D)` and one `(Z)`,
   both with the surrounding whitespace USDA is documented to send.
2. **A SUPPRESSED COUNTY IS NOT A ZERO.** It is a county with production that USDA
   will not print. Read as zero, its weight silently moves to its neighbours. The
   clean step keeps it as a null with the flag in a `suppression` column.
3. **`county_code` 998 IS AN AGGREGATE**, spelled `OTHER (COMBINED) COUNTIES` — the
   remainder of the state, not a place. Weighted as a county it would invent a
   large phantom producer. Note that its `county_ansi` is **empty** while its
   `county_code` is `998`, which is why the client reads the code and not the ansi.
4. **THE COUNTY FIPS IS `state_fips_code` + `county_code`**, both zero-padded — so
   Polk County is `19` + `153` = `19153`. That is the **federal** code. It is NOT
   the nClimGrid identifier for the same county, which is `13153`; see
   `tests/fixtures/nclimgrid/PROVENANCE.md`. Joining the two as if they were the
   same files Iowa's weather under Massachusetts. `19001` (Adair) and `19003`
   (Adams) are here so the leading zero in a county code is exercised.
5. **`load_time` is used as the publication date** and is the weakest assumption in
   this source. It is believed to be when NASS loaded the record.
   `publication_lag_days` is null for this source precisely because the real lag is
   report-specific — monthly in season, the Annual Summary in January after
   harvest, and a county figure can be revised afterwards — so nothing is
   computed from it. **Verify it against the NASS release calendar before anything
   point-in-time rests on it.**
