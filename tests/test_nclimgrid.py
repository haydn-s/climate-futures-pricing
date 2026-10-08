"""nClimGrid-Daily: the county identifier is not a FIPS code, and that is the point.

The most dangerous mistake available in this project lives in this file. Column 1
of an nClimGrid county file is a 2-digit NCEI state code followed by a 3-digit
county code, and it is NOT a federal FIPS code: Iowa is NCEI 13 and FIPS 19, so
the file calls Polk County 13153 while USDA calls it 19153. Joining the two as if
they were the same does not raise -- it files Iowa's weather under Massachusetts,
whose NCEI code happens to be 19. So the translation is asserted here directly,
and the resulting numbers are checked against the unit anchors recorded in
tests/fixtures/nclimgrid/PROVENANCE.md.

The other two traps: every file has exactly 31 day slots whatever the month's
length, and -999.99 is the only missing marker. Left unmasked, a July mean for
Polk County lands near -0.3 instead of 34.18.
"""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path

import pytest

from pipeline.calendar import PUBLICATION_COLUMN
from pipeline.clean import nclimgrid as clean_nclimgrid
from pipeline.clients import nclimgrid as client
from pipeline.http import FetchError
from pipeline.ingest import nclimgrid as ingest_nclimgrid


@pytest.fixture
def grid(fixtures: Path) -> Path:
    return fixtures / "nclimgrid"


def seed_month(harness, grid: Path, name: str, key: str, *, status: str = "scaled",
               published: date | None = date(2022, 9, 1), stamp: str = "complete") -> None:
    harness.seed("nclimgrid", key, (grid / name).read_bytes(),
                 params={"status": status}, publication_date=published, version_stamp=stamp)


# --------------------------------------------------------------- version sidecar


@pytest.mark.parametrize("name,year,month,state,status,covers_end", [
    ("ncdd-202608-version.txt", 2026, 8, "complete", "scaled", "2026-08-31"),
    ("ncdd-202609-version.txt", 2026, 9, "prelim", "prelim", "2026-09-12"),
    ("ncdd-201207-version.txt", 2012, 7, "complete", "scaled", "2012-07-31"),
    ("ncdd-195107-version.txt", 1951, 7, "complete", "scaled", "1951-07-31"),
])
def test_both_sidecar_formats_parse(grid: Path, name, year, month, state, status, covers_end) -> None:
    info = client.parse_version((grid / name).read_bytes(), year, month, url=name)
    assert info.state == state
    # The two vocabularies differ: the sidecar says "complete", the filename "scaled".
    assert info.status == status
    assert info.covers_end.isoformat() == covers_end


def test_a_prelim_sidecar_covers_less_than_the_csv_pretends(grid: Path) -> None:
    info = client.parse_version((grid / "ncdd-202609-version.txt").read_bytes(), 2026, 9)
    assert info.covers_end == date(2026, 9, 12)
    # The CSV still carries all 31 day slots for that month.
    rows = list(client.rows((grid / "tmax-202609-cty-prelim.csv").read_bytes(),
                            variable="TMAX", year=2026, month=9))
    assert len(rows[0][2]) == 31
    assert rows[0][2][12] == "-999.99", "day 13 onwards is not covered but is still a column"


def test_the_current_format_carries_a_ghcn_build_and_the_old_one_a_download_date(grid: Path) -> None:
    new = client.parse_version((grid / "ncdd-202608-version.txt").read_bytes(), 2026, 8)
    assert new.build == "3.34-upd-2026090418" and new.created_on == date(2026, 9, 6)
    old = client.parse_version((grid / "ncdd-201207-version.txt").read_bytes(), 2012, 7)
    # Pre-2017 bulk reprocessing has no "created on" line and no build id.
    assert old.build is None and old.created_on is None
    assert old.downloaded == "Sat Apr 22 05:18:09 2017"
    # Both still yield a comparable vintage token.
    assert new.stamp != old.stamp and old.stamp.startswith("complete")


def test_a_sidecar_for_the_wrong_month_is_refused(grid: Path) -> None:
    with pytest.raises(client.NclimgridResponseError, match="covers"):
        client.parse_version((grid / "ncdd-201207-version.txt").read_bytes(), 2012, 8)


def test_an_unrecognisable_sidecar_is_refused(grid: Path) -> None:
    with pytest.raises(client.NclimgridResponseError, match="complete|prelim"):
        client.parse_version(b"some other file entirely", 2012, 7)


# ------------------------------------------------------------------- county rows


