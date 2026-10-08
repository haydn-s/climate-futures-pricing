# Yahoo Finance chart responses — fixture provenance

Real bytes from Yahoo Finance's `v8/finance/chart` endpoint. **Nothing here was
trimmed.** Every file is a complete response, byte for byte, because this endpoint
takes an arbitrary window: asking for one month of daily bars returns ~2 KB, so a
small fixture is a small *request* rather than an edited payload. That is worth
preferring wherever an endpoint allows it — a fixture nobody had to cut cannot
have been cut wrongly.

Endpoint:

    https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={epoch}&period2={epoch}&interval={interval}

Undocumented, and not a contract: treat availability as best-effort.

## Fetch date

**Retrieved 2026-09-27, 01:58–02:01 UTC**, with
`User-Agent: Mozilla/5.0` and `Accept: application/json`. The agent is recorded
because it was the project's assumption at capture time, not because it is
required — see below. Re-fetching the same window with the project's own agent
returns a byte-identical body.

## The user-agent gate is on presence, not on plausibility

`config/sources.yaml` used to send `Mozilla/5.0` on the belief that the endpoint
rejects unfamiliar agents. Probed on 2026-09-27 against three windows nothing had
cached (`age: 0`, a distinct `y-rid` each time):

| User-Agent sent | Result |
|---|---|
| `climate-futures-pricing/0.1 (academic research)` | **200** |
| `Mozilla/5.0` | **200** |
| *(header omitted entirely)* | **429** `Edge: Too Many Requests` |

So the gate is on sending *a* user agent, and the honest project agent passes it.
`params.user_agent` is now null and this source sends the same agent as every
other. Note that 429 is retryable in `pipeline.http`, so an agent-less request
would burn three attempts and their backoff before failing — which cannot happen
through the pipeline, since `pipeline.http` always sends one.

## Fixtures

| File | Symbol | Window / range | Status | Bytes | Bars | Why it is here |
|---|---|---|---|---|---|---|
| `chart_ZC-F_2012-07_1d.json` | `ZC=F` | 2012-07 | 200 | 2,184 | 21 | The baseline. Corn in **USX**, CBOT. |
| `chart_CORN_2012-07_1d.json` | `CORN` | 2012-07 | 200 | 3,458 | 21 | Same window in **USD** on NYSEArca — the roll-free fund. |
| `chart_CC-F_2012-07_1d.json` | `CC=F` | 2012-07 | 200 | 2,223 | 21 | Cocoa: a **future quoted in USD**, on `ICE Futures`. |
| `chart_ZC-F_2025-07_1d_null_close.json` | `ZC=F` | 2025-07 | 200 | 2,284 | 23 | Holds a **null close** (2025-07-04). |
| `chart_ZC-F_rangemax_monthly_trap.json` | `ZC=F` | `range=max` | 200 | 14,729 | 269 | **The trap.** See below. |
| `error_404_unknown_symbol.json` | `NOTASYMBOL=F` | 2012-07 | **404** | 108 | — | The error envelope. |
| `empty_weekend_window.json` | `ZC=F` | 2012-07-07→09 | **200** | 1,091 | **0** | A weekend: 200, full `meta`, and **no `timestamp` key at all**. |

The symbol `ZC=F` contains `=`, which is illegal in a filename on some systems, so
it is written `ZC-F` in the fixture names. The symbol inside the JSON is untouched.

### `range=max` silently changes the frequency

`chart_ZC-F_rangemax_monthly_trap.json` was fetched with **`interval=1d`** and
`range=max`. The response reports:

    "dataGranularity": "1mo",   "range": "max"

and carries **269 bars for 26 years** instead of ~6,500. Nothing about it looks
like a failure — the envelope, the fields and the types are all correct, there are
simply monthly bars where daily ones were asked for, which would quietly destroy
the daily timing test this project rests on. This is why
`pipeline.clients.yahoo_prices` compares `meta.dataGranularity` against the
interval it requested and refuses a mismatch, and why `period1`/`period2` are
always sent explicitly.

## Quirks these fixtures deliberately preserve

1. **CURRENCY IS NOT CONSTANT ACROSS SYMBOLS.** `ZC=F` is `USX` — US **cents** per
   bushel — while `CORN` and `CC=F` are `USD`. Nothing in the numbers says so, and
   comparing a level across them without converting is meaningless. The three
   2012-07 fixtures exist as a set for exactly this reason.
2. **`meta.gmtoffset` is `-14400`**, and a daily bar's timestamp is the session
   open in exchange time. Reading the timestamp as UTC happens to give the right
   calendar date for CBOT and stops doing so for any venue whose session starts
   the evening before, which is why the session date is derived through the offset.
3. **A null close is inside the series, not absent from it** (2025-07-04). Left in,
   it becomes a NaN return that a careless forward-fill carries across a market
   closure. A holiday may also be omitted entirely instead — July 2012 returns 21
   bars with no null at all — so both shapes occur and neither can be assumed.
4. **An empty window is HTTP 200 with a complete `meta` block and no `timestamp`
   key**, not an error and not an empty array. Anything that reaches for
   `result[0]["timestamp"]` raises `KeyError` several frames from the cause.
5. **`indicators` carries `adjclose` alongside `quote`**, and `quote[0]` holds
   `open`, `high`, `low`, `close`, `volume` as parallel arrays that must be the
   same length as `timestamp`. The clean step checks that; a short array would
   otherwise silently misalign every subsequent day.
6. **`meta` carries live fields** — `regularMarketTime`, `regularMarketPrice`,
   `fiftyTwoWeekHigh`, `currentTradingPeriod` — that change on every request, even
   for a window closed years ago. **So two fetches of identical history are not
   byte-identical**, and the raw archive gains a version on every run for reasons
   that have nothing to do with the prices. That is expected;
   `pipeline.ingest.yahoo_prices` explains why the bytes are archived verbatim
   anyway and what to compare instead.
7. **The 404 body is the whole error**: `{"chart":{"result":null,"error":{"code":
   "Not Found","description":"No data found, symbol may be delisted"}}}`. Note
   `result` is `null`, not `[]`.

## Reproducing

    period1 = int(datetime(2012, 7, 1, tzinfo=timezone.utc).timestamp())
    period2 = int(datetime(2012, 8, 1, tzinfo=timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{quote('ZC=F', safe='')}"
           f"?period1={period1}&period2={period2}&interval=1d")

Re-fetching will NOT reproduce these bytes, because of quirk 6 above — the live
`meta` fields move. Compare the `timestamp` array and the `quote` arrays, which
are the part that is supposed to be stable, and treat a change there as a
back-adjustment worth investigating.
