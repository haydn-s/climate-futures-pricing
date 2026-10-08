"""Archived USDM responses to one tidy county-week drought table, offline.

    PYTHONPATH=src python -m pipeline clean --source usdm   ->  usdm_county_drought

One row per county per map date, carrying the publication date the service never
sends (mapDate + 2 days) so pipeline.calendar.as_of can reconstruct what a
backtest was allowed to see. The severity columns keep the names the service
uses, and statisticFormatID is kept as a column rather than dropped, because
"d2" means "D2 or worse" under type 1 and "exactly D2" under type 2 -- a table
that has forgotten which one it holds is a table of unknown numbers.
"""

from __future__ import annotations

import pandas as pd

from ..calendar import PUBLICATION_COLUMN, usdm_release_date
from ..cli import CleanRequest
from ..clients import usdm as client
from ._common import archived

TABLE = "usdm_county_drought"
CUMULATIVE = 1
TOLERANCE = 0.011  # values are served to two decimals


def clean(request: CleanRequest) -> list:
    severity = [str(name) for name in request.spec.params["severity_columns"]]
    expected_type = int(request.spec.params["statistics_type"])

    frames: list[pd.DataFrame] = []
    for record, body in sorted(archived(request.raw, request.spec.name),
                               key=lambda pair: pair[0].fetched_at):
        rows = client.parse(body, aoi=record.key, url=str(record.path), expected_type=expected_type)
        frame = pd.DataFrame(rows)
        frame["source_key"] = record.key
        frames.append(frame)

    if not frames:
        raise SystemExit(
            f"usdm: nothing archived yet -- run `python -m pipeline fetch --source usdm` first"
        )

    table = pd.concat(frames, ignore_index=True)
    table["fips"] = table["fips"].astype(str).str.zfill(5)
    table["map_date"] = pd.to_datetime(table["mapDate"])
    for column in severity:
        table[column] = pd.to_numeric(table[column], errors="coerce")
    table["statistic_format_id"] = table["statisticFormatID"].astype(int)

    # Adjacent year keys overlap by one map: a January 1 startdate also returns
    # the late-December map, because the window filters on the valid week
    # overlapping it rather than on mapDate. Keep the most recently fetched copy
    # of a duplicated county-week -- the frames are already in fetch order.
    before = len(table)
    table = table.drop_duplicates(subset=["fips", "map_date"], keep="last")
    duplicates = before - len(table)

    table[PUBLICATION_COLUMN] = pd.to_datetime(
        [usdm_release_date(value) for value in table["map_date"]]
    )
    _check_cumulative(table, severity, expected_type)

    table = (table[["fips", "county", "state", "map_date", PUBLICATION_COLUMN,
                    *severity, "statistic_format_id"]]
             .sort_values(["fips", "map_date"])
             .reset_index(drop=True))

    print(f"  {len(table)} county-weeks, {table['fips'].nunique()} counties, "
          f"{table['map_date'].nunique()} map dates, "
          f"{table['map_date'].min().date()}..{table['map_date'].max().date()}"
          f"{f', {duplicates} overlapping rows de-duplicated' if duplicates else ''}")
    if request.dry_run:
        print(f"  would write {request.processed.path(TABLE)}")
        return []
    return [request.processed.write(TABLE, table)]


def _check_cumulative(table: pd.DataFrame, severity: list[str], expected_type: int) -> None:
    """Assert the invariant that gives the severity columns their meaning.

    Under statisticsType=1 the categories nest, so d0 >= d1 >= ... >= d4 and
    none + d0 == 100 exactly. If that fails, the table is not what the analysis
    reads d2 as, and it is better to stop here than to publish a correlation
    against a column that means something else.
    """
    if expected_type != CUMULATIVE:
        return
    ladder = [name for name in severity if name.startswith("d")]
    descending = pd.Series(True, index=table.index)
    for higher, lower in zip(ladder, ladder[1:]):
        descending &= table[higher] >= table[lower] - TOLERANCE
    complements = (table[severity[0]] + table[ladder[0]] - 100.0).abs() <= TOLERANCE

    broken = (~descending) | (~complements)
    if broken.any():
        example = table.loc[broken].iloc[0]
        raise SystemExit(
            f"usdm: {int(broken.sum())} of {len(table)} rows break the cumulative invariant "
            f"for statisticsType={CUMULATIVE} (d0 >= ... >= d4 and none + d0 == 100). "
            f"First: fips {example['fips']} on {example['map_date'].date()} -> "
            f"{ {name: float(example[name]) for name in severity} }. Either the archive mixes "
            f"statistics types or the service changed its contract."
        )