def test_a_county_row_keeps_its_identifier_as_text(grid: Path) -> None:
    rows = list(client.rows((grid / "tmax-201207-cty-scaled.csv").read_bytes(),
                            variable="TMAX", year=2012, month=7))
    assert len(rows) == 10
    identifier, name, slots = rows[5]
    assert identifier == "13153" and isinstance(identifier, str)
    assert name == "IA: Polk County"
    assert len(slots) == 31


def test_always_thirty_one_slots_whatever_the_month(grid: Path) -> None:
    for name, variable, year, month in [("prcp-201209-cty-scaled.csv", "PRCP", 2012, 9),
                                        ("tmax-201202-cty-scaled.csv", "TMAX", 2012, 2),
                                        ("tmax-201302-cty-scaled.csv", "TMAX", 2013, 2)]:
        rows = list(client.rows((grid / name).read_bytes(), variable=variable,
                                year=year, month=month))
        real = calendar.monthrange(year, month)[1]
        slots = rows[0][2]
        assert len(slots) == 31, "the column count never tells you the month's length"
        assert all(value == "-999.99" for value in slots[real:]), "the tail is padding"


@pytest.mark.parametrize("variable,year,month", [("PRCP", 2012, 7), ("TMAX", 2012, 8), ("TMAX", 2013, 7)])
def test_rows_refuse_a_file_that_is_not_what_was_asked_for(grid: Path, variable, year, month) -> None:
    with pytest.raises(client.NclimgridResponseError):
        list(client.rows((grid / "tmax-201207-cty-scaled.csv").read_bytes(),
                         variable=variable, year=year, month=month))


def test_a_short_row_is_refused_rather_than_read_off_the_end() -> None:
    with pytest.raises(client.NclimgridResponseError, match="37 fields"):
        list(client.rows(b"cty,13153,IA: Polk County,2012,07,TMAX,1.0,2.0",
                         variable="TMAX", year=2012, month=7))


def test_a_file_with_no_county_rows_is_refused() -> None:
    with pytest.raises(client.NclimgridResponseError, match="no county rows"):
        list(client.rows(b"\n\n", variable="TMAX", year=2012, month=7))


def test_the_state_prefix_in_the_name_is_the_authority(grid: Path) -> None:
    # NCEI's own cross-reference has state_name swapped for codes 11 and 12, so the
    # "IA: " prefix is what can be trusted -- never a name-keyed lookup.
    assert client.postal_from_name("IA: Polk County") == "IA"
    assert client.postal_from_name("IL: Champaign County") == "IL"
    with pytest.raises(client.NclimgridResponseError):
        client.postal_from_name("Polk County")


# ------------------------------------------------------------- status and fallback


def test_the_sidecar_beats_the_calendar_guess(sources, grid: Path) -> None:
    spec = sources["nclimgrid"]
    prelim = client.parse_version((grid / "ncdd-202609-version.txt").read_bytes(), 2026, 9)
    # The calendar rule alone would call 2026-09 scaled once October arrives; the
    # sidecar says prelim, and it is a statement of fact.
    assert client.status_for(spec, 2026, 9, date(2026, 10, 20)) == "scaled"
    assert client.status_for(spec, 2026, 9, date(2026, 10, 20), prelim) == "prelim"


def test_a_404_on_the_expected_status_falls_back_to_the_other(sources, monkeypatch, grid: Path) -> None:
    # The month in progress exists ONLY as prelim; once scaled exists the prelim
    # file is deleted, so either name can be the 404 depending on the day.
    tried: list[str] = []

    def stub(url, **kwargs):
        tried.append(url)
        if "scaled" in url:
            raise FetchError("HTTP 404", url=url, status=404)
        return (grid / "tmax-202609-cty-prelim.csv").read_bytes(), {"last-modified": "x"}

    monkeypatch.setattr(client, "fetch_with_headers", stub)
    body, headers, status = client.month(sources["nclimgrid"], "tmax", 2026, 9,
                                         today=date(2026, 10, 20))
    assert status == "prelim"
    assert len(tried) == 2 and "scaled" in tried[0] and "prelim" in tried[1]


def test_neither_status_existing_is_reported_clearly(sources, monkeypatch) -> None:
    monkeypatch.setattr(client, "fetch_with_headers",
                        lambda url, **kw: (_ for _ in ()).throw(FetchError("404", url=url, status=404)))
    with pytest.raises(client.NclimgridResponseError, match="neither"):
        client.month(sources["nclimgrid"], "tmax", 1940, 1, today=date(2026, 9, 27))


