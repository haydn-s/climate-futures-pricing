"""Talking to Yahoo Finance's chart endpoint. Undocumented, so treated as fragile.

Three things this module will not let a caller get wrong:

  * period1 AND period2 must both be sent as epoch seconds. `range=max` silently
    returns MONTHLY bars, which would quietly destroy a daily timing test -- the
    response looks fine, there are just 300 rows where there should be 6,500. The
    granularity the response reports back is checked against what was asked for.
  * Symbols contain "=" (ZC=F) and sit in the URL PATH, so they are percent-quoted
    before the template is filled.
  * A session date is derived from meta.gmtoffset, not from a UTC normalisation. A
    daily bar's timestamp is the session open in exchange time; reading it as UTC
    happens to work for CBOT today and stops working for any venue whose session
    starts the evening before.

This endpoint also rejects the project's honest user agent, so params.user_agent
overrides it for this source only.

    from pipeline.clients import yahoo_prices
    answer, body, url = yahoo_prices.chart(spec, symbol="ZC=F", start=..., end=...)
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from ..config import SourceSpec
from ..http import fetch

QUOTE_FIELDS = ("open", "high", "low", "close", "volume")


class YahooResponseError(Exception):
    """The chart endpoint answered, but not with the daily series asked for."""


@dataclass(frozen=True)
class Chart:
    """One parsed chart response.

    `sessions` is (session date, {field: value}) per bar, in the exchange's own
    calendar. `currency` matters more than it looks: CBOT grain futures are quoted
    in USX -- US CENTS -- while the Teucrium CORN fund is in USD, so a table that
    has forgotten the unit cannot compare them.
    """

    symbol: str
    currency: str
    exchange: str
    timezone_name: str
    gmt_offset_seconds: int
    granularity: str
    sessions: Sequence[tuple[date, dict[str, float | None]]]

    @property
    def stamp(self) -> str:
        first, last = self.sessions[0][0], self.sessions[-1][0]
        return (f"{self.symbol} {self.granularity} {first.isoformat()}..{last.isoformat()} "
                f"n={len(self.sessions)}")

    @property
    def last_session(self) -> date:
        return self.sessions[-1][0]


def url(spec: SourceSpec, *, symbol: str, start: date, end: date, interval: str | None = None) -> str:
    """The chart URL for one symbol over one window, with the symbol quoted."""
    if end < start:
        raise YahooResponseError(f"window ends before it starts: {start.isoformat()}..{end.isoformat()}")
    return spec.format_url(
        symbol=urllib.parse.quote(symbol, safe=""),
        period1=_epoch(start),
        # Exclusive-feeling but inclusive in practice: the last session on or
        # before this instant is returned, so `end` is pushed to its own midnight
        # plus a day to be sure today's close is not cut off.
        period2=_epoch(end + timedelta(days=1)),
        **({"interval": interval} if interval else {}),
    )


def chart(spec: SourceSpec, *, symbol: str, start: date, end: date,
          interval: str | None = None) -> tuple[Chart, bytes, str]:
    target = url(spec, symbol=symbol, start=start, end=end, interval=interval)
    agent = spec.params.get("user_agent")
    body = fetch(target, accept=spec.accept, user_agent=str(agent) if agent else None)
    expected = interval or str(spec.params.get("interval", "1d"))
    return parse(body, symbol=symbol, url=target, expected_granularity=expected), body, target


def parse(body: bytes, *, symbol: str, url: str = "",
          expected_granularity: str | None = None) -> Chart:
    """A chart response, refusing anything that is not the daily series. Offline."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise YahooResponseError(f"{url or 'response'}: not JSON ({exc}); first bytes: {body[:120]!r}") from exc
    if not isinstance(payload, Mapping):
        raise YahooResponseError(f"{url or 'response'}: expected an object, found {type(payload).__name__}")
    if "finance" in payload and "chart" not in payload:
        raise YahooResponseError(f"{url or 'response'}: {json.dumps(payload.get('finance'))[:300]}")

    container = payload.get("chart")
    if not isinstance(container, Mapping):
        raise YahooResponseError(f"{url or 'response'}: no chart object; keys were {sorted(payload)}")
    if container.get("error"):
        raise YahooResponseError(f"{url or 'response'}: {json.dumps(container['error'])[:300]}")
    results = container.get("result")
    if not isinstance(results, list) or not results:
        raise YahooResponseError(f"{url or 'response'}: chart.result is empty -- unknown symbol {symbol!r}?")

    result = results[0]
    meta = result.get("meta") or {}
    granularity = str(meta.get("dataGranularity", ""))
    if expected_granularity and granularity != expected_granularity:
        # This is the range=max trap: a plausible response at the wrong frequency.
        raise YahooResponseError(
            f"{url or 'response'}: served {granularity!r} bars, asked for "
            f"{expected_granularity!r}. Send explicit period1/period2 epoch seconds; "
            f"range=max returns monthly bars."
        )
    timestamps = result.get("timestamp")
    quote = (result.get("indicators") or {}).get("quote") or [{}]
    if not isinstance(timestamps, list) or not timestamps:
        raise YahooResponseError(f"{url or 'response'}: no timestamps for {symbol!r}")
    fields = {name: quote[0].get(name) for name in QUOTE_FIELDS}
    for name, values in fields.items():
        if values is not None and len(values) != len(timestamps):
            raise YahooResponseError(
                f"{url or 'response'}: {name} has {len(values)} values against "
                f"{len(timestamps)} timestamps"
            )

    offset = int(meta.get("gmtoffset", 0))
    sessions = [
        (_session_date(stamp, offset),
         {name: _value(values, index) for name, values in fields.items()})
        for index, stamp in enumerate(timestamps)
    ]
    return Chart(
        symbol=str(meta.get("symbol", symbol)),
        currency=str(meta.get("currency", "")),
        exchange=str(meta.get("fullExchangeName") or meta.get("exchangeName", "")),
        timezone_name=str(meta.get("exchangeTimezoneName", "")),
        gmt_offset_seconds=offset,
        granularity=granularity,
        sessions=sessions,
    )


def _session_date(stamp: Any, offset_seconds: int) -> date:
    """The exchange-local calendar date of a bar.

    A daily bar's timestamp is the session open in exchange time, so the date has
    to be read in the exchange's offset. Normalising in UTC gives the same answer
    for CBOT and a different one for a venue trading the evening before.
    """
    return datetime.fromtimestamp(int(stamp) + offset_seconds, tz=timezone.utc).date()


def _value(values: list | None, index: int) -> float | None:
    if values is None:
        return None
    raw = values[index]
    return None if raw is None else float(raw)


def _epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
