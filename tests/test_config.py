"""Config loading and, mostly, config REJECTION.

The two YAML files are the reason a new source needs no Python change, so the
value of this module is in the failure cases: a typo, a missing field, an
unquoted FIPS code that YAML turns into a number. Each test asserts the error
names the key path that caused it, because an error that does not is a config
file you have to bisect by hand.

The last group covers the secret handling: a missing NASS_API_KEY must fail with
a message that says where to get one, and must never contain the key itself.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from pipeline.config import (
    ConfigError,
    MonthWindow,
    SourceSpec,
    load_dotenv,
    load_geography,
    load_sources,
)

SOURCES = "config/sources.yaml"
GEOGRAPHY = "config/geography.yaml"

# The three codes for the corn-belt states, as verified in the nClimGrid probe:
# postal -> (federal FIPS, NCEI state code). These differ, and swapping them is
# the single most dangerous mistake available in this project.
STATE_CODES = {"IA": ("19", "13"), "IL": ("17", "11"), "IN": ("18", "12"),
               "MN": ("27", "21"), "NE": ("31", "25")}


def write(path: Path, text: str) -> Path:
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def minimal_source(**overrides: str) -> str:
    body = {
        "kind": "csv",
        "url_template": '"https://example.test/{year}.csv"',
        "params": "{}",
        "publication_lag_days": "1",
        "notes": '"why this source exists"',
    }
    body.update(overrides)
    lines = "\n".join(f"    {key}: {value}" for key, value in body.items())
    return f"sources:\n  demo:\n{lines}\n"


# --------------------------------------------------------------- the real files


def test_every_configured_source_loads(repo_root: Path) -> None:
    sources = load_sources(repo_root / SOURCES)
    assert set(sources) == {"nclimgrid", "nasa_power", "usdm", "yahoo_prices", "owid_yields",
                            "nass_production"}
    for name, spec in sources.items():
        assert spec.name == name
        assert spec.kind in {"csv", "json"}
        assert spec.notes.strip(), f"{name} has no note"


def test_only_nass_needs_a_key_and_it_says_where_to_get_one(repo_root: Path) -> None:
    sources = load_sources(repo_root / SOURCES)
    keyless = [name for name, spec in sources.items() if not spec.needs_key]
    assert sorted(keyless) == ["nasa_power", "nclimgrid", "owid_yields", "usdm", "yahoo_prices"]

    nass = sources["nass_production"]
    assert nass.api_key_env == "NASS_API_KEY"
    assert nass.api_key_signup_url == "https://quickstats.nass.usda.gov/api"


def test_publication_lags_match_the_probe_findings(repo_root: Path) -> None:
    sources = load_sources(repo_root / SOURCES)
    # Verified: Tuesday map, Thursday release.
    assert sources["usdm"].publication_lag_days == 2
    # Assumption, documented in pipeline.calendar: scaled lands ~a week into the
    # following month.
    assert sources["nclimgrid"].publication_lag_days == 7
    # Observed, not scheduled: 5 days on 2026-09-27 against about 3 in the earlier
    # probe. params keeps the range so the timing test can be re-run at either end,
    # which config/sources.yaml explains is not a neutral choice for this project.
    assert sources["nasa_power"].publication_lag_days == 5
    assert sources["nasa_power"].params["observed_latency_days"] == [3, 5]
    # Null is a claim in itself: the date must be read per record, not computed.
    assert sources["owid_yields"].publication_lag_days is None
    assert sources["nass_production"].publication_lag_days is None


def test_usdm_asks_for_json_because_the_default_is_csv(repo_root: Path) -> None:
    sources = load_sources(repo_root / SOURCES)
    assert sources["usdm"].accept == "application/json"


def test_url_templates_fill_from_params_plus_call_site(repo_root: Path) -> None:
    sources = load_sources(repo_root / SOURCES)
    url = sources["nclimgrid"].format_url(year=2012, month=7, var="tmax")
    assert url.endswith("/2012/tmax-201207-cty-scaled.csv")  # status defaulted from params

    usdm = sources["usdm"].format_url(aoi="IA", startdate="2012-07-01", enddate="2012-07-31")
    assert "statisticsType=1" in usdm and "aoi=IA" in usdm


def test_missing_template_value_names_the_placeholder(repo_root: Path) -> None:
    spec = load_sources(repo_root / SOURCES)["nclimgrid"]
    with pytest.raises(ConfigError, match="'var'"):
        spec.format_url(year=2012, month=7)


def test_corn_geography_carries_all_three_state_codes(repo_root: Path) -> None:
    corn = load_geography(repo_root / GEOGRAPHY).crop("corn")
    assert corn.postal_codes() == ("IA", "IL", "IN", "MN", "NE")
    for postal, (fips, ncei) in STATE_CODES.items():
        state = corn.state(postal)
        assert (state.fips, state.ncei) == (fips, ncei)
        assert state.fips != state.ncei, f"{postal}: FIPS and NCEI codes must not be conflated"


def test_corn_sensitive_window_is_planting_through_harvest(repo_root: Path) -> None:
    corn = load_geography(repo_root / GEOGRAPHY).crop("corn")
    assert (corn.sensitive_months.start, corn.sensitive_months.end) == (4, 10)
    assert corn.sensitive_months.contains(7)       # pollination, the month that matters
    assert not corn.sensitive_months.contains(3)
    assert not corn.sensitive_months.wraps


def test_corn_counties_are_a_placeholder_not_a_silent_empty_weighting(repo_root: Path) -> None:
    corn = load_geography(repo_root / GEOGRAPHY).crop("corn")
    assert corn.counties == ()          # production weights land here later
    assert corn.county_weights() == {}  # and callers can ask unconditionally


def test_cocoa_keeps_the_schema_from_being_corn_only(repo_root: Path) -> None:
    cocoa = load_geography(repo_root / GEOGRAPHY).crop("cocoa")
    assert cocoa.states == () and cocoa.counties == ()
    assert [point.name for point in cocoa.points] == ["Soubre", "Daloa", "Kumasi", "Sunyani"]
    assert {point.country for point in cocoa.points} == {"Cote d'Ivoire", "Ghana"}
    assert cocoa.countries == ("Cote d'Ivoire", "Ghana")
    assert cocoa.sensitive_months.months() == (6, 7, 8, 9)


def test_unknown_crop_lists_the_configured_ones(repo_root: Path) -> None:
    geography = load_geography(repo_root / GEOGRAPHY)
    with pytest.raises(ConfigError, match="corn"):
        geography.crop("soybeans")


# --------------------------------------------------------------- rejection


def test_unknown_source_key_names_the_key_path(tmp_path: Path) -> None:
    path = write(tmp_path / "sources.yaml", minimal_source() + "    acccept: text/csv\n")
    with pytest.raises(ConfigError, match=r"sources\.demo\.acccept: unknown key"):
        load_sources(path)


def test_missing_required_source_field_names_the_key_path(tmp_path: Path) -> None:
    body = minimal_source().replace('    url_template: "https://example.test/{year}.csv"\n', "")
    path = write(tmp_path / "sources.yaml", body)
    with pytest.raises(ConfigError, match=r"sources\.demo\.url_template: required key is missing"):
        load_sources(path)


def test_empty_note_is_rejected_so_every_source_is_documented(tmp_path: Path) -> None:
    path = write(tmp_path / "sources.yaml", minimal_source(notes='""'))
    with pytest.raises(ConfigError, match=r"sources\.demo\.notes"):
        load_sources(path)


def test_unknown_kind_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "sources.yaml", minimal_source(kind="netcdf"))
    with pytest.raises(ConfigError, match=r"sources\.demo\.kind: unknown kind 'netcdf'"):
        load_sources(path)


def test_negative_publication_lag_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "sources.yaml", minimal_source(publication_lag_days="-1"))
    with pytest.raises(ConfigError, match=r"sources\.demo\.publication_lag_days"):
        load_sources(path)


def test_unquoted_date_in_params_is_rejected_because_the_manifest_is_json(tmp_path: Path) -> None:
    # YAML parses an unquoted 2000-01-04 into a date object, which would blow up
    # only when the raw manifest line was written at the end of a long fetch.
    path = write(tmp_path / "sources.yaml", minimal_source(params="{first_map_date: 2000-01-04}"))
    with pytest.raises(ConfigError, match=r"sources\.demo\.params: must be JSON-serialisable"):
        load_sources(path)


def test_api_key_env_without_a_signup_url_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "sources.yaml", minimal_source(api_key_env="DEMO_KEY"))
    with pytest.raises(ConfigError, match=r"sources\.demo\.api_key_signup_url"):
        load_sources(path)


def test_wrong_schema_version_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "sources.yaml", "version: 2\n" + minimal_source())
    with pytest.raises(ConfigError, match="version: expected schema version 1"):
        load_sources(path)


def test_missing_file_names_the_path() -> None:
    with pytest.raises(ConfigError, match="no such config file"):
        load_sources("config/does-not-exist.yaml")


def test_unquoted_state_code_is_rejected_for_losing_its_leading_zero(tmp_path: Path) -> None:
    path = write(tmp_path / "geography.yaml", """
        crops:
          demo:
            label: demo
            sensitive_months: {start: 4, end: 10}
            states:
              - {postal: AL, name: Alabama, fips: 01, ncei: "01"}
    """)
    with pytest.raises(ConfigError, match=r'crops\.demo\.states\[0\]\.fips: quote the code'):
        load_geography(path)


def test_unquoted_county_fips_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "geography.yaml", """
        crops:
          demo:
            label: demo
            sensitive_months: {start: 4, end: 10}
            counties:
              - {fips: 19153, name: Polk}
    """)
    with pytest.raises(ConfigError, match=r'crops\.demo\.counties\[0\]\.fips: quote the code'):
        load_geography(path)


def test_partial_county_weights_are_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "geography.yaml", """
        crops:
          demo:
            label: demo
            sensitive_months: {start: 4, end: 10}
            counties:
              - {fips: "19153", weight: 0.5}
              - {fips: "17113"}
    """)
    with pytest.raises(ConfigError, match=r"weights are all-or-nothing"):
        load_geography(path)


def test_month_outside_the_year_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "geography.yaml", """
        crops:
          demo:
            label: demo
            sensitive_months: {start: 4, end: 13}
    """)
    with pytest.raises(ConfigError, match=r"crops\.demo\.sensitive_months\.end: expected a month 1-12"):
        load_geography(path)


def test_out_of_range_point_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "geography.yaml", """
        crops:
          demo:
            label: demo
            sensitive_months: {start: 4, end: 10}
            points:
              - {name: nowhere, lat: 95.0, lon: 0.0}
    """)
    with pytest.raises(ConfigError, match=r"crops\.demo\.points\[0\]\.lat"):
        load_geography(path)


# --------------------------------------------------------------- windows and weights


def test_a_window_may_wrap_the_new_year() -> None:
    # No crop needs this yet, but cocoa's Harmattan damage runs Dec-Feb, so the
    # schema supports it rather than forcing a wrong window later.
    harmattan = MonthWindow(start=11, end=3, label="Harmattan")
    assert harmattan.wraps
    assert harmattan.months() == (11, 12, 1, 2, 3)
    assert harmattan.contains(1) and not harmattan.contains(6)


def test_county_weights_are_normalised(tmp_path: Path) -> None:
    path = write(tmp_path / "geography.yaml", """
        crops:
          demo:
            label: demo
            sensitive_months: {start: 4, end: 10}
            counties:
              - {fips: "19153", weight: 3.0}
              - {fips: "17113", weight: 1.0}
    """)
    weights = load_geography(path).crop("demo").county_weights()
    assert weights == {"19153": 0.75, "17113": 0.25}


# --------------------------------------------------------------- secrets


def key_spec() -> SourceSpec:
    return SourceSpec(
        name="nass_production", kind="json", url_template="https://example.test/?key={key}",
        params={}, publication_lag_days=None, notes="needs a key",
        api_key_env="NASS_API_KEY", api_key_signup_url="https://quickstats.nass.usda.gov/api",
    )


def test_missing_key_names_the_variable_and_where_to_get_one() -> None:
    with pytest.raises(ConfigError) as raised:
        key_spec().api_key(env={})
    message = str(raised.value)
    assert "NASS_API_KEY" in message
    assert "https://quickstats.nass.usda.gov/api" in message
    assert ".env" in message


def test_a_present_key_is_returned_and_never_echoed_in_an_error() -> None:
    assert key_spec().api_key(env={"NASS_API_KEY": "  secret-value  "}) == "secret-value"
    with pytest.raises(ConfigError) as raised:
        key_spec().api_key(env={"NASS_API_KEY": "   "})  # blank counts as unset
    assert "secret-value" not in str(raised.value)


def test_a_keyless_source_returns_none(repo_root: Path) -> None:
    assert load_sources(repo_root / SOURCES)["usdm"].api_key() is None


def test_dotenv_reports_names_not_values_and_respects_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path / ".env", """
        # a comment
        export DEMO_KEY="from-dotenv"
        ALREADY_SET=from-dotenv
        malformed line
    """)
    monkeypatch.delenv("DEMO_KEY", raising=False)
    monkeypatch.setenv("ALREADY_SET", "from-environment")

    loaded = load_dotenv(tmp_path / ".env")
    assert loaded == ["DEMO_KEY"]                  # names only: safe to print
    assert os.environ["DEMO_KEY"] == "from-dotenv"
    assert os.environ["ALREADY_SET"] == "from-environment"  # a real variable wins
    monkeypatch.delenv("DEMO_KEY", raising=False)


def test_a_missing_dotenv_is_not_an_error(tmp_path: Path) -> None:
    assert load_dotenv(tmp_path / "absent.env") == []