def test_a_non_404_error_is_not_swallowed_by_the_fallback(sources, monkeypatch) -> None:
    monkeypatch.setattr(client, "fetch_with_headers",
                        lambda url, **kw: (_ for _ in ()).throw(FetchError("500", url=url, status=500)))
    with pytest.raises(FetchError):
        client.month(sources["nclimgrid"], "tmax", 2012, 7, today=date(2026, 9, 27))


# ----------------------------------------------------------------------- ingest


def test_the_months_in_scope_ignore_a_crop_this_source_cannot_serve(geography) -> None:
    # Cocoa is points-only and nClimGrid is US-county-only, so cocoa's June-to-
    # September window must not widen a fetch. Corn's April-October is the answer.
    assert ingest_nclimgrid._months(geography) == (4, 5, 6, 7, 8, 9, 10)


def test_a_dry_run_plans_a_sidecar_and_one_file_per_variable(harness, no_network, capsys) -> None:
    records = ingest_nclimgrid.fetch(harness.fetch(
        "nclimgrid", years=(2012,), months=(7,), dry_run=True, today=date(2026, 9, 27)))
    assert [record.key for record in records] == [
        "version/201207", "prcp/201207", "tmax/201207", "tmin/201207", "tavg/201207"]
    # Every file is the whole nation, so the plan's size is worth saying out loud.
    assert "about 4 MB" in capsys.readouterr().out


def test_a_future_month_is_not_planned(harness, no_network) -> None:
    records = ingest_nclimgrid.fetch(harness.fetch(
        "nclimgrid", years=(2026,), months=(8, 9, 10), dry_run=True, today=date(2026, 9, 27)))
    assert {key.split("/")[1] for key in (r.key for r in records)} == {"202608", "202609"}


def test_a_month_already_archived_and_consistent_is_settled(harness, grid: Path) -> None:
    stamp = client.parse_version((grid / "ncdd-201207-version.txt").read_bytes(), 2012, 7).stamp
    request = harness.fetch("nclimgrid", years=(2012,), months=(7,), today=date(2026, 9, 27))
    variables = ["prcp", "tmax"]

    harness.seed("nclimgrid", "version/201207", b"x", version_stamp=stamp)
    assert not ingest_nclimgrid._month_is_settled(request, harness.raw.latest("nclimgrid", "version/201207"),
                                                 variables, 2012, 7), "no data files yet"
    for variable in variables:
        harness.seed("nclimgrid", f"{variable}/201207", f"{variable}".encode(), version_stamp=stamp)
    assert ingest_nclimgrid._month_is_settled(request, harness.raw.latest("nclimgrid", "version/201207"),
                                             variables, 2012, 7)


def test_a_prelim_month_is_never_settled(harness, grid: Path) -> None:
    # A prelim file is rebuilt daily and then deleted outright, so an unarchived
    # observation of one is gone for good.
    stamp = client.parse_version((grid / "ncdd-202609-version.txt").read_bytes(), 2026, 9).stamp
    harness.seed("nclimgrid", "version/202609", b"x", version_stamp=stamp)
    harness.seed("nclimgrid", "tmax/202609", b"y", version_stamp=stamp)
    request = harness.fetch("nclimgrid", today=date(2026, 9, 27))
    assert not ingest_nclimgrid._month_is_settled(
        request, harness.raw.latest("nclimgrid", "version/202609"), ["tmax"], 2026, 9)


# ------------------------------------------------------------------------ clean


def test_the_ncei_identifier_becomes_a_federal_fips(harness, grid: Path) -> None:
    seed_month(harness, grid, "tmax-201207-cty-scaled.csv", "tmax/201207")
    clean_nclimgrid.clean(harness.clean("nclimgrid"))
    table = harness.processed.read(clean_nclimgrid.TABLE)

    polk = table[table["county"] == "Polk County"]
    assert set(polk["fips"]) == {"19153"}, "FIPS 19153, translated from NCEI 13153"
    assert "13153" not in set(table["fips"]), "the NCEI identifier must not survive"
    # 19*** is Massachusetts in NCEI's numbering -- the error this guards against.
    assert set(table["state"]) == {"IA", "IL", "IN", "MN", "NE"}
    assert set(table[table["state"] == "IA"]["fips"].str[:2]) == {"19"}
    assert set(table[table["state"] == "IL"]["fips"].str[:2]) == {"17"}


