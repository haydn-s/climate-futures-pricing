"""Publication dates, checked against the captured responses rather than against prose.

Every claim this module makes about when data became public is testable offline,
so it is tested offline:

  * The Tuesday-to-Thursday rule is asserted on the real USDM map dates, and the
    two-day lag is proved from the fixture captured on 2026-09-16, which returns
    the map valid 2026-09-08 and omits the one valid 2026-09-15 -- valid the day
    before the fetch, released the day after it.
  * The nClimGrid assumption is checked against the version sidecar for the one
    month whose finalisation date was observed (2026-08, created 2026-09-06).

If NOAA or the NDMC change their schedule, these tests are where it shows up.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from pipeline.calendar import (
    PUBLICATION_COLUMN,
    as_of,
    expected_nclimgrid_status,
    nclimgrid_available_after,
    usdm_release_date,
)

# The fixtures were captured on this date; the USDM one is only meaningful with it.
USDM_FETCH_DATE = date(2026, 9, 16)


def map_dates(path: Path) -> list[date]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return sorted({datetime.fromisoformat(row["mapDate"]).date() for row in rows})


# --------------------------------------------------------------- USDM


def test_a_tuesday_map_is_published_on_the_thursday() -> None:
    assert usdm_release_date(date(2012, 7, 17)) == date(2012, 7, 19)
    assert usdm_release_date(date(2012, 7, 17)).weekday() == 3  # Thursday


def test_the_map_date_is_accepted_in_the_shapes_the_api_and_pandas_produce() -> None:
    expected = date(2012, 7, 19)
    assert usdm_release_date("2012-07-17T00:00:00") == expected  # as USDM spells it
    assert usdm_release_date("2012-07-17") == expected
    assert usdm_release_date(datetime(2012, 7, 17, 0, 0)) == expected
    assert usdm_release_date(pd.Timestamp("2012-07-17")) == expected


def test_a_non_tuesday_map_date_is_refused_rather_than_shifted() -> None:
    # validEnd (a Monday) and a resampled week start are the two ways to get this
    # wrong, and both would silently move the publication date by days.
    with pytest.raises(ValueError, match="Monday.*every USDM map date is a"):
        usdm_release_date(date(2012, 7, 23))
    with pytest.raises(ValueError, match="Thursday"):
        usdm_release_date(date(2012, 7, 19))


def test_every_real_map_date_in_the_fixtures_is_a_tuesday(fixtures: Path) -> None:
    for name in ("county_ia_2012-07_type1_multiweek.json",
                 "county_polk_2012-12-18_2013-01-15.json",
                 "county_polk_2026-08-25_2026-09-30_latest.json"):
        for valid in map_dates(fixtures / "usdm" / name):
            assert valid.weekday() == 1, f"{name}: {valid} is not a Tuesday"
            assert usdm_release_date(valid) == valid + timedelta(days=2)


def test_the_two_day_lag_is_what_the_captured_response_actually_shows(fixtures: Path) -> None:
    # Requested 2026-09-16 with enddate 9/30/2026: a future end date is clamped to
    # what has been RELEASED, so the newest map here bounds the real lag.
    dates = map_dates(fixtures / "usdm" / "county_polk_2026-08-25_2026-09-30_latest.json")
    newest = max(dates)
    assert newest == date(2026, 9, 8)
    assert usdm_release_date(newest) <= USDM_FETCH_DATE

    # The next map was valid the day before the fetch and still absent, because
    # its Thursday had not arrived. That is the whole argument for the +2 shift.
    next_map = newest + timedelta(days=7)
    assert next_map not in dates
    assert next_map < USDM_FETCH_DATE < usdm_release_date(next_map)


# --------------------------------------------------------------- nClimGrid


def test_a_month_is_expected_early_in_the_following_month() -> None:
    assert nclimgrid_available_after(2012, 7) == date(2012, 8, 7)
    assert nclimgrid_available_after(2026, 12) == date(2027, 1, 7)  # year rollover
    with pytest.raises(ValueError, match="month must be 1-12"):
        nclimgrid_available_after(2012, 13)


def test_the_assumed_lag_brackets_the_one_finalisation_we_observed(fixtures: Path) -> None:
    # ncdd-202608-version.txt: "created on 2026-09-06". The assumption must not be
    # earlier than an observed publication, nor far behind it.
    text = (fixtures / "nclimgrid" / "ncdd-202608-version.txt").read_text(encoding="utf-8")
    created = date.fromisoformat(re.search(r"created on (\d{4}-\d{2}-\d{2})", text).group(1))
    expected = nclimgrid_available_after(2026, 8)
    assert created <= expected <= created + timedelta(days=3)


def test_the_version_file_says_complete_for_the_month_it_covers(fixtures: Path) -> None:
    # Cross-check that the fixture we calibrate against really is a finished month.
    text = (fixtures / "nclimgrid" / "ncdd-202608-version.txt").read_text(encoding="utf-8")
    assert "complete for 2026-08-01 through 2026-08-31" in text


def test_status_switches_from_prelim_to_scaled_on_the_expected_date(fixtures: Path) -> None:
    # On the capture date 2026-09 existed only as prelim and 2026-08 as scaled,
    # which is exactly what the fixture pair shows.
    assert expected_nclimgrid_status(2026, 9, date(2026, 9, 16)) == "prelim"
    assert expected_nclimgrid_status(2026, 8, date(2026, 9, 16)) == "scaled"
    assert expected_nclimgrid_status(2012, 7, date(2026, 9, 16)) == "scaled"
    assert (fixtures / "nclimgrid" / "tmax-202609-cty-prelim.csv").exists()
    assert (fixtures / "nclimgrid" / "tmax-201207-cty-scaled.csv").exists()

    # The boundary itself: the day before, only prelim should be expected.
    assert expected_nclimgrid_status(2026, 8, date(2026, 9, 6)) == "prelim"
    assert expected_nclimgrid_status(2026, 8, date(2026, 9, 7)) == "scaled"


def test_the_prelim_version_file_covers_less_than_the_csv_pretends(fixtures: Path) -> None:
    # The sidecar is the authority: 12 days of real data behind 31 columns.
    text = (fixtures / "nclimgrid" / "ncdd-202609-version.txt").read_text(encoding="utf-8")
    assert "prelim for 2026-09-01 through 2026-09-12" in text


# --------------------------------------------------------------- as_of


def weekly_frame() -> pd.DataFrame:
    """Four USDM weeks with the publication date the pipeline has to add."""
    valid = [date(2012, 7, 3), date(2012, 7, 10), date(2012, 7, 17), date(2012, 7, 24)]
    return pd.DataFrame({
        "map_date": valid,
        PUBLICATION_COLUMN: [usdm_release_date(day) for day in valid],
        "d2": [10.0, 40.0, 70.0, 90.0],
    })


def test_as_of_keeps_only_what_had_been_published() -> None:
    frame = weekly_frame()
    visible = as_of(frame, PUBLICATION_COLUMN, date(2012, 7, 19))
    assert list(visible["map_date"]) == [date(2012, 7, 3), date(2012, 7, 10), date(2012, 7, 17)]
    # The 7/24 map describes a week that has begun but is not public until 7/26.
    assert date(2012, 7, 24) not in list(visible["map_date"])


def test_as_of_includes_a_map_released_on_the_cutoff_day() -> None:
    frame = weekly_frame()
    assert len(as_of(frame, PUBLICATION_COLUMN, date(2012, 7, 18))) == 2
    assert len(as_of(frame, PUBLICATION_COLUMN, date(2012, 7, 19))) == 3


def test_as_of_treats_a_datetime_as_an_instant_for_the_0830_release() -> None:
    # A Thursday close is only point-in-time valid against a map released earlier
    # that morning, so the time of day has to be able to matter.
    frame = weekly_frame()
    frame[PUBLICATION_COLUMN] = [
        datetime.combine(day, datetime.min.time()).replace(hour=12, minute=30)
        for day in frame[PUBLICATION_COLUMN]
    ]
    before = as_of(frame, PUBLICATION_COLUMN, datetime(2012, 7, 19, 12, 0))
    after = as_of(frame, PUBLICATION_COLUMN, datetime(2012, 7, 19, 13, 0))
    assert len(before) == 2 and len(after) == 3


def test_as_of_drops_rows_with_no_publication_date() -> None:
    frame = weekly_frame()
    frame.loc[1, PUBLICATION_COLUMN] = None
    visible = as_of(frame, PUBLICATION_COLUMN, date(2012, 7, 31))
    assert len(visible) == 3
    assert date(2012, 7, 10) not in list(visible["map_date"])


def test_as_of_accepts_a_publication_index_so_a_price_series_needs_no_reshaping() -> None:
    prices = pd.DataFrame(
        {"close": [6.0, 7.0, 8.0]},
        index=pd.DatetimeIndex(["2012-07-17", "2012-07-19", "2012-07-24"], name=PUBLICATION_COLUMN),
    )
    assert list(as_of(prices, PUBLICATION_COLUMN, date(2012, 7, 19))["close"]) == [6.0, 7.0]


def test_as_of_names_the_columns_it_could_not_find() -> None:
    with pytest.raises(KeyError, match="d2"):
        as_of(weekly_frame(), "released_on", date(2012, 7, 19))


def test_as_of_on_the_real_response_hides_the_unreleased_week(fixtures: Path) -> None:
    rows = json.loads((fixtures / "usdm" / "county_ia_2012-07_type1_multiweek.json").read_text())
    frame = pd.DataFrame([
        {"fips": row["fips"],
         "map_date": datetime.fromisoformat(row["mapDate"]).date(),
         "d2": row["d2"]}
        for row in rows
    ])
    frame[PUBLICATION_COLUMN] = [usdm_release_date(day) for day in frame["map_date"]]

    # Standing on Thursday 2012-07-19, the 7/17 map is out and 7/24 is not.
    visible = as_of(frame, PUBLICATION_COLUMN, date(2012, 7, 19))
    assert max(visible["map_date"]) == date(2012, 7, 17)
    assert len(visible) == len(frame[frame["map_date"] <= date(2012, 7, 17)])
    # The fixture also proves the range filter over-reads at the start: a July
    # request returns the June 26 map, whose week merely overlaps July 1.
    assert min(frame["map_date"]) == date(2012, 6, 26)
