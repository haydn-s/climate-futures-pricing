"""Talking to the US Drought Monitor county statistics service.

Everything in this module exists because the service answers badly in ways that
do not look like failures. It returns HTTP 200 with `[]` for a misspelled area,
an out-of-range window and an invalid statisticsType alike; one of its error
bodies is a bare JSON string, so `for row in json.loads(body)` iterates
characters instead of raising; and with the wrong Accept header it serves CSV
with different date spellings under the same status code. So `rows()` refuses to
return anything it cannot positively identify as a list of statistic records.

    from pipeline.clients import usdm
    records = usdm.rows(spec, aoi="IA", start=date(2012, 1, 1), end=date(2012, 12, 31))
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Mapping

from ..config import SourceSpec
from ..http import fetch

# Every statistic record carries these; a body missing them is not what we asked
# for, whatever its status code said.
REQUIRED_FIELDS = ("mapDate", "fips", "county", "state", "statisticFormatID")


class UsdmResponseError(Exception):
    """The service answered, but not with county drought statistics."""


def url(spec: SourceSpec, *, aoi: str, start: date, end: date,
        statistics_type: int | None = None) -> str:
    """The query URL for one area of interest over one date window.

    ISO dates, because the service's parser is US month-first and rejects
    D/M/YYYY with a 400. `aoi` takes state abbreviations and 5-digit county FIPS,
    comma-separated and mixable -- but NOT a numeric state FIPS on this endpoint,
    which is one of the ways to earn an empty 200.
    """
    if end < start:
        # The service calls this "-end date is greater than start date." with the
        # comparison backwards, as a bare JSON string. Refuse it here, where the
        # message can name the actual dates.
        raise UsdmResponseError(
            f"window ends before it starts: {start.isoformat()}..{end.isoformat()}"
        )
    values: dict[str, Any] = {
        "aoi": aoi,
        "startdate": start.isoformat(),
        "enddate": end.isoformat(),
    }
    if statistics_type is not None:
        values["statistics_type"] = statistics_type
    return spec.format_url(**values)


def rows(spec: SourceSpec, *, aoi: str, start: date, end: date,
         statistics_type: int | None = None) -> list[dict[str, Any]]:
    """Fetch one window and return its statistic records, or raise.

    Sends Accept from the spec (application/json), without which the service
    answers in CSV.
    """
    target = url(spec, aoi=aoi, start=start, end=end, statistics_type=statistics_type)
    body = fetch(target, accept=spec.accept)
    return parse(body, aoi=aoi, url=target, expected_type=_statistics_type(spec, statistics_type))


def parse(body: bytes, *, aoi: str, url: str, expected_type: int | None = None) -> list[dict[str, Any]]:
    """Statistic records from a response body, refusing every non-answer.

    Offline so the ingest tests can use tests/fixtures/usdm directly.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        # The 500 for a missing statisticsType is a text/plain .NET stack trace.
        raise UsdmResponseError(f"{url}: response is not JSON ({exc}); first bytes: {body[:120]!r}") from exc

    if isinstance(payload, str):
        # error_400_reversed_range.json is exactly this: a bare JSON string. It is
        # iterable, so anything that trusts the type gets a character at a time.
        raise UsdmResponseError(f"{url}: service returned an error string: {payload.strip()!r}")
    if isinstance(payload, Mapping):
        errors = payload.get("errors") or payload.get("title") or payload
        raise UsdmResponseError(f"{url}: service returned a validation error: {json.dumps(errors)[:300]}")
    if not isinstance(payload, list):
        raise UsdmResponseError(f"{url}: expected a list of records, found {type(payload).__name__}")
    if not payload:
        # An empty array is never "no drought": it is equally a rejected aoi, a
        # window outside the record, or an invalid statisticsType -- all of which
        # are HTTP 200 here.
        raise UsdmResponseError(
            f"{url}: zero rows for aoi={aoi!r}. The service answers 200 with [] for a "
            f"rejected area (a numeric state FIPS is rejected on the county endpoint), "
            f"a window outside 2000-01-04..present, and an invalid statisticsType. It "
            f"does NOT mean there was no drought."
        )

    for index, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise UsdmResponseError(f"{url}: row {index} is {type(row).__name__}, not an object")
        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            raise UsdmResponseError(f"{url}: row {index} is missing {', '.join(missing)}")

    if expected_type is not None:
        served = {int(row["statisticFormatID"]) for row in payload}
        if served != {expected_type}:
            # Mixing cumulative and exclusive rows in one table would make "d2"
            # mean two different things in the same column.
            raise UsdmResponseError(
                f"{url}: asked for statisticsType={expected_type} but the rows echo "
                f"statisticFormatID {sorted(served)}"
            )
    return list(payload)


def _statistics_type(spec: SourceSpec, override: int | None) -> int | None:
    value = override if override is not None else spec.params.get("statistics_type")
    return None if value is None else int(value)