def test_the_provenance_unit_anchors_are_reproduced(harness, grid: Path) -> None:
    # From tests/fixtures/nclimgrid/PROVENANCE.md: Polk County, IA, July 2012 peaks
    # at 39.78 C on the 24th and totals 37.28 mm. The mean is the sentinel test --
    # unmasked it lands near -0.3 instead of 34.18.
    seed_month(harness, grid, "tmax-201207-cty-scaled.csv", "tmax/201207")
    seed_month(harness, grid, "prcp-201207-cty-scaled.csv", "prcp/201207")
    clean_nclimgrid.clean(harness.clean("nclimgrid"))
    polk = harness.processed.read(clean_nclimgrid.TABLE).query("fips == '19153'")

    assert len(polk) == 31
    assert polk["tmax_c"].max() == pytest.approx(39.78)
    assert polk.loc[polk["tmax_c"].idxmax(), "date"].date() == date(2012, 7, 24)
    assert polk["prcp_mm"].sum() == pytest.approx(37.28, abs=0.01)
    assert polk["tmax_c"].mean() == pytest.approx(34.18, abs=0.01)


@pytest.mark.parametrize("name,key,year,month,days", [
    ("prcp-201209-cty-scaled.csv", "prcp/201209", 2012, 9, 30),
    ("tmax-201202-cty-scaled.csv", "tmax/201202", 2012, 2, 29),
    ("tmax-201302-cty-scaled.csv", "tmax/201302", 2013, 2, 28),
])
def test_the_month_length_comes_from_the_calendar(harness, grid: Path, name, key, year, month, days) -> None:
    seed_month(harness, grid, name, key)
    clean_nclimgrid.clean(harness.clean("nclimgrid"))
    table = harness.processed.read(clean_nclimgrid.TABLE)
    polk = table[table["fips"] == "19153"]
    assert len(polk) == days
    assert polk["date"].max().day == days


def test_a_prelim_month_stops_where_its_data_does_and_says_so(harness, grid: Path) -> None:
    seed_month(harness, grid, "tmax-202609-cty-prelim.csv", "tmax/202609",
               status="prelim", published=date(2026, 9, 15), stamp="prelim")
    clean_nclimgrid.clean(harness.clean("nclimgrid"))
    table = harness.processed.read(clean_nclimgrid.TABLE)
    # Days 13-30 are the sentinel in every row, so they are not observations.
    assert table["date"].max().date() == date(2026, 9, 12)
    assert set(table["status"]) == {"prelim"}


def test_a_month_with_mixed_statuses_is_called_prelim(harness, grid: Path) -> None:
    seed_month(harness, grid, "tmax-202609-cty-prelim.csv", "tmax/202609", status="prelim")
    seed_month(harness, grid, "prcp-201207-cty-scaled.csv", "prcp/201207", status="scaled")
    clean_nclimgrid.clean(harness.clean("nclimgrid"))
    table = harness.processed.read(clean_nclimgrid.TABLE)
    assert set(table[table["date"].dt.year == 2026]["status"]) == {"prelim"}
    assert set(table[table["date"].dt.year == 2012]["status"]) == {"scaled"}


def test_the_publication_date_is_the_last_of_the_months_files(harness, grid: Path) -> None:
    seed_month(harness, grid, "tmax-201207-cty-scaled.csv", "tmax/201207",
               published=date(2022, 9, 1))
    seed_month(harness, grid, "prcp-201207-cty-scaled.csv", "prcp/201207",
               published=date(2022, 9, 3))
    clean_nclimgrid.clean(harness.clean("nclimgrid"))
    table = harness.processed.read(clean_nclimgrid.TABLE)
    # A row is knowable when the LAST of its variables is.
    assert set(table[PUBLICATION_COLUMN].dt.date) == {date(2022, 9, 3)}


def test_a_state_table_disagreeing_with_the_file_stops_the_run(harness, grid: Path, monkeypatch) -> None:
    seed_month(harness, grid, "tmax-201207-cty-scaled.csv", "tmax/201207")
    # Pretend config/geography.yaml claims NCEI 13 is Illinois. The file says IA,
    # and the file wins -- loudly, because this mistake is otherwise invisible.
    real = clean_nclimgrid._state_index

    def wrong(geography):
        index = real(geography)
        index["13"] = index["11"]  # NCEI 13 -> the IL StateRef
        return index

    monkeypatch.setattr(clean_nclimgrid, "_state_index", wrong)
    with pytest.raises(SystemExit, match="name prefix is authoritative"):
        clean_nclimgrid.clean(harness.clean("nclimgrid"))


def test_clean_on_an_empty_archive_says_what_to_run(harness) -> None:
    with pytest.raises(SystemExit, match="nothing archived yet"):
        clean_nclimgrid.clean(harness.clean("nclimgrid"))
