"""Archive US Drought Monitor county statistics, one state-year per key.

    PYTHONPATH=src python -m pipeline fetch --source usdm
    PYTHONPATH=src python -m pipeline fetch --source usdm --states IA --years 2012

A state-year is the archiving unit because it is the largest window that stays
stable: a finished year's maps never move again unless the NDMC restates them,
which is exactly the event the raw archive exists to make visible. The current
year is re-fetched every run, because a new map lands every Thursday.

Note that the service serves the CURRENT value for a past map date, so a past
year is not a true as-first-published vintage. `--force` re-fetches a year that
is already archived; if the numbers have been restated since, that shows up as a
new version rather than as an overwrite.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Iterable

from ..calendar import usdm_release_date
from ..cli import FetchRequest
from ..clients import usdm as client
from ..config import Geography
from ..storage import RawRecord
from ._common import pause, planned, resolve_years

FIRST_YEAR = 2000  # the record starts 2000-01-04


def fetch(request: FetchRequest) -> list[RawRecord]:
    states = _states(request.geography, request.states)
    if not states:
        asked = ", ".join(request.states)
        raise SystemExit(
            f"usdm: no states in scope{f' matching {asked}' if asked else ''}. This source is "
            f"US-only; the crops carrying US states in config/geography.yaml are the ones it "
            f"can fetch."
        )
    years = resolve_years(request.years, first=FIRST_YEAR, last=request.today.year,
                          default_start=FIRST_YEAR)
    statistics_type = int(request.spec.params["statistics_type"])

    records: list[RawRecord] = []
    for postal in states:
        for year in years:
            key = f"{postal}/{year}"
            start, end = date(year, 1, 1), date(year, 12, 31)
            target = client.url(request.spec, aoi=postal, start=start, end=end,
                                statistics_type=statistics_type)

            if request.dry_run:
                records.append(planned(request.raw, request.spec.name, key, target))
                continue
            # A finished year is stable, so skip it once archived. The year in
            # progress gains a map every Thursday and is always re-fetched.
            if year < request.today.year and request.raw.has(request.spec.name, key) and not request.force:
                continue

            rows = client.rows(request.spec, aoi=postal, start=start, end=end,
                               statistics_type=statistics_type)
            records.append(request.raw.save(
                request.spec.name, key, _body(rows),
                url=target,
                params={
                    "aoi": postal,
                    "startdate": start.isoformat(),
                    "enddate": end.isoformat(),
                    "statisticsType": statistics_type,
                    "rows": len(rows),
                    "counties": len({row["fips"] for row in rows}),
                    "map_dates": len({row["mapDate"] for row in rows}),
                },
                publication_date=_published(rows),
                version_stamp=_stamp(rows),
            ))
            pause()
    return records


def _states(geography: Geography, requested: Iterable[str]) -> list[str]:
    """Every US state any crop declares, narrowed by --states.

    Config-driven rather than hard-coded: adding a state to a crop in
    config/geography.yaml brings it into scope with no change here.
    """
    wanted = {code.upper() for code in requested}
    codes: dict[str, None] = {}
    for crop in geography:
        for state in crop.states:
            if not wanted or state.postal in wanted:
                codes.setdefault(state.postal, None)
    return list(codes)


def _body(rows: list[dict]) -> bytes:
    """The rows as they will be archived.

    Re-serialised rather than kept verbatim, and deliberately so: the service
    returns rows county-major and newest-first, and that order is a property of a
    server-side collation rather than of the data, so two identical fetches can
    differ only in order and look like a revision. Sorting by (fips, mapDate)
    first means a changed hash means changed numbers.
    """
    ordered = sorted(rows, key=lambda row: (str(row["fips"]), str(row["mapDate"])))
    return json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _published(rows: list[dict]) -> date:
    """When the whole file became public: the newest map in it, plus two days.

    One date per file, so it has to be the LAST thing in it to become knowable.
    Per-row publication dates are added by pipeline.clean.usdm, which is what an
    as-of filter on the table actually uses. usdm_release_date raises if a map
    date is not a Tuesday, which catches a mis-derived date here rather than
    three steps downstream.
    """
    return max(usdm_release_date(row["mapDate"]) for row in rows)


def _stamp(rows: list[dict]) -> str:
    dates = sorted({str(row["mapDate"])[:10] for row in rows})
    return f"maps {dates[0]}..{dates[-1]} n={len(dates)} rows={len(rows)}"
