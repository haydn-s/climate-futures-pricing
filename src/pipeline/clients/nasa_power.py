"""Talking to NASA POWER's daily point endpoint.

Two things here are not obvious from a successful response:

  * THE TAIL IS PADDED, NOT TRUNCATED. Ask past the available record and the
    missing days come back as fill_value rows, while header.end is clamped to the
    server's today rather than to the last real day. So neither the row count nor
    the header tells you how much data you got -- `last_observed` does, by
    looking for the last value that is not the fill.
  * `header` CHANGES TYPE BETWEEN SUCCESS AND FAILURE. It is an object on 200 and
    a prose string on POWER's own 422; the framework's query validation returns
    {"detail": [...]} with no header at all. `parse` checks the shape before it
    reads anything.

Requests are pre-validated against the record's own bounds, because the 422 body
carries the useful message ("The data starts at 1981/01/01") and
pipeline.http.fetch does not keep error bodies.

    from pipeline.clients import nasa_power
    answer = nasa_power.daily(spec, lat=5.8, lon=-6.6, start=date(2012,1,1), end=date(2012,12,31))
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from ..config import SourceSpec
from ..http import FetchError, fetch


class PowerResponseError(Exception):
    """POWER answered, but not with a usable daily series."""


@dataclass(frozen=True)
class PowerDaily:
    """One parsed daily point response.

    `series` maps a POWER parameter name to {date: value}, with the fill value
    already turned into None so nothing downstream can average it by accident.
    `last_observed` is the last date any variable has a real value for, which is
    the only honest statement of coverage this endpoint makes.
    """

    latitude: float
    longitude: float
    elevation: float | None
    api_version: str
    sources: tuple[str, ...]
    fill_value: float
    time_standard: str
    header_start: str
    header_end: str
    units: Mapping[str, str]
    series: Mapping[str, Mapping[date, float | None]]
    last_observed: date | None

    @property
    def stamp(self) -> str:
        """A vintage token: the API build, the reanalysis behind it, and real coverage.

        Both move -- v2.9.7 to v2.10.0 between 2025 and 2026, MERRA2 for 1981
        against GEOSIT for 2026 -- and neither is in any header this endpoint
        sends, so it has to be lifted out of the body and stored.
        """
        observed = self.last_observed.isoformat() if self.last_observed else "none"
        return f"{self.api_version} {'+'.join(self.sources)} observed<={observed}"


def url(spec: SourceSpec, *, lat: float, lon: float, start: date, end: date,
        parameters: str | None = None) -> str:
    """The query URL for one point over one window, with the endpoint's own rules enforced."""
    if end < start:
        raise PowerResponseError(f"window ends before it starts: {start.isoformat()}..{end.isoformat()}")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise PowerResponseError(f"({lat}, {lon}) is not a latitude/longitude pair")
    first = _first_date(spec)
    if start < first:
        raise PowerResponseError(
            f"{start.isoformat()} is before the POWER record, which starts {first.isoformat()}"
        )
    fmt = str(spec.params.get("date_format", "%Y%m%d"))
    return spec.format_url(
        latitude=lat, longitude=lon,
        start=start.strftime(fmt), end=end.strftime(fmt),
        **({"parameters": parameters} if parameters else {}),
    )


def daily(spec: SourceSpec, *, lat: float, lon: float, start: date, end: date,
          parameters: str | None = None) -> tuple[PowerDaily, bytes, str]:
    """Fetch one point-window. Returns the parsed answer, the raw bytes and the URL."""
    target = url(spec, lat=lat, lon=lon, start=start, end=end, parameters=parameters)
    try:
        body = fetch(target, accept=spec.accept)
    except FetchError as exc:
        if exc.status == 422:
            # The body would have said which field it disliked, but it is not kept.
            raise PowerResponseError(
                f"{exc.url}: HTTP 422. POWER rejects a dashed ISO date (send YYYYMMDD), a "
                f"parameter name it does not publish for this community, a window starting "
                f"before {_first_date(spec).isoformat()}, and a latitude or longitude out of "
                f"range."
            ) from exc
        raise
    return parse(body, url=target), body, target


