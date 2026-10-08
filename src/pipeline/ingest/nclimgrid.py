"""Archive nClimGrid-Daily county averages, one variable-month per key.

    PYTHONPATH=src python -m pipeline fetch --source nclimgrid --years 2012 --months 7
    PYTHONPATH=src python -m pipeline fetch --source nclimgrid --dry-run

EVERY FILE IS THE WHOLE NATION -- 3,107 counties, about 1 MB -- because NCEI
serves no geographic subset. The default scope (the project's years, the crops'
sensitive months, all four variables) is therefore several hundred megabytes, so
the plan and its size are printed before anything is fetched and `--years` /
`--months` are the normal way to run this. Nothing is re-downloaded once it is
archived and consistent with its sidecar.

Each month's version sidecar is fetched first and under its own key. It decides
whether to ask for `scaled` or `prelim`, and its GHCN-Daily build id becomes the
version stamp on that month's data files -- which is what makes a silent
reprocessing visible later. A month already archived as complete and consistent
with its sidecar is skipped entirely; `--force` re-checks it, which is how a
restatement is found.
"""

from __future__ import annotations

from typing import Iterable

from ..calendar import NCLIMGRID_PRELIM
from ..cli import FetchRequest
from ..clients import nclimgrid as client
from ..config import Geography
from ..http import FetchError, parse_last_modified
from ..storage import RawRecord
from ._common import pause, planned, resolve_years

FIRST_YEAR = 1951  # the archive starts 1951-01
# Not from params: the archive reaches back to 1951, but this project looks at
# 2000 onwards, the window analysis/initial_analysis.py uses. --years overrides.
DEFAULT_START_YEAR = 2000
VERSION_PREFIX = "version"


def fetch(request: FetchRequest) -> list[RawRecord]:
    params = request.spec.params
    variables = [str(name) for name in params["variables"]]
    years = resolve_years(request.years, first=FIRST_YEAR, last=request.today.year,
                          default_start=DEFAULT_START_YEAR)
    months = tuple(request.months) or _months(request.geography)
    periods = [(year, month) for year in years for month in sorted(set(months))
               if (year, month) <= (request.today.year, request.today.month)]
    if request.states:
        print("  note: --states does not narrow this source; every file covers all 3,107 counties")

    _announce(periods, variables, int(params.get("expected_bytes", 0)), request.dry_run)

    records: list[RawRecord] = []
    for year, month in periods:
        if request.dry_run:
            records.append(planned(request.raw, request.spec.name, _version_key(year, month),
                                   client.version_url(request.spec, year, month)))
            for variable in variables:
                status = client.status_for(request.spec, year, month, request.today)
                records.append(planned(
                    request.raw, request.spec.name, _data_key(variable, year, month),
                    client.data_url(request.spec, variable, year, month, status),
                ))
            continue

        archived = request.raw.latest(request.spec.name, _version_key(year, month))
        if not request.force and _month_is_settled(request, archived, variables, year, month):
            continue

        try:
            info, body, headers = client.version(request.spec, year, month)
        except FetchError as exc:
            if exc.status == 404:
                # A month with no sidecar has no data files either; say which and move on.
                print(f"  {year}-{month:02d}: no version file yet, skipping the month")
                continue
            raise
        records.append(request.raw.save(
            request.spec.name, _version_key(year, month), body,
            url=client.version_url(request.spec, year, month),
            params={"state": info.state, "covers_start": info.covers_start.isoformat(),
                    "covers_end": info.covers_end.isoformat(),
                    "created_on": info.created_on.isoformat() if info.created_on else None,
                    "build": info.build, "software": info.software},
            publication_date=_published(headers),
            version_stamp=info.stamp,
        ))
        pause()

        for variable in variables:
            key = _data_key(variable, year, month)
            previous = request.raw.latest(request.spec.name, key)
            # A prelim file is rebuilt daily and then deleted outright, so an
            # unarchived observation of one is gone for good -- never skip it.
            if (not request.force and previous is not None
                    and previous.version_stamp == info.stamp
                    and info.status != NCLIMGRID_PRELIM):
                continue
            body, headers, status = client.month(
                request.spec, variable, year, month, today=request.today, info=info,
            )
            rows = sum(1 for _ in client.rows(body, variable=variable.upper(), year=year,
                                              month=month,
                                              region_type=str(params.get("region_type", "cty"))))
            _check_rows(rows, int(params.get("expected_rows", 0)), variable, year, month)
            records.append(request.raw.save(
                request.spec.name, key, body,
                url=client.data_url(request.spec, variable, year, month, status),
                params={"variable": variable, "status": status, "rows": rows,
                        "covers_end": info.covers_end.isoformat(),
                        "build": info.build},
                publication_date=_published(headers),
                version_stamp=info.stamp,
            ))
            pause()
    return records


def _months(geography: Geography) -> tuple[int, ...]:
    """The sensitive months of every crop that has US states.

    This source is US-county-only, so a crop described by points alone (cocoa)
    must not widen the scope of a fetch it cannot be read for.
    """
    months: set[int] = set()
    for crop in geography:
        if crop.states:
            months.update(crop.sensitive_months.months())
    return tuple(sorted(months)) or tuple(range(1, 13))


def _data_key(variable: str, year: int, month: int) -> str:
    return f"{variable}/{year}{month:02d}"


def _version_key(year: int, month: int) -> str:
    return f"{VERSION_PREFIX}/{year}{month:02d}"


def _month_is_settled(request: FetchRequest, archived: RawRecord | None,
                      variables: Iterable[str], year: int, month: int) -> bool:
    """Whether the month is already archived as complete and internally consistent.

    Settled means: the sidecar we hold says complete, and every requested variable
    is archived carrying that same sidecar stamp. A month that fails any part of
    that is re-fetched. Because this trusts the archived sidecar rather than
    asking NCEI for a fresh one, a reprocessing of an old month is found by
    `--force`, not by a default run.
    """
    if archived is None or not str(archived.version_stamp or "").startswith("complete"):
        return False
    for variable in variables:
        previous = request.raw.latest(request.spec.name, _data_key(variable, year, month))
        if previous is None or previous.version_stamp != archived.version_stamp:
            return False
    return True


def _published(headers: dict[str, str]):
    """Last-Modified as a date, which is when THIS COPY was published.

    Not the vintage of the data inside it: NCEI stamps 2017-vintage archive files
    with their 2022 republication date. The version stamp carries the vintage.
    """
    stamp = parse_last_modified(headers)
    return stamp.date() if stamp else None


def _check_rows(rows: int, expected: int, variable: str, year: int, month: int) -> None:
    if expected and rows != expected:
        print(f"  {variable} {year}-{month:02d}: {rows} county rows, expected {expected} "
              f"-- county coverage changed, or the product did")


def _announce(periods: list, variables: list[str], each: int, dry_run: bool) -> None:
    files = len(periods) * (len(variables) + 1)
    megabytes = len(periods) * len(variables) * each / 1_000_000
    verb = "would fetch" if dry_run else "planning"
    print(f"  {verb} {len(periods)} month(s) x {len(variables)} variable(s) + sidecars "
          f"= {files} files, about {megabytes:,.0f} MB; narrow with --years and --months")
