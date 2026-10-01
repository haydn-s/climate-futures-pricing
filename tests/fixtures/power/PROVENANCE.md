# NASA POWER daily point data — fixture provenance

Real bytes from NASA POWER's daily point endpoint. **Nothing here was trimmed.**
Like the Yahoo fixtures, every file is a complete response: the endpoint takes an
arbitrary window, so one month at one point is ~1.5 KB and a small fixture is a
small *request* rather than an edited payload.

Endpoint:

    https://power.larc.nasa.gov/api/temporal/daily/point
      ?parameters={vars}&community=AG&latitude={lat}&longitude={lon}
      &start={YYYYMMDD}&end={YYYYMMDD}&format=JSON

Request headers: `User-Agent: climate-futures-pricing/0.1 (academic research)`,
`Accept: application/json`. Keyless. Reanalysis, not station observation.

## Fetch date

**Retrieved 2026-09-27, 02:10 UTC.** This matters more than usual for
`point_iowa_current_padded_tail.json`, whose whole point is what the service does
at the edge of its record — that file is only interpretable against the date it
was fetched.

**There is no `Last-Modified` and no `ETag` on this endpoint.** Both were checked
and absent on all six responses. So no publication date can be read from a header,
which is why `config/sources.yaml` carries an observed lag instead and why
`header.api.version` plus `header.sources` are stored as the only vintage markers.

## Fixtures

| File | Point | Window | Status | Bytes | Why it is here |
|---|---|---|---|---|---|
| `point_soubre_2012-07_verbatim.json` | Soubre, CI (5.8, −6.6) | 2012-07 | 200 | 1,585 | The baseline, and a **cocoa point** — the geography nClimGrid cannot serve. |
| `point_iowa_1981-01_merra2.json` | Iowa (42.0, −93.6) | 1981-01 | 200 | 1,552 | The **first month of the record**, and `sources: ["MERRA2","POWER"]`. |
| `point_iowa_current_padded_tail.json` | Iowa | 2026-09-01→30 | 200 | 1,474 | **The padded tail.** See below. `sources: ["GEOSIT","POWER"]`. |
| `error_422_before_1981.json` | Iowa | 1970-01 | **422** | 265 | POWER's own validation shape, naming the real first date. |
| `error_422_dashed_dates.json` | Iowa | `2012-07-01` | **422** | 302 | The framework's query-validation shape. |
| `error_422_bad_parameter.json` | Iowa | 2012-07 | **422** | 214 | An unpublished parameter name. |

## The two traps

### 1. The tail is padded, not truncated — and `header.end` lies about it

`point_iowa_current_padded_tail.json` was requested with `end=20260930`, two days
past the fetch date. The response says:

    "header": { "start": "20260901", "end": "20260927", ... "fill_value": -999.0 }

`header.end` is **the server's today**, not the last day with data. The series runs
to 20260927 (27 values) and the last **five** are `-999.0`, so the real coverage
ends **2026-09-22** — a five-day latency. Neither the row count nor the header
tells you that; only the last non-fill value does, which is what
`PowerDaily.last_observed` computes.

`-999.0` must become NaN before any mean or sum. This is nClimGrid's `-999.99`
trap in a different coat, and it bites hardest at the end of a series where a
current-month mean silently collapses.

### 2. `header` changes type between success and failure

On 200 it is an **object**. On POWER's own 422 it is a **prose string** with the
reasons in a sibling `messages` array. The framework's 422 has **no `header` at
all**, just `detail`. So `body["header"]["end"]` against an error indexes a string
and fails far from the cause. Check the status and the shape, never the key alone.

## Other quirks these preserve

1. **`geometry.coordinates` is `[lon, lat, elevation]`** — GeoJSON order, longitude
   FIRST. Read as `(lat, lon)`, Soubre's `[-6.6, 5.8, 195.92]` lands in the
   Atlantic. The request is echoed back rather than the grid cell that answered,
   so the response cannot tell you how far a value travelled to reach the point.
2. **THE REANALYSIS BEHIND THE SERIES CHANGES WITH THE ERA.** 1981 is
   `["MERRA2","POWER"]`; 2026 is `["GEOSIT","POWER"]`. Both fixtures exist so that
   contrast is available offline.
3. **The `sources` array arrives in an UNSTABLE ORDER.** Two otherwise identical
   requests return `["GEOSIT","MERRA2","POWER"]` and `["MERRA2","GEOSIT","POWER"]`,
   which is why `pipeline.clients.nasa_power` sorts it before building a version
   stamp — an unsorted list makes the stamp flap and two identical fetches look
   like a reprocessing.
4. **`time_standard` is `LST`** (local solar time), not UTC, so a daily value is
   not aligned to any market session.
5. **Dates are YYYYMMDD integers.** A dashed ISO date is a 422, not a parse.
6. **`api.version` moves**: these say `v2.10.0`; the response cached by
   `analysis/initial_analysis.py` in 2025 says `v2.9.7`.

## Reproducing

    curl -H 'User-Agent: climate-futures-pricing/0.1 (academic research)' \
      'https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M_MAX,PRECTOTCORR&community=AG&latitude=5.8&longitude=-6.6&start=20120701&end=20120731&format=JSON'

The two historical fixtures should reproduce byte for byte unless the reanalysis is
reprocessed — which is exactly the event worth noticing. `point_iowa_current_padded_tail.json`
will NOT reproduce: its window is relative to the fetch date. To re-capture its
behaviour, request a window ending a few days past today and check where the
`-999.0` values begin.
