"""Archived nClimGrid month-files to one daily county table, offline.

    PYTHONPATH=src python -m pipeline clean --source nclimgrid  ->  nclimgrid_county_daily

One row per county per day, one column per variable, with units in the column
names because this source mixes millimetres and degrees Celsius in identically
shaped files. Three things happen here that the raw bytes do not do for you:

  * THE IDENTIFIER IS TRANSLATED. Column 1 is an NCEI state code plus a 3-digit
    county code, not a FIPS code. Iowa is NCEI 13 and FIPS 19, so the file's
    13153 becomes 19153 -- and 19153 is absent from the file while 19*** is
    Massachusetts. The translation goes through config/geography.yaml's state
    table and is cross-checked against the authoritative "IA: " prefix in the
    region name, because NCEI's own cross-reference has two state names swapped.
  * THE MONTH'S LENGTH COMES FROM THE CALENDAR, never from the column count:
    every file has exactly 31 day slots whatever the month, so September's
    31st slot and February's 30th are padding.
  * -999.99 BECOMES NaN BEFORE ANYTHING IS AGGREGATED. Left in, an unmasked July
    mean for Polk County lands near -0.3 instead of 34.18.

`status` is kept as a column: prelim values are not scaled to the monthly
product, so a series that silently mixes them is not one series.
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date

import pandas as pd

from ..calendar import PUBLICATION_COLUMN
from ..cli import CleanRequest
from ..clients import nclimgrid as client
from ..config import Geography, StateRef
from ._common import archived

TABLE = "nclimgrid_county_daily"
VERSION_PREFIX = "version/"
# Units belong in the name: these four files are byte-identical in shape.
COLUMN_NAMES = {"prcp": "prcp_mm", "tmax": "tmax_c", "tmin": "tmin_c", "tavg": "tavg_c"}


def clean(request: CleanRequest) -> list:
    states = _state_index(request.geography)
    if not states:
        raise SystemExit(
            "nclimgrid: no crop in config/geography.yaml declares US states, so there is "
            "nothing this US-county-only source can be read for"
        )
    missing = float(request.spec.params["missing_value"])

    long_rows: list[tuple] = []
    status_by_month: dict[tuple[int, int], set[str]] = {}
    published_by_month: dict[tuple[int, int], list[date]] = {}
    files = 0
    masked = 0

    for record, body in archived(request.raw, request.spec.name):
        if record.key.startswith(VERSION_PREFIX):
            continue
        variable, _, period = record.key.partition("/")
        year, month = int(period[:4]), int(period[4:6])
        days = _calendar.monthrange(year, month)[1]
        files += 1
        status_by_month.setdefault((year, month), set()).add(str(record.params.get("status", "unknown")))
        if record.publication_date:
            published_by_month.setdefault((year, month), []).append(record.publication_date)

        for identifier, region_name, slots in client.rows(
            body, variable=variable.upper(), year=year, month=month, url=str(record.path),
            region_type=str(request.spec.params.get("region_type", "cty")),
        ):
            state = _resolve(identifier, region_name, states)
            if state is None:
                continue  # a county outside every crop's scope; the raw file keeps it
            fips = state.fips + identifier[-3:]
            county = region_name.partition(":")[2].strip()
            # Only the real days of the month: slots 29-31 of a February are padding,
            # not data, and must never become a row.
            for day, raw_value in enumerate(slots[:days], start=1):
                value = float(raw_value)
                if value == missing:
                    masked += 1
                    value = float("nan")
                long_rows.append((
                    fips, state.postal, county, date(year, month, day),
                    variable.lower(), value,
                ))

    if not files:
        raise SystemExit(
            "nclimgrid: nothing archived yet -- run "
            "`python -m pipeline fetch --source nclimgrid --years 2012 --months 7` first"
        )

    long = pd.DataFrame(long_rows, columns=["fips", "state", "county", "date", "variable", "value"])
    # groupby().last().unstack() rather than pivot_table: pivot_table with
    # dropna=False builds the CARTESIAN PRODUCT of all four index levels, which
    # for 473 counties over two months is 68 million rows of nothing. This keeps
    # only the county-days that were actually read.
    wide = (long.groupby(["fips", "state", "county", "date", "variable"], sort=False)["value"]
            .last().unstack("variable"))
    # A day whose every variable is the sentinel is padding, not an observation:
    # September has no 31st, and a prelim month stops before the month does. Drop
    # those rows here, explicitly and counted, rather than letting a default do it.
    unobserved = wide.isna().all(axis=1)
    empty = int(unobserved.sum())
    table = wide[~unobserved].rename(columns=COLUMN_NAMES).reset_index()
    table.columns.name = None

    periods = pd.MultiIndex.from_arrays([table["date"].map(lambda d: d.year),
                                         table["date"].map(lambda d: d.month)])
    table["status"] = [_status(status_by_month[period]) for period in periods]
    table[PUBLICATION_COLUMN] = pd.to_datetime(
        [max(published_by_month.get(period, [])) if published_by_month.get(period) else None
         for period in periods]
    )
    table["date"] = pd.to_datetime(table["date"])
    table = table.sort_values(["fips", "date"]).reset_index(drop=True)

    values = [name for name in table.columns
              if name not in ("fips", "state", "county", "date", "status", PUBLICATION_COLUMN)]
    print(f"  {len(table)} county-days from {files} file(s), {table['fips'].nunique()} counties "
          f"in {table['state'].nunique()} states, {', '.join(values)}, "
          f"{table['date'].min().date()}..{table['date'].max().date()}, "
          f"status {'/'.join(sorted(table['status'].unique()))}")
    print(f"    {masked} day slot(s) masked from {missing}; {empty} day(s) dropped as "
          f"entirely unobserved (short months and the prelim tail)")
    for name in values:
        gaps = int(table[name].isna().sum())
        if gaps:
            print(f"    {name}: {gaps} remaining gap(s) inside observed days")
    if request.dry_run:
        print(f"  would write {request.processed.path(TABLE)}")
        return []
    return [request.processed.write(TABLE, table)]


def _state_index(geography: Geography) -> dict[str, StateRef]:
    """NCEI state code -> the state, for every state any crop declares.

    Built from config/geography.yaml rather than from a table in here, because the
    NCEI-to-FIPS mapping is exactly the kind of thing that must have one home.
    """
    index: dict[str, StateRef] = {}
    for crop in geography:
        for state in crop.states:
            index.setdefault(state.ncei, state)
    return index


def _resolve(identifier: str, region_name: str, states: dict[str, StateRef]) -> StateRef | None:
    """The state a county row belongs to, or None when it is out of scope.

    Raises if the NCEI code and the name's postal prefix disagree: that would mean
    the state table in config/geography.yaml is wrong, and a wrong NCEI code does
    not error downstream -- it files Iowa's weather under Massachusetts.
    """
    if len(identifier) != 5 or not identifier.isdigit():
        raise SystemExit(f"nclimgrid: {identifier!r} is not a 5-digit NCEI county identifier")
    state = states.get(identifier[:2])
    if state is None:
        return None
    postal = client.postal_from_name(region_name)
    if postal != state.postal:
        raise SystemExit(
            f"nclimgrid: identifier {identifier} maps to NCEI {identifier[:2]} = "
            f"{state.postal} in config/geography.yaml, but the file calls it {postal!r} "
            f"({region_name!r}). The name prefix is authoritative -- fix the ncei code for "
            f"{state.postal} in config/geography.yaml."
        )
    return state


def _status(statuses: set[str]) -> str:
    """One status for a month, taking the weakest when its files disagree.

    A month whose variables were archived at different times can hold a scaled
    tmax beside a prelim prcp; calling the row prelim is the honest summary,
    because part of it is.
    """
    if len(statuses) == 1:
        return next(iter(statuses))
    return "prelim" if "prelim" in statuses else "/".join(sorted(statuses))
