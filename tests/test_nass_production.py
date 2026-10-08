"""USDA NASS Quick Stats: the only source whose fixtures are SYNTHETIC.

Everything these tests assert about USDA's behaviour comes from USDA's published
documentation, not from a captured response, because Quick Stats needs a key and
none was available when the client was written. So read every pass here as "the
parser is self-consistent", never as "the parser is right". Only a captured
response settles that, and tests/fixtures/nass/PROVENANCE.md says what to do the
moment a key exists.

The first test in this file guards that honesty: it asserts the fixtures are still
labelled synthetic and the provenance still carries its warning. When real
captures replace them, that test is the one to delete, deliberately.

What is genuinely worth testing even so: the three documented traps that would
each silently corrupt the county production weights. `Value` is a string that is
sometimes not a number; a suppressed county is not a zero; and county_code 998 is
an aggregate rather than a place.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pipeline.calendar import PUBLICATION_COLUMN
from pipeline.clean import nass_production as clean_nass
from pipeline.clients import nass_production as client
from pipeline.ingest import nass_production as ingest_nass


@pytest.fixture
def nass(fixtures: Path) -> Path:
    return fixtures / "nass"


@pytest.fixture
def iowa(nass: Path) -> bytes:
    return (nass / "county_ia_2024_synthetic.json").read_bytes()


# ------------------------------------------------------- the honesty guard


def test_every_fixture_here_is_labelled_synthetic_and_says_so_loudly(nass: Path) -> None:
    # Delete this test when real captures land -- deliberately, having read
    # PROVENANCE.md's replacement checklist. Until then it stops a synthetic file
    # from quietly being treated as evidence.
    data = sorted(path.name for path in nass.glob("*.json"))
    assert data, "no fixtures at all"
    assert all(name.endswith("_synthetic.json") for name in data), data

    # Normalised past the markdown: the warning is a blockquote wrapped across
    # lines, so both the ">" markers and the line breaks have to come out first.
    raw = (nass / "PROVENANCE.md").read_text(encoding="utf-8")
    provenance = " ".join(line.lstrip("> ") for line in raw.splitlines())
    provenance = " ".join(provenance.split())
    assert "THESE BYTES ARE SYNTHETIC" in provenance
    assert "does not prove the parser is right" in provenance


def test_no_fixture_contains_anything_resembling_a_key(nass: Path) -> None:
    # The resolved URL for this source is a secret; a fixture must never carry one.
    for path in nass.glob("*.json"):
        body = path.read_text(encoding="utf-8")
        assert "key=" not in body and "api_key" not in body


# ------------------------------------------------------------------ Value parsing


def test_a_figure_with_thousands_separators_becomes_a_number(iowa) -> None:
    records = {r.county_name: r for r in client.parse(iowa, url="fixture")}
    assert records["POLK"].quantity == 12_345_678.0
    assert records["KOSSUTH"].quantity == 45_678_901.0
    assert records["POLK"].suppression is None


@pytest.mark.parametrize("flag", ["D", "Z", "S", "NA", "X"])
def test_a_suppression_flag_becomes_none_plus_the_reason(flag: str) -> None:
    # (D) withheld to avoid disclosing an individual operation, (Z) less than half
    # the rounding unit, and so on. A bare float() raises on all of them.
    quantity, suppression = client._quantity(f" ({flag}) ")
    assert quantity is None and suppression == flag


def test_an_unrecognised_value_is_none_rather_than_a_crash() -> None:
    quantity, suppression = client._quantity("not a number at all")
    assert quantity is None and suppression == "not a number at all"
    assert client._quantity(None) == (None, None)


def test_a_suppressed_county_is_never_read_as_zero(iowa) -> None:
    records = {r.county_name: r for r in client.parse(iowa, url="fixture")}
    adair = records["ADAIR"]
    assert adair.quantity is None, "a county with production USDA will not print"
    assert adair.quantity != 0, "zero would move its weight to its neighbours"
    assert adair.suppression == "D"


# ------------------------------------------------------------- codes and aggregates


def test_the_county_fips_is_the_federal_code_assembled_from_two_fields(iowa) -> None:
    records = {r.county_name: r for r in client.parse(iowa, url="fixture")}
    assert records["POLK"].fips == "19153"
    # Leading zeros survive: 001 and 003, not 1 and 3.
    assert records["ADAIR"].fips == "19001"
    assert records["ADAMS"].fips == "19003"


def test_the_federal_code_is_not_the_nclimgrid_identifier_for_the_same_county(iowa) -> None:
    # USDA calls Polk County 19153; nClimGrid calls it 13153. Joining the two as if
    # they were the same files Iowa's weather under Massachusetts.
    records = {r.county_name: r for r in client.parse(iowa, url="fixture")}
    assert records["POLK"].fips == "19153"
    assert records["POLK"].fips != "13153"


def test_the_other_counties_row_is_marked_as_an_aggregate(iowa) -> None:
    records = {r.county_name: r for r in client.parse(iowa, url="fixture")}
    other = records["OTHER (COMBINED) COUNTIES"]
    assert other.is_county is False, "a remainder, not a place"
    # Its county_ansi is empty while county_code is 998, which is why the client
    # reads the code rather than the ansi.
    assert other.fips == "19998"


def test_a_record_with_no_county_code_at_all_is_refused() -> None:
    body = json.dumps({"data": [{"state_fips_code": "19", "year": "2024", "Value": "1"}]}).encode()
    with pytest.raises(client.NassResponseError, match="county FIPS cannot be assembled"):
        client.parse(body, url="fixture")


def test_load_time_is_read_in_the_shapes_usda_is_documented_to_send() -> None:
    assert client._load_time("2025-01-10 15:00:00") == date(2025, 1, 10)
    assert client._load_time("2025-01-10") == date(2025, 1, 10)
    assert client._load_time("") is None
    assert client._load_time("sometime last winter") is None


# --------------------------------------------------------------------- envelopes


@pytest.mark.parametrize("name,expected", [
    ("error_unauthorized_synthetic.json", "unauthorized"),
    ("error_exceeds_limit_synthetic.json", "exceeds limit"),
])
def test_an_error_envelope_raises_with_advice(nass: Path, name: str, expected: str) -> None:
    with pytest.raises(client.NassResponseError) as caught:
        client.parse((nass / name).read_bytes(), url="fixture")
    message = str(caught.value)
    assert expected in message
    assert "key was rejected" in message and "narrowed" in message


def test_zero_records_is_ambiguous_and_raises(nass: Path) -> None:
    with pytest.raises(client.NassResponseError) as caught:
        client.parse((nass / "empty_data_synthetic.json").read_bytes(), url="fixture")
    assert "never means 'no production'" in str(caught.value)


def test_an_unexpected_envelope_says_where_to_fix_it(nass: Path) -> None:
    # Deliberately not a claim about USDA: this asserts the parser fails usefully
    # when the documented envelope turns out to be wrong.
    with pytest.raises(client.NassResponseError) as caught:
        client.parse((nass / "wrong_envelope_synthetic.json").read_bytes(), url="fixture")
    message = str(caught.value)
    assert "no 'data' list" in message
    assert "written from documentation, not from a captured response" in message


# ----------------------------------------------------------------------- ingest


def test_a_fetch_without_a_key_refuses_before_doing_anything(harness, no_network) -> None:
    with pytest.raises(SystemExit, match="needs a key"):
        ingest_nass.fetch(harness.fetch("nass_production", api_key=None,
                                        today=date(2026, 9, 27)))


def test_a_dry_run_plans_a_state_year_per_key_with_the_key_redacted(harness, no_network) -> None:
    records = ingest_nass.fetch(harness.fetch(
        "nass_production", api_key="a-test-key", states=("IA",), years=(2023, 2024),
        dry_run=True, today=date(2026, 9, 27)))
    assert [record.key for record in records] == ["IA/2023", "IA/2024"]
    for record in records:
        assert "a-test-key" not in record.url
        assert "key=REDACTED" in record.url
    assert not harness.raw.manifest_path.exists()


def test_the_default_scope_is_recent_years_not_the_whole_archive(harness, no_network) -> None:
    records = ingest_nass.fetch(harness.fetch(
        "nass_production", api_key="k", states=("IA",), dry_run=True, today=date(2026, 9, 27)))
    years = sorted(int(record.key.split("/")[1]) for record in records)
    assert years == [2021, 2022, 2023, 2024, 2025, 2026]


# ------------------------------------------------------------------------ clean


def test_clean_excludes_aggregates_and_keeps_suppressions_as_null(harness, iowa, capsys) -> None:
    harness.seed("nass_production", "IA/2024", iowa, params={"state_alpha": "IA", "year": 2024})
    clean_nass.clean(harness.clean("nass_production"))
    table = harness.processed.read(clean_nass.TABLE)

    assert len(table) == 4, "four counties; the 998 aggregate is excluded"
    assert "19998" not in set(table["fips"])
    suppressed = table[table["fips"] == "19001"].iloc[0]
    assert suppressed["production"] != suppressed["production"]  # NaN, not 0
    assert suppressed["suppression"] == "D"

    out = capsys.readouterr().out
    assert "1 'other counties' aggregate row(s) excluded" in out
    assert "kept as null (never zero)" in out


def test_a_share_is_computed_only_from_the_figures_that_were_printed(harness, iowa) -> None:
    harness.seed("nass_production", "IA/2024", iowa, params={"state_alpha": "IA", "year": 2024})
    clean_nass.clean(harness.clean("nass_production"))
    table = harness.processed.read(clean_nass.TABLE)

    shares = table.set_index("fips")["state_share"].dropna()
    assert shares.sum() == pytest.approx(1.0)
    # Kossuth is much larger than Polk, so it carries most of the weight.
    assert shares["19109"] > shares["19153"]
    # The suppressed counties contribute nothing, so these shares overstate the two
    # that were printed -- which is why the docstring says so rather than hiding it.
    assert len(shares) == 2


def test_the_publication_date_comes_from_load_time(harness, iowa) -> None:
    harness.seed("nass_production", "IA/2024", iowa, params={"state_alpha": "IA", "year": 2024})
    clean_nass.clean(harness.clean("nass_production"))
    table = harness.processed.read(clean_nass.TABLE)
    assert set(table[PUBLICATION_COLUMN].dt.date) == {date(2025, 1, 10)}


def test_the_weight_suggestion_is_printed_in_config_shape_and_not_written(harness, iowa, capsys) -> None:
    harness.seed("nass_production", "IA/2024", iowa, params={"state_alpha": "IA", "year": 2024})
    clean_nass.clean(harness.clean("nass_production"))
    out = capsys.readouterr().out
    # Paste-ready for config/geography.yaml, because which counties to carry and
    # over how many years is a research decision, not a side effect of cleaning.
    assert '- {fips: "19109", name: "Kossuth County, IA", weight:' in out
    assert "2 of 2 counties shown" in out


def test_clean_on_an_empty_archive_says_to_set_the_key_first(harness) -> None:
    with pytest.raises(SystemExit, match="set NASS_API_KEY"):
        clean_nass.clean(harness.clean("nass_production"))
