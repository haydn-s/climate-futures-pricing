"""Publication dates: when a dated observation actually became public.

The whole argument of this project is a timing claim, so the difference between
the date a record describes and the date it was published is not a detail -- it
is the measurement. Neither of the two weather sources hands that date over:

  * USDM serves mapDate, validStart and validEnd, and none of them is a
    publication date. The map is valid from a Tuesday and released the Thursday
    after, so the pipeline adds two days (proven offline against
    tests/fixtures/usdm: a request made on 2026-09-16 returns maps through
    2026-09-08 and omits the map that was valid on 2026-09-15 but unreleased).
  * nClimGrid publishes a finished month early in the following month, and shows
    the month in progress as prelim until then.

`as_of` is the function the analysis should filter with: it keeps only the rows
that had been published by a chosen date, which is what a backtest is allowed to
see. Nothing in this module reads the wall clock -- every horizon is an argument
-- so a test cannot drift and a backtest cannot leak the future.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd

# The Drought Monitor's week: data cut off Tuesday 08:00 ET, map released Thursday
# 08:30 ET. The time of day matters for a same-day price comparison; pass a
# datetime to as_of() when it does.
USDM_MAP_WEEKDAY = 1  # Tuesday, as date.weekday() numbers it
USDM_RELEASE_LAG_DAYS = 2
USDM_RELEASE_TIME_ET = "08:30"

# NOAA finalises a month's scaled county files early in the following month: the
# one observation on record is 2026-08, created 2026-09-06 (see
# tests/fixtures/nclimgrid/ncdd-202608-version.txt). Day 7 is that observation
# rounded conservatively upwards -- treat it as the earliest date to EXPECT the
# scaled file, not a guarantee. The month's version sidecar is the authority on
# what is really covered, and it should be fetched with the data.
NCLIMGRID_SCALED_DAY_OF_MONTH = 7
NCLIMGRID_SCALED = "scaled"
NCLIMGRID_PRELIM = "prelim"

# House convention for the column that carries a publication date through the
# processed tables, so as_of() has something to filter on.
PUBLICATION_COLUMN = "publication_date"


def _as_date(value: date | datetime | str) -> date:
    """Accept what the sources actually hand over: a date, a datetime, or an ISO string.

    USDM spells its dates "2012-07-17T00:00:00", so a str is the common case.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError as exc:
            raise ValueError(f"{value!r} is not an ISO date or datetime") from exc
    raise TypeError(f"expected a date, datetime or ISO string, found {type(value).__name__}")


def usdm_release_date(map_date: date | datetime | str) -> date:
    """The date a Drought Monitor map became public: its valid Tuesday plus two days.

    Raises ValueError if the map date is not a Tuesday, because every map date in
    the record is one. A non-Tuesday means the caller derived the date wrongly
    (a validEnd instead of a validStart, a resampled index, an off-by-one week),
    and silently shifting publication by a few days would corrupt exactly the
    measurement this project rests on.
    """
    valid = _as_date(map_date)
    if valid.weekday() != USDM_MAP_WEEKDAY:
        raise ValueError(
            f"{valid.isoformat()} is a {valid.strftime('%A')}, but every USDM map date is a "
            f"Tuesday; pass mapDate (== validStart), not validEnd or a resampled date"
        )
    return valid + timedelta(days=USDM_RELEASE_LAG_DAYS)


def nclimgrid_available_after(year: int, month: int) -> date:
    """The earliest date to expect the scaled county files for a month.

    ASSUMPTION: NOAA finalises a month around the 7th of the following month.
    That is one verified observation (2026-08 was created 2026-09-06) rounded up,
    not a published schedule. Before this date the month exists only as prelim,
    which is rebuilt daily and then deleted, so a prelim observation has to be
    archived when it is read or it is lost. Check the month's version sidecar for
    the authoritative coverage window rather than trusting this date.
    """
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 1-12, found {month}")
    following_year, following_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return date(following_year, following_month, NCLIMGRID_SCALED_DAY_OF_MONTH)


def expected_nclimgrid_status(year: int, month: int, as_of_date: date | datetime | str) -> str:
    """"scaled" or "prelim": which file to expect for a month on a given date.

    The two are not interchangeable -- prelim values are not scaled to the
    monthly product -- so the status that actually answered must be recorded with
    the bytes. Try the expected one, fall back to the other, believe the server.
    """
    return NCLIMGRID_SCALED if _as_date(as_of_date) >= nclimgrid_available_after(year, month) else NCLIMGRID_PRELIM


def as_of(frame: pd.DataFrame, publication_column: str, as_of_date: date | datetime | str) -> pd.DataFrame:
    """The rows of `frame` that had been published on or before `as_of_date`.

    Two rules worth knowing:

    * A `date`, or an ISO string with no time of day, is read as the whole day,
      so a map released at 08:30 on the cutoff day is included. A `datetime`, or
      a string carrying a time, is read as an instant -- which is what the USDM
      release time is for: a Thursday close is only point-in-time valid against a
      map released earlier that morning.
    * Rows whose publication date is missing are DROPPED, not kept. An
      unpublished row was knowable on no date, and keeping it would leak exactly
      the information this function exists to withhold.

    `publication_column` may name the frame's index instead of a column, so a
    price series indexed by date works without reshaping. Comparisons happen in
    UTC; naive timestamps are read as UTC.
    """
    if publication_column in frame.columns:
        values = frame[publication_column]
    elif frame.index.name == publication_column:
        values = pd.Series(frame.index, index=frame.index)
    else:
        raise KeyError(
            f"no publication column {publication_column!r} in the frame "
            f"(columns: {', '.join(map(str, frame.columns))}; index: {frame.index.name!r})"
        )

    published = pd.to_datetime(values, errors="coerce", utc=True).dt.tz_convert(None)
    cutoff, whole_day = _cutoff(as_of_date)
    comparable = published.dt.normalize() if whole_day else published
    return frame[(published.notna() & (comparable <= cutoff)).to_numpy()]


def _cutoff(as_of_date: date | datetime | str) -> tuple[pd.Timestamp, bool]:
    """An as-of horizon as a naive-UTC timestamp, plus whether it means a whole day."""
    instant = isinstance(as_of_date, datetime)
    if isinstance(as_of_date, str):
        try:
            parsed = datetime.fromisoformat(as_of_date.strip())
        except ValueError as exc:
            raise ValueError(f"{as_of_date!r} is not an ISO date or datetime") from exc
        instant = parsed.time() != time(0, 0)
        as_of_date = parsed
    stamp = pd.Timestamp(as_of_date)
    if stamp.tz is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return stamp, not instant
