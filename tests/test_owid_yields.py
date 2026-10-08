"""Our World in Data yields: the value column's name is different in every dataset.

`maize_yield` for maize; `cocoa_beans__00000661__yield__005412__tonnes_per_hectare`
for cocoa. It can only be found BY EXCLUSION -- whatever is not entity, code or
year -- and selecting it by name works right up until the next slug. The two
fixtures spell it as differently as possible for exactly that reason.

This module also asserts the fixtures are still a verbatim subset of what the
endpoint serves, which is the promise tests/fixtures/owid/PROVENANCE.md makes, and
that no row of this table carries a publication date -- because this source
serves none, and pretending otherwise would let it into a point-in-time test it
has no business in.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from pipeline.calendar import PUBLICATION_COLUMN, as_of
from pipeline.clean import owid_yields as clean_owid
from pipeline.clean._common import fold, matches_any
from pipeline.clients import owid_yields as client
from pipeline.ingest import owid_yields as ingest_owid


@pytest.fixture
def owid(fixtures: Path) -> Path:
    return fixtures / "owid"


# ---------------------------------------------------------- the fixtures' promise


@pytest.mark.parametrize("slug", ["maize-yields", "cocoa-bean-yields"])
def test_a_fixture_is_a_verbatim_row_selection(owid: Path, slug: str) -> None:
    # PROVENANCE.md claims row selection and nothing else. These are the first
    # three of its four assertions; the fourth compares against a live response and
    # is a manual step, because this suite is offline by design.
    lines = [line for line in (owid / f"{slug}.csv").read_bytes().split(b"\n") if line]
    assert lines[0].startswith(b"entity,code,year,")
    assert not (owid / f"{slug}.csv").read_bytes().endswith(b"\n"), "no trailing newline, as served"
    assert len(lines) > 1


def test_the_fixtures_are_pure_ascii_so_the_diacritic_warning_is_not_exercised(owid: Path) -> None:
    # config/sources.yaml warns that "Cote d'Ivoire" appears with and without
    # diacritics across datasets. As of 2026-09-27 that is NOT reproducible: both
    # responses are pure ASCII. The folding below is therefore defensive, and its
    # accent path is exercised by a constructed string, not by these bytes.
    for slug in ("maize-yields", "cocoa-bean-yields"):
        body = (owid / f"{slug}.csv").read_bytes()
        assert all(byte < 128 for byte in body)
        assert b"Cote d'Ivoire" in body or slug == "maize-yields"


# ----------------------------------------------------------- the value column


def test_the_value_column_is_found_by_exclusion(owid: Path) -> None:
    _, maize = client.parse((owid / "maize-yields.csv").read_bytes())
    _, cocoa = client.parse((owid / "cocoa-bean-yields.csv").read_bytes())
    assert maize == "maize_yield"
    assert cocoa == "cocoa_beans__00000661__yield__005412__tonnes_per_hectare"
    assert maize != cocoa, "the whole reason it cannot be selected by name"


def test_a_missing_key_column_names_it() -> None:
    with pytest.raises(client.OwidResponseError, match="useColumnShortNames"):
        client.parse(b"Entity,Code,Year,maize_yield\nUnited States,USA,2012,7.7\n")


def test_two_value_columns_are_ambiguous_and_refused() -> None:
    with pytest.raises(client.OwidResponseError, match="exactly one value column"):
        client.parse(b"entity,code,year,a,b\nUnited States,USA,2012,1,2\n")


def test_no_value_column_is_refused() -> None:
    with pytest.raises(client.OwidResponseError, match="exactly one value column"):
        client.parse(b"entity,code,year\nUnited States,USA,2012\n")


def test_a_csv_with_no_data_rows_is_refused() -> None:
    with pytest.raises(client.OwidResponseError, match="no data rows"):
        client.parse(b"entity,code,year,maize_yield\n")


def test_a_byte_order_mark_does_not_hide_the_first_column() -> None:
    body = "﻿entity,code,year,maize_yield\nUnited States,USA,2012,7.7".encode("utf-8")
    rows, column = client.parse(body)
    assert column == "maize_yield" and rows[0]["entity"] == "United States"


# ------------------------------------------------------------- entity matching


def test_folding_makes_the_two_spellings_of_one_country_match() -> None:
    assert fold("Côte d'Ivoire") == fold("Cote d'Ivoire") == "cote d'ivoire"
    assert matches_any("Côte d'Ivoire", ("Cote d'Ivoire",))
    assert matches_any("Cote d'Ivoire", ("Côte d'Ivoire",))


def test_a_regional_aggregate_does_not_match_a_country() -> None:
    for aggregate in ("Africa (FAO)", "World", "Western Africa (FAO)"):
        assert not matches_any(aggregate, ("Cote d'Ivoire", "Ghana"))


def test_matching_is_by_prefix_so_a_qualifier_does_not_break_the_join() -> None:
    assert matches_any("United States of America", ("United States",))
    assert not matches_any("United", ("United States",))


# ----------------------------------------------------------------------- ingest


def test_a_dry_run_plans_one_key_per_slug(harness, no_network) -> None:
    """One key per slug, yield slugs AND production slugs.

    Derived from the shipped config: production was added as a parallel slug map
    so a bloc's area could be recovered, and a frozen list failed for that rather
    than for anything behavioural.
    """
    spec = harness.sources["owid_yields"]
    expected = sorted(list(spec.params["slugs"].values())
                      + list((spec.params.get("production_slugs") or {}).values()))
    records = ingest_owid.fetch(harness.fetch("owid_yields", dry_run=True))
    assert sorted(record.key for record in records) == expected
    assert len(expected) > len(spec.params["slugs"]), "production slugs are planned too"
    assert not harness.raw.manifest_path.exists()


def test_the_scope_flags_are_reported_as_meaningless_here(harness, no_network, capsys) -> None:
    ingest_owid.fetch(harness.fetch("owid_yields", years=(2012,), dry_run=True))
    assert "no scope filters" in capsys.readouterr().out


# ------------------------------------------------------------------------ clean


def seed(harness, owid: Path) -> None:
    for slug in ("maize-yields", "cocoa-bean-yields"):
        harness.seed("owid_yields", slug, (owid / f"{slug}.csv").read_bytes(),
                     params={"slug": slug}, publication_date=None)


def test_clean_keeps_only_the_countries_each_crop_declares(harness, owid: Path) -> None:
    seed(harness, owid)
    clean_owid.clean(harness.clean("owid_yields"))
    table = harness.processed.read(clean_owid.TABLE)

    assert set(table[table["crop"] == "corn"]["entity"]) == {"United States"}
    assert set(table[table["crop"] == "cocoa"]["entity"]) == {"Cote d'Ivoire", "Ghana"}
    # The aggregates in the fixtures must not survive the join.
    assert "Africa (FAO)" not in set(table["entity"])
    assert "World" not in set(table["entity"])


def test_clean_preserves_the_2012_shortfall_the_project_is_built_around(harness, owid: Path) -> None:
    seed(harness, owid)
    clean_owid.clean(harness.clean("owid_yields"))
    table = harness.processed.read(clean_owid.TABLE)
    corn = table[table["crop"] == "corn"].set_index("year")[clean_owid.VALUE]
    assert corn[2012] == pytest.approx(7.7260685)
    # Well below both neighbours -- the drought year.
    assert corn[2012] < corn[2011] and corn[2012] < corn[2013]


def test_no_row_carries_a_publication_date_and_as_of_therefore_drops_them_all(
        harness, owid: Path, capsys) -> None:
    seed(harness, owid)
    clean_owid.clean(harness.clean("owid_yields"))
    table = harness.processed.read(clean_owid.TABLE)

    assert table[PUBLICATION_COLUMN].isna().all()
    assert "use it for validation, not timing" in capsys.readouterr().out
    # The consequence, asserted rather than assumed: this table cannot leak into a
    # point-in-time test, because as_of keeps nothing from it.
    assert len(as_of(table, PUBLICATION_COLUMN, "2026-09-27")) == 0


def test_the_value_column_is_renamed_to_something_a_human_can_read(harness, owid: Path) -> None:
    seed(harness, owid)
    clean_owid.clean(harness.clean("owid_yields"))
    table = harness.processed.read(clean_owid.TABLE)
    assert clean_owid.VALUE in table.columns
    assert "cocoa_beans__00000661__yield__005412__tonnes_per_hectare" not in table.columns


def test_a_crop_whose_countries_match_nothing_says_what_the_file_offers(harness, owid: Path) -> None:
    body = (owid / "maize-yields.csv").read_bytes()
    rows = list(csv.DictReader(io.StringIO(body.decode())))
    # Drop the United States, leaving only entities corn does not name.
    kept = [row for row in rows if row["entity"] != "United States"]
    header = body.split(b"\n")[0]
    trimmed = header + b"\n" + b"\n".join(
        f"{r['entity']},{r['code']},{r['year']},{r['maize_yield']}".encode() for r in kept)
    harness.seed("owid_yields", "maize-yields", trimmed, params={"slug": "maize-yields"})
    harness.seed("owid_yields", "cocoa-bean-yields",
                 (owid / "cocoa-bean-yields.csv").read_bytes(), params={"slug": "cocoa-bean-yields"})
    with pytest.raises(SystemExit) as caught:
        clean_owid.clean(harness.clean("owid_yields"))
    assert "United States" in str(caught.value)
    assert "Africa (FAO)" in str(caught.value), "say what the file does offer"


def test_clean_on_an_empty_archive_says_what_to_run(harness) -> None:
    with pytest.raises(SystemExit, match="nothing archived yet"):
        clean_owid.clean(harness.clean("owid_yields"))