def parse(body: bytes, *, url: str = "") -> PowerDaily:
    """A daily response, refusing both of the endpoint's error shapes. Offline."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise PowerResponseError(f"{url or 'response'}: not JSON ({exc}); first bytes: {body[:120]!r}") from exc
    if not isinstance(payload, Mapping):
        raise PowerResponseError(f"{url or 'response'}: expected an object, found {type(payload).__name__}")

    # Framework query validation: {"detail": [{"loc": [...], "msg": "..."}]}
    if "detail" in payload:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', []))}: {item.get('msg')}"
            for item in payload.get("detail") or [] if isinstance(item, Mapping)
        )
        raise PowerResponseError(f"{url or 'response'}: request rejected -- {problems or payload['detail']}")
    header = payload.get("header")
    # POWER's own validation: header is prose, and the reasons are in messages.
    if isinstance(header, str):
        messages = payload.get("messages") or []
        raise PowerResponseError(
            f"{url or 'response'}: POWER refused the request -- "
            f"{'; '.join(str(message) for message in messages) or header}"
        )
    if not isinstance(header, Mapping):
        raise PowerResponseError(f"{url or 'response'}: no header object; keys were {sorted(payload)}")

    parameter = (payload.get("properties") or {}).get("parameter")
    if not isinstance(parameter, Mapping) or not parameter:
        raise PowerResponseError(f"{url or 'response'}: no properties.parameter series")

    fill = float(header.get("fill_value", -999.0))
    series: dict[str, dict[date, float | None]] = {}
    for name, values in parameter.items():
        if not isinstance(values, Mapping):
            raise PowerResponseError(f"{url or 'response'}: {name} is {type(values).__name__}, not a series")
        series[name] = {
            _as_date(stamp, url): (None if float(value) == fill else float(value))
            for stamp, value in values.items()
        }
    if any(len(days) != len(next(iter(series.values()))) for days in series.values()):
        raise PowerResponseError(
            f"{url or 'response'}: the variables cover different numbers of days "
            f"({ {name: len(days) for name, days in series.items()} })"
        )

    lon, lat, elevation = _coordinates(payload, url)
    return PowerDaily(
        latitude=lat,
        longitude=lon,
        elevation=elevation,
        api_version=str((header.get("api") or {}).get("version", "unknown")),
        # SORTED, not as served: the same point-year comes back as
        # ["GEOSIT","MERRA2","POWER"] and ["MERRA2","GEOSIT","POWER"] on different
        # requests, so an unsorted list makes the version stamp flap and two
        # identical fetches look like a reprocessing.
        sources=tuple(sorted(str(item) for item in header.get("sources") or ())),
        fill_value=fill,
        time_standard=str(header.get("time_standard", "")),
        header_start=str(header.get("start", "")),
        header_end=str(header.get("end", "")),
        units={name: str((meta or {}).get("units", ""))
               for name, meta in (payload.get("parameters") or {}).items()},
        series=series,
        last_observed=_last_observed(series),
    )


def _coordinates(payload: Mapping[str, Any], url: str) -> tuple[float, float, float | None]:
    """(lon, lat, elevation) -- GeoJSON order, LONGITUDE FIRST.

    Read as (lat, lon) this puts a Cote d'Ivoire point in the Atlantic, so the
    order is unpacked once, here, and never inline at a call site.
    """
    coordinates = (payload.get("geometry") or {}).get("coordinates")
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
        raise PowerResponseError(f"{url or 'response'}: geometry.coordinates is {coordinates!r}")
    lon, lat = float(coordinates[0]), float(coordinates[1])
    elevation = float(coordinates[2]) if len(coordinates) > 2 else None
    return lon, lat, elevation


def _last_observed(series: Mapping[str, Mapping[date, float | None]]) -> date | None:
    """The last day every variable has a real value for.

    The minimum across variables, not the maximum: a file is only knowable up to
    the point where all of it is there, and the tail is padded rather than absent
    so a maximum would read the padding as coverage.
    """
    per_variable = []
    for days in series.values():
        observed = [day for day, value in days.items() if value is not None]
        if not observed:
            return None
        per_variable.append(max(observed))
    return min(per_variable) if per_variable else None


def _first_date(spec: SourceSpec) -> date:
    return date.fromisoformat(str(spec.params.get("first_date", "1981-01-01")))


def _as_date(stamp: str, url: str) -> date:
    try:
        return datetime.strptime(str(stamp), "%Y%m%d").date()
    except ValueError as exc:
        raise PowerResponseError(f"{url or 'response'}: {stamp!r} is not a YYYYMMDD date") from exc
