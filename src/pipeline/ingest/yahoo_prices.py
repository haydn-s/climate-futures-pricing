"""Archive daily futures and fund closes, one series per key.

    PYTHONPATH=src python -m pipeline fetch --source yahoo_prices
    PYTHONPATH=src python -m pipeline fetch --source yahoo_prices --years 2012

One key per series (`corn/ZC=F`), each holding the WHOLE series, and --years does
not narrow it. That is deliberate, and it is the one place this source differs
from every other module here: the window is not part of the key, so a narrowed
fetch would write a truncated series to the same key, and `clean` -- which reads
the newest version per key -- would quietly rebuild the table from the shorter
one. Per-year keys would fix that the way pipeline.ingest.nasa_power does, at the
cost of 27 requests per symbol against an undocumented endpoint that already
rejects unfamiliar user agents. Fetching all 26 years in one 330 KB request is
the better trade, so the scope flag is refused out loud instead.

Unlike every other source here, an already-archived key is ALWAYS re-fetched:
there is a new close every session, continuous futures are back-adjusted at
contract rolls, and Yahoo rewrites history when it re-adjusts. That rewrite is
the thing worth catching, and it is only visible as a new version beside the old
numbers.

The seven symbols come from config/sources.yaml's params.symbols, so adding a
contract is a config change.
"""

from __future__ import annotations

from datetime import date

from ..cli import FetchRequest
from ..clients import yahoo_prices as client
from ..storage import RawRecord
from ._common import pause, planned

FIRST_YEAR = 2000  # the window analysis/initial_analysis.py uses


def fetch(request: FetchRequest) -> list[RawRecord]:
    symbols = {str(series): str(symbol)
               for series, symbol in request.spec.params["symbols"].items()}
    # Always the full series -- see the module docstring for why a narrowed window
    # would corrupt the archive for this source specifically.
    start, end = date(FIRST_YEAR, 1, 1), request.today
    ignored = [flag for flag, value in (("--years", request.years), ("--months", request.months),
                                        ("--states", request.states)) if value]
    if ignored:
        print(f"  note: {', '.join(ignored)} do(es) not narrow this source. Each key holds the "
              f"whole series ({start.isoformat()} onwards); a partial window written to the same "
              f"key would silently shorten the cleaned table.")

    records: list[RawRecord] = []
    for series, symbol in symbols.items():
        key = f"{series}/{symbol}"
        if request.dry_run:
            records.append(planned(request.raw, request.spec.name, key,
                                   client.url(request.spec, symbol=symbol, start=start, end=end)))
            continue
        answer, body, target = client.chart(request.spec, symbol=symbol, start=start, end=end)
        records.append(request.raw.save(
            request.spec.name, key, body,
            url=target,
            params={"series": series, "symbol": symbol, "currency": answer.currency,
                    "exchange": answer.exchange, "timezone": answer.timezone_name,
                    "granularity": answer.granularity, "sessions": len(answer.sessions),
                    "requested_start": start.isoformat(), "requested_end": end.isoformat(),
                    "first_session": answer.sessions[0][0].isoformat(),
                    "last_session": answer.last_session.isoformat()},
            # A daily close is public when the session ends, so the last session in
            # the file is the date the file became knowable. Same-day, not
            # same-instant: pair it with a data release through pipeline.calendar
            # .as_of, and mind the USDM 08:30 ET release on a Thursday.
            publication_date=answer.last_session,
            version_stamp=answer.stamp,
        ))
        pause()
    return records
