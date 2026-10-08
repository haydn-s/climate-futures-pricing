"""Talking to USDA NASS Quick Stats. THE ONLY UNVERIFIED CLIENT IN THIS PACKAGE.

Every other client here was written against captured bytes. This one was not:
Quick Stats needs a free key in NASS_API_KEY, no key was available when it was
written, and so everything below follows USDA's published API documentation
rather than an observed response. Treat it as a starting point.

WHAT TO CHECK ON THE FIRST REAL RUN, in this order:

  1. The envelope. This module expects {"data": [ ... ]} and an error as
     {"error": ["..."]}. Confirm both, including what a bad key returns.
  2. `Value` is documented as a STRING with thousands separators ("1,234,567")
     and as a suppression flag in parentheses -- (D) withheld for disclosure,
     (Z) less than half the rounding unit, (S) insufficient, (NA), (X). A naive
     float() raises on all of them; `quantity` returns None instead and the
     reason is kept. Confirm which flags actually appear for county production.
  3. county_code 998 is documented as "other (combined) counties", an AGGREGATE
     that must never be weighted as a county. `is_county` excludes it. Confirm
     the code and look for any others.
  4. THE COUNTY FIPS IS state_fips_code + county_code, both zero-padded. Confirm
     the field names; some endpoints spell them state_ansi and county_ansi.
  5. `load_time` is used as the publication date. Confirm what it means -- it is
     believed to be when NASS loaded the record -- and check it against the NASS
     release calendar before anything point-in-time rests on it.

THE RESOLVED URL IS A SECRET: the key travels in the query string. It is never
logged and never stored -- pipeline.http.redact_url replaces it before a URL
reaches an error message or data/manifest.jsonl.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from ..config import SourceSpec
from ..http import FetchError, fetch, redact_url

# Documented suppression flags. Anything else in parentheses is treated the same
# way -- unknown but not a number -- rather than crashing the run.
SUPPRESSION = re.compile(r"^\s*\((?P<flag>[A-Z]+)\)\s*$")
AGGREGATE_COUNTY_CODES = frozenset({"998"})


class NassResponseError(Exception):
    """Quick Stats answered, but not with county production records."""


@dataclass(frozen=True)
class NassRecord:
    """One Quick Stats record, reduced to what the weights need."""

    fips: str
    state_alpha: str
    county_name: str
    year: int
    quantity: float | None
    unit: str
    suppression: str | None
    load_time: date | None
    is_county: bool


def url(spec: SourceSpec, *, api_key: str, state_alpha: str, year: int) -> str:
    return spec.format_url(key=api_key, state_alpha=state_alpha, year=year)


def records(spec: SourceSpec, *, api_key: str, state_alpha: str,
            year: int) -> tuple[list[NassRecord], bytes, str]:
    """Fetch one state-year. The returned URL is already redacted; the fetched one is not."""
    target = url(spec, api_key=api_key, state_alpha=state_alpha, year=year)
    safe = redact_url(target)
    try:
        body = fetch(target, accept=spec.accept)
    except FetchError as exc:
        # exc.url is redacted by FetchError itself, so this cannot leak the key.
        raise NassResponseError(
            f"{exc}. A 401 or 403 here usually means NASS_API_KEY is wrong rather than "
            f"missing; request a free key at {spec.api_key_signup_url}."
        ) from exc
    return parse(body, url=safe), body, safe


def parse(body: bytes, *, url: str = "") -> list[NassRecord]:
    """Records from a Quick Stats response. Offline, and UNVERIFIED -- see the module docstring."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise NassResponseError(f"{url or 'response'}: not JSON ({exc}); first bytes: {body[:120]!r}") from exc
    if not isinstance(payload, Mapping):
        raise NassResponseError(f"{url or 'response'}: expected an object, found {type(payload).__name__}")
    if payload.get("error"):
        problems = payload["error"]
        text = "; ".join(str(item) for item in problems) if isinstance(problems, list) else str(problems)
        raise NassResponseError(
            f"{url or 'response'}: {text}. 'unauthorized' means the key was rejected; "
            f"'exceeds limit' means the query must be narrowed (one state-year at a time)."
        )
    data = payload.get("data")
    if not isinstance(data, list):
        raise NassResponseError(
            f"{url or 'response'}: no 'data' list; keys were {sorted(payload)}. If this "
            f"envelope is wrong, fix pipeline.clients.nass_production -- it was written "
            f"from documentation, not from a captured response."
        )
    if not data:
        raise NassResponseError(
            f"{url or 'response'}: zero records. Quick Stats returns nothing rather than an "
            f"error for a filter combination that does not exist (a state with no county "
            f"survey for that year), so this is ambiguous and never means 'no production'."
        )
    return [_record(row, index, url) for index, row in enumerate(data)]


def _record(row: Any, index: int, url: str) -> NassRecord:
    if not isinstance(row, Mapping):
        raise NassResponseError(f"{url or 'response'}: record {index} is {type(row).__name__}")
    state_fips = _digits(row, ("state_fips_code", "state_ansi"), 2, index, url)
    county_code = _digits(row, ("county_code", "county_ansi"), 3, index, url)
    quantity, suppression = _quantity(row.get("Value"))
    return NassRecord(
        fips=state_fips + county_code,
        state_alpha=str(row.get("state_alpha", "")).strip(),
        county_name=str(row.get("county_name", "")).strip(),
        year=int(str(row.get("year")).strip()),
        quantity=quantity,
        unit=str(row.get("unit_desc", "")).strip(),
        suppression=suppression,
        load_time=_load_time(row.get("load_time")),
        # An "other (combined) counties" row is a remainder, not a place: weighting
        # it as a county would silently invent one.
        is_county=county_code not in AGGREGATE_COUNTY_CODES,
    )


def _quantity(value: Any) -> tuple[float | None, str | None]:
    """A Value string as a number, or None plus the suppression flag that replaced it.

    "1,234,567" is a number with separators; "(D)" is a withheld figure. Both are
    normal in county data, and neither survives float() unaided.
    """
    if value is None:
        return None, None
    text = str(value).strip()
    flag = SUPPRESSION.match(text)
    if flag:
        return None, flag["flag"]
    try:
        return float(text.replace(",", "")), None
    except ValueError:
        return None, text or None


def _load_time(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _digits(row: Mapping[str, Any], names: tuple[str, ...], width: int,
            index: int, url: str) -> str:
    """A zero-padded code from the first of several field spellings that is present."""
    for name in names:
        raw = row.get(name)
        if raw is None or str(raw).strip() == "":
            continue
        text = str(raw).strip()
        if not text.isdigit():
            raise NassResponseError(f"{url or 'response'}: record {index} has {name}={raw!r}")
        return text.zfill(width)
    raise NassResponseError(
        f"{url or 'response'}: record {index} has none of {', '.join(names)}; the county FIPS "
        f"cannot be assembled without them"
    )
