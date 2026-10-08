"""US Drought Monitor: client, ingest and clean, against the captured responses.

This service answers badly in ways that do not look like failures, so most of
what is tested here is refusal. The single most important assertion in the file is
that an empty array raises: the service returns HTTP 200 with `[]` for a rejected
area, an out-of-range window AND an invalid statisticsType, so reading zero rows
as "no drought" would silently turn a broken request into a calm, wrong number.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pipeline.calendar import PUBLICATION_COLUMN
from pipeline.clean import usdm as clean_usdm
from pipeline.clients import usdm as client
from pipeline.config import ConfigError
from pipeline.ingest import usdm as ingest_usdm


@pytest.fixture
def usdm_fixtures(fixtures: Path) -> Path:
    return fixtures / "usdm"


@pytest.fixture
def iowa_july(usdm_fixtures: Path) -> bytes:
    return (usdm_fixtures / "county_ia_2012-07-17_type1.json").read_bytes()


# ----------------------------------------------------------------------- the URL


def test_the_url_sends_iso_dates_because_the_parser_is_month_first(sources) -> None:
    url = client.url(sources["usdm"], aoi="IA", start=date(2012, 1, 1), end=date(2012, 12, 31))
    assert "startdate=2012-01-01" in url and "enddate=2012-12-31" in url
    # Defaulted from params, and required: omitting it is a 500, not a 400.
    assert "statisticsType=1" in url


def test_a_reversed_window_is_refused_before_the_request(sources) -> None:
    with pytest.raises(client.UsdmResponseError) as caught:
        client.url(sources["usdm"], aoi="IA", start=date(2012, 7, 31), end=date(2012, 7, 1))
    # Better than the service's own message, which has the comparison backwards.
    assert "2012-07-31..2012-07-01" in str(caught.value)


def test_the_county_endpoint_is_asked_for_json_because_the_default_is_csv(sources) -> None:
    assert sources["usdm"].accept == "application/json"


# --------------------------------------------------------------------- refusals


def test_a_real_response_parses_and_keeps_its_statistic_format(iowa_july) -> None:
    rows = client.parse(iowa_july, aoi="IA", url="fixture", expected_type=1)
    assert len(rows) == 99  # Iowa's counties, one map date
    assert {row["statisticFormatID"] for row in rows} == {1}


def test_an_empty_array_raises_because_it_is_ambiguous(usdm_fixtures: Path) -> None:
    body = (usdm_fixtures / "empty_bad_aoi.json").read_bytes()
    assert body == b"[]"
    with pytest.raises(client.UsdmResponseError) as caught:
        client.parse(body, aoi="ZZ", url="fixture")
    message = str(caught.value)
    assert "zero rows" in message
    assert "does NOT mean there was no drought" in message


def test_a_bare_error_string_raises_instead_of_iterating_characters(usdm_fixtures: Path) -> None:
    # json.loads gives a str here, which is iterable -- `for row in rows` would
    # walk it one character at a time and fail somewhere unrecognisable.
    body = (usdm_fixtures / "error_400_reversed_range.json").read_bytes()
    assert isinstance(json.loads(body), str)
    with pytest.raises(client.UsdmResponseError, match="error string"):
        client.parse(body, aoi="19153", url="fixture")


def test_a_validation_object_raises_with_its_field_errors(usdm_fixtures: Path) -> None:
    body = (usdm_fixtures / "error_400_bad_date.json").read_bytes()
    with pytest.raises(client.UsdmResponseError) as caught:
        client.parse(body, aoi="19153", url="fixture")
    assert "StartDate" in str(caught.value) or "EndDate" in str(caught.value)


def test_a_plain_text_stack_trace_raises_rather_than_a_json_error(usdm_fixtures: Path) -> None:
    body = (usdm_fixtures / "error_500_missing_statisticstype.txt").read_bytes()
    with pytest.raises(client.UsdmResponseError, match="not JSON"):
        client.parse(body, aoi="19153", url="fixture")


def test_the_wrong_statistics_type_is_caught_before_it_reaches_a_table(usdm_fixtures: Path) -> None:
    # d2 means "D2 or worse" under type 1 and "exactly D2" under type 2. Mixing
    # them puts two different measurements in one column.
    body = (usdm_fixtures / "county_ia_2012-07-17_type2.json").read_bytes()
    with pytest.raises(client.UsdmResponseError, match="statisticFormatID"):
        client.parse(body, aoi="IA", url="fixture", expected_type=1)


def test_the_two_statistics_types_agree_where_the_categories_nest(usdm_fixtures: Path) -> None:
    # type1.dK == sum(type2.dJ for J >= K). This is what makes type 1 cumulative,
    # and it is the property the clean step's invariant check rests on.
    one = {r["fips"]: r for r in client.parse(
        (usdm_fixtures / "county_ia_2012-07-17_type1.json").read_bytes(), aoi="IA", url="f")}
    two = {r["fips"]: r for r in client.parse(
        (usdm_fixtures / "county_ia_2012-07-17_type2.json").read_bytes(), aoi="IA", url="f")}
    ladder = ["d0", "d1", "d2", "d3", "d4"]
    for fips, cumulative in one.items():
        exclusive = two[fips]
        for index, name in enumerate(ladder):
            assert cumulative[name] == pytest.approx(sum(exclusive[n] for n in ladder[index:]))


def test_the_valid_week_overlap_returns_a_map_from_before_the_window(usdm_fixtures: Path) -> None:
    # startdate=2012-07-01 also returns the 2012-06-26 map, because the window
    # filters on the valid week overlapping it rather than on mapDate. Counting
    # weeks naively over-reads by one row at the start.
    rows = client.parse((usdm_fixtures / "county_ia_2012-07_type1_multiweek.json").read_bytes(),
                        aoi="IA", url="fixture")
    map_dates = sorted({row["mapDate"][:10] for row in rows})
    assert len(map_dates) == 6, "six map dates for a one-month window, not five"
    assert map_dates[0] == "2012-06-26"


# ----------------------------------------------------------------------- ingest


def test_the_states_in_scope_come_from_the_crops_not_from_python(geography) -> None:
    # Derived from the shipped config rather than frozen as a list: the property
    # under test is that scope FOLLOWS the crops, so hard-coding the answer here
    # makes the test fail every time a crop is added -- which is the one change
    # that ought to leave it passing.
    expected: list[str] = []
    for crop in geography:
        for state in crop.states:
            if state.postal not in expected:
                expected.append(state.postal)
    assert expected, "the shipped geography declares no US states at all"
    assert ingest_usdm._states(geography, []) == expected
    # Declaration order is preserved and duplicates across crops collapse.
    assert len(set(expected)) == len(expected)
    assert ingest_usdm._states(geography, ["ia", "NE"]) == ["IA", "NE"]


def test_a_dry_run_plans_every_state_year_and_writes_nothing(harness, no_network) -> None:
    records = ingest_usdm.fetch(harness.fetch(
        "usdm", states=("IA", "IL"), years=(2012, 2013), dry_run=True, today=date(2026, 9, 27)))
    assert [record.key for record in records] == ["IA/2012", "IA/2013", "IL/2012", "IL/2013"]
    assert all(record.bytes == 0 for record in records)
    assert not harness.raw.manifest_path.exists()


def test_an_out_of_scope_state_is_reported_rather_than_fetched(harness, no_network) -> None:
    with pytest.raises(SystemExit, match="no states in scope"):
        ingest_usdm.fetch(harness.fetch("usdm", states=("ZZ",), dry_run=True))


def test_a_finished_year_is_skipped_once_archived_but_the_current_one_is_not(
        harness, monkeypatch, iowa_july) -> None:
    rows = client.parse(iowa_july, aoi="IA", url="fixture")
    asked: list[tuple] = []

    def stub(spec, *, aoi, start, end, statistics_type=None):
        asked.append((aoi, start.year))
        return rows

    monkeypatch.setattr(client, "rows", stub)
    monkeypatch.setattr(ingest_usdm, "pause", lambda *a: None)

    request = harness.fetch("usdm", states=("IA",), years=(2012, 2026), today=date(2026, 9, 27))
    assert len(ingest_usdm.fetch(request)) == 2
    asked.clear()

    # Second run: 2012 is final and skipped; 2026 gains a map every Thursday.
    ingest_usdm.fetch(request)
    assert asked == [("IA", 2026)]

    # --force re-checks a finished year, which is how a restatement is found.
    asked.clear()
    ingest_usdm.fetch(harness.fetch("usdm", states=("IA",), years=(2012,), force=True,
                                    today=date(2026, 9, 27)))
    assert asked == [("IA", 2012)]


def test_the_archived_body_is_sorted_so_a_changed_hash_means_changed_numbers(iowa_july) -> None:
    rows = client.parse(iowa_july, aoi="IA", url="fixture")
    shuffled = list(reversed(rows))
    # The service orders rows county-major and newest-first under a server-side
    # collation, so the same data can arrive in a different order. Sorting before
    # archiving keeps the hash meaningful.
    assert ingest_usdm._body(rows) == ingest_usdm._body(shuffled)
    first = json.loads(ingest_usdm._body(rows))[0]
    assert first["fips"] == "19001"


def test_the_file_is_published_when_its_newest_map_was(usdm_fixtures: Path) -> None:
    rows = client.parse((usdm_fixtures / "county_ia_2012-07_type1_multiweek.json").read_bytes(),
                        aoi="IA", url="fixture")
    # Newest map is 2012-07-31 (a Tuesday); released the Thursday after.
    assert ingest_usdm._published(rows) == date(2012, 8, 2)


def test_a_non_tuesday_map_date_stops_the_ingest(iowa_july) -> None:
    rows = client.parse(iowa_july, aoi="IA", url="fixture")
    broken = [{**rows[0], "mapDate": "2012-07-18T00:00:00"}]  # a Wednesday
    with pytest.raises(ValueError, match="Tuesday"):
        ingest_usdm._published(broken)


# ------------------------------------------------------------------------ clean


def test_clean_builds_a_county_week_table_with_publication_dates(harness, iowa_july) -> None:
    harness.seed("usdm", "IA/2012", ingest_usdm._body(
        client.parse(iowa_july, aoi="IA", url="fixture")))
    written = clean_usdm.clean(harness.clean("usdm"))
    assert len(written) == 1

    table = harness.processed.read(clean_usdm.TABLE)
    assert len(table) == 99
    assert list(table.columns) == ["fips", "county", "state", "map_date", PUBLICATION_COLUMN,
                                   "none", "d0", "d1", "d2", "d3", "d4", "statistic_format_id"]
    assert table["fips"].str.len().eq(5).all()
    # Tuesday map, Thursday release -- the whole timing argument in one column.
    assert (table[PUBLICATION_COLUMN] - table["map_date"]).dt.days.eq(2).all()
    assert table["fips"].is_monotonic_increasing


def test_clean_de_duplicates_the_year_boundary_overlap(harness, usdm_fixtures: Path) -> None:
    # 19153 over 2012-12-18..2013-01-15: adjacent year keys both contain the maps
    # in the overlap, because a January 1 startdate reaches back into December.
    body = ingest_usdm._body(client.parse(
        (usdm_fixtures / "county_polk_2012-12-18_2013-01-15.json").read_bytes(),
        aoi="19153", url="fixture"))
    harness.seed("usdm", "IA/2012", body)
    harness.seed("usdm", "IA/2013", body)
    clean_usdm.clean(harness.clean("usdm"))
    table = harness.processed.read(clean_usdm.TABLE)
    assert not table.duplicated(subset=["fips", "map_date"]).any()


def test_clean_refuses_a_table_whose_categories_do_not_nest(harness, iowa_july) -> None:
    rows = client.parse(iowa_july, aoi="IA", url="fixture")
    # d2 above d1 cannot happen under type 1; if it does, the column does not mean
    # "D2 or worse" and no correlation computed from it means anything.
    broken = [{**row, "d1": 0.0, "d2": 100.0} for row in rows]
    harness.seed("usdm", "IA/2012", json.dumps(broken).encode())
    with pytest.raises(SystemExit, match="cumulative invariant"):
        clean_usdm.clean(harness.clean("usdm"))


def test_clean_on_an_empty_archive_says_what_to_run(harness) -> None:
    with pytest.raises(SystemExit, match="nothing archived yet"):
        clean_usdm.clean(harness.clean("usdm"))


def test_a_dry_run_clean_writes_no_table(harness, iowa_july) -> None:
    harness.seed("usdm", "IA/2012", ingest_usdm._body(
        client.parse(iowa_july, aoi="IA", url="fixture")))
    assert clean_usdm.clean(harness.clean("usdm", dry_run=True)) == []
    assert harness.processed.names() == []
