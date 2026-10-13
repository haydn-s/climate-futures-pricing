"""Original USDA Crop Progress reports retain their point-in-time comparisons."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from pipeline.clean import nass_crop_progress as clean_source
from pipeline.clients import nass_crop_progress as client
from pipeline.ingest import nass_crop_progress as ingest


CATALOG = b"""
<a href="/sites/default/release-files/796095/prog4026.txt">Oct 05 2026 - txt</a>
<a href="/sites/default/release-files/796095/prog4026.pdf">Oct 05 2026 - pdf</a>
<a href="/publication/crop-progress?page=61">Last</a>
"""

REPORT = b"""Crop Progress

Released October 5, 2026, by the National Agricultural Statistics Service
(NASS), Agricultural Statistics Board, United States Department of
Agriculture (USDA).

Corn Mature - Selected States
[These 18 States planted 91% of the 2025 corn acreage]
------------------------------------------------------------------------
      State     : October 4,  :September 27,: October 4,  :  2021-2025
                :    2025     :    2026     :    2026     :   Average
------------------------------------------------------------------------
Illinois .......:    (NA)           83            92            88
Iowa ...........:    (NA)           72            85            88
18 States ......:    (NA)           72            83            83
------------------------------------------------------------------------

Corn Condition - Selected States: Week Ending October 4, 2026
----------------------------------------------------------------------------
      State     : Very poor :   Poor    :   Fair    :   Good    : Excellent
----------------------------------------------------------------------------
Illinois .......:      3           9          30          43          15
Iowa ...........:      2           7          19          57          15
18 States ......:      6          12          28          42          12
Previous week ..:      6          11          26          44          13
Previous year ..:   (NA)        (NA)        (NA)        (NA)        (NA)
----------------------------------------------------------------------------
"""

DELAY_NOTICE = b"""Washington, DC - (April 16, 2012)
Release of today's regularly scheduled 4:00 p.m. weekly Crop Progress report
is delayed. We expect to release the reports tomorrow, Tuesday April 17.
"""

NO_CORN_REPORT = b"""Crop Progress

Released November 13, 2012, by the National Agricultural Statistics Service
(NASS), Agricultural Statistics Board, United States Department of
Agriculture (USDA).

Soybeans Harvested - Selected States
------------------------------------------------------------------------
Iowa ...........:      99            99            99           97
18 States ......:      95            93            96           93
------------------------------------------------------------------------
"""


def test_catalog_keeps_only_text_releases_and_finds_the_last_page() -> None:
    catalog = client.parse_catalog(
        CATALOG, url="https://esmis.nal.usda.gov/publication/crop-progress?page=0"
    )
    assert catalog.last_page == 61
    assert catalog.releases == (client.Release(
        date(2026, 10, 5),
        "https://esmis.nal.usda.gov/sites/default/release-files/796095/prog4026.txt",
    ),)


def test_catalog_refuses_a_release_link_that_leaves_the_official_hosts() -> None:
    body = b'<a href="https://example.com/report.txt">Oct 05 2026 - txt</a>'
    with pytest.raises(client.CropProgressError, match="refusing release link"):
        client.parse_catalog(body, url="https://esmis.nal.usda.gov/publication/crop-progress")


def test_report_preserves_current_and_contemporaneous_comparisons() -> None:
    rows = client.parse_report(REPORT, expected_release=date(2026, 10, 5))
    assert {row.reference_date for row in rows} == {date(2026, 10, 4)}
    assert {row.available_at for row in rows} == {
        datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)
    }
    selected = [row for row in rows if row.region == "selected_states"]
    mature = {(row.comparison, row.value) for row in selected
              if row.measure == "progress" and row.stage == "mature"}
    assert mature == {
        ("previous_year", None), ("previous_week", 72.0),
        ("current", 83.0), ("five_year_average", 83.0),
    }
    condition = {(row.comparison, row.category): row.value for row in selected
                 if row.measure == "condition"}
    assert condition[("current", "good")] == 42.0
    assert condition[("current", "excellent")] == 12.0
    assert condition[("previous_week", "good")] == 44.0


def test_a_corn_table_stops_before_the_next_crop_table() -> None:
    body = REPORT + b"""
Soybeans Mature - Selected States
------------------------------------------------------------------------
Iowa ...........:      1            2            99           3
18 States ......:      1            2            98           3
------------------------------------------------------------------------
"""
    rows = client.parse_report(body, expected_release=date(2026, 10, 5))
    corn_current = [row.value for row in rows if row.measure == "progress"
                    and row.stage == "mature" and row.region == "Iowa"
                    and row.comparison == "current"]
    assert corn_current == [85.0]


def test_monday_holiday_release_still_maps_to_the_prior_sunday() -> None:
    body = REPORT.replace(b"October 5, 2026", b"October 13, 2026")
    rows = client.parse_report(body, expected_release=date(2026, 10, 13))
    assert {row.reference_date for row in rows} == {date(2026, 10, 11)}
    assert {row.available_at for row in rows} == {
        datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)
    }


def test_report_rejects_catalog_date_mismatch() -> None:
    with pytest.raises(client.CropProgressError, match="body says"):
        client.parse_report(REPORT, expected_release=date(2026, 10, 6))


def test_source_state_rounding_error_is_flagged_but_aggregate_error_is_rejected() -> None:
    state_error = REPORT.replace(
        b"Iowa ...........:      2           7          19          57          15",
        b"Iowa ...........:      2           7          19          57          17",
    )
    rows = client.parse_report(state_error, expected_release=date(2026, 10, 5))
    iowa = [row for row in rows if row.region == "Iowa" and row.measure == "condition"]
    assert {row.quality_status for row in iowa} == {"source_condition_sum_102"}

    aggregate_error = REPORT.replace(
        b"18 States ......:      6          12          28          42          12",
        b"18 States ......:      6          12          28          42          13",
    )
    with pytest.raises(client.CropProgressError, match="aggregate condition"):
        client.parse_report(aggregate_error, expected_release=date(2026, 10, 5))


def test_catalogued_delay_notice_is_not_mistaken_for_a_data_report() -> None:
    assert client.parse_report(
        DELAY_NOTICE, expected_release=date(2012, 4, 16)
    ) == []
    assert client.document_type(DELAY_NOTICE, []) == "release_notice"


def test_valid_late_season_report_can_have_no_corn_table() -> None:
    assert client.parse_report(
        NO_CORN_REPORT, expected_release=date(2012, 11, 13)
    ) == []
    assert client.document_type(NO_CORN_REPORT, []) == "report_without_corn"


def test_dry_run_is_network_free_and_names_the_discovery_scope(harness, no_network) -> None:
    records = ingest.fetch(harness.fetch(
        "nass_crop_progress", years=(2012, 2013), dry_run=True,
        today=date(2026, 10, 13),
    ))
    assert [record.key for record in records] == ["2012-2013"]
    assert records[0].params["discovery"] == "catalog crawl"


def test_clean_writes_long_point_in_time_observations(harness) -> None:
    harness.seed(
        "nass_crop_progress", "2026-10-05", REPORT,
        publication_date=date(2026, 10, 5),
    )
    paths = clean_source.clean(harness.clean("nass_crop_progress"))
    assert len(paths) == 1
    table = harness.processed.read(clean_source.TABLE)
    assert len(table) == 37
    assert table["release_date"].nunique() == 1
    assert table["source_sha256"].str.len().eq(64).all()
