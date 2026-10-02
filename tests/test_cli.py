"""The command line's dispatch contract, which is the only thing pipeline.cli owns.

Four sources are written by four hands against one request object, so the
guarantees worth testing are the ones that stop those four disagreeing:

  * a scope argument means the same thing everywhere (`--years 2000-2025` and
    `--years 2012 2013` and `--years 2012,2013` all parse here, once);
  * a missing per-source module says which file to write, rather than raising an
    ImportError traceback at whoever ran it;
  * an api_key is resolved BEFORE dispatch, so a missing NASS_API_KEY fails
    immediately instead of part-way through a module's work;
  * --dry-run is passed through, never interpreted here, because only the source's
    own module knows which URLs it would have fetched.

Nothing here touches the network: the two commands that could (fetch, clean) are
either dry-run or pointed at a stub module.
"""

from __future__ import annotations

import sys
import textwrap
import types
from datetime import date
from pathlib import Path

import pytest

from pipeline import cli
from pipeline.cli import _int_list, _states, build_parser, main


# ------------------------------------------------------------------- arguments


@pytest.mark.parametrize("given,expected", [
    (["2012"], (2012,)),
    (["2012", "2013"], (2012, 2013)),
    (["2012,2013"], (2012, 2013)),
    (["2000-2003"], (2000, 2001, 2002, 2003)),
    (["2012", "2000-2002", "2015,2016"], (2012, 2000, 2001, 2002, 2015, 2016)),
    (["2012-2012"], (2012,)),
    ([" 2012 , 2013 "], (2012, 2013)),
])
def test_years_parse_as_numbers_ranges_and_mixtures(given: list[str], expected: tuple) -> None:
    assert _int_list(given, "years", 1951, 2100) == expected


def test_a_repeated_year_is_kept_once_in_the_order_it_was_given() -> None:
    assert _int_list(["2013", "2012", "2013"], "years", 1951, 2100) == (2013, 2012)


def test_a_month_range_needs_no_shell_loop() -> None:
    assert _int_list(["4-10"], "months", 1, 12) == (4, 5, 6, 7, 8, 9, 10)


@pytest.mark.parametrize("given", [["banana"], ["2012-banana"], ["20-12-13"]])
def test_an_unparseable_scope_says_what_a_range_looks_like(given: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        _int_list(given, "years", 1951, 2100)
    assert "range like 4-10" in str(caught.value)


@pytest.mark.parametrize("given,low,high", [(["1900"], 1951, 2100), (["13"], 1, 12), (["0"], 1, 12)])
def test_a_scope_outside_the_bounds_names_them(given: list[str], low: int, high: int) -> None:
    with pytest.raises(SystemExit) as caught:
        _int_list(given, "months", low, high)
    assert f"outside {low}-{high}" in str(caught.value)


def test_a_reversed_range_is_rejected_rather_than_silently_empty() -> None:
    with pytest.raises(SystemExit):
        _int_list(["2015-2012"], "years", 1951, 2100)


def test_states_are_upper_cased_split_and_de_duplicated() -> None:
    assert _states(["ia", "IL,in"]) == ("IA", "IL", "IN")
    assert _states(["IA", "ia"]) == ("IA",)
    assert _states([" ia , il "]) == ("IA", "IL")


def test_the_global_options_work_on_either_side_of_the_verb() -> None:
    # `--data-root X fetch` and `fetch --data-root X` must mean the same thing, or
    # every example in every docstring has to pick a side.
    parser = build_parser()
    before = parser.parse_args(["--data-root", "/tmp/a", "status"])
    after = parser.parse_args(["status", "--data-root", "/tmp/a"])
    assert before.data_root == after.data_root == "/tmp/a"
    assert before.command == after.command == "status"


def test_the_defaults_point_at_the_shipped_config() -> None:
    args = build_parser().parse_args(["check-config"])
    assert args.sources_config.endswith("config/sources.yaml")
    assert args.geography_config.endswith("config/geography.yaml")
    assert args.data_root is None  # resolved to the repository's data/ later


# -------------------------------------------------------------------- dispatch


def write_sources(path: Path, name: str = "demo") -> Path:
    path.write_text(textwrap.dedent(f"""
        version: 1
        sources:
          {name}:
            kind: csv
            url_template: "https://example.test/{{year}}.csv"
            params: {{}}
            publication_lag_days: 1
            notes: "a source with no module, for the dispatch tests"
    """), encoding="utf-8")
    return path


def test_an_unknown_source_lists_the_configured_ones(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["fetch", "--source", "nope", "--data-root", str(tmp_path)])
    message = str(caught.value)
    assert "unknown source 'nope'" in message
    assert "nasa_power" in message and "usdm" in message


def test_a_missing_ingest_module_names_the_file_to_write(tmp_path: Path) -> None:
    config = write_sources(tmp_path / "sources.yaml")
    with pytest.raises(SystemExit) as caught:
        main(["fetch", "--source", "demo", "--sources-config", str(config),
              "--data-root", str(tmp_path)])
    message = str(caught.value)
    assert "src/pipeline/ingest/demo.py" in message
    assert "fetch(request)" in message


def test_a_missing_clean_module_names_the_file_too(tmp_path: Path) -> None:
    config = write_sources(tmp_path / "sources.yaml")
    with pytest.raises(SystemExit) as caught:
        main(["clean", "--source", "demo", "--sources-config", str(config),
              "--data-root", str(tmp_path)])
    assert "src/pipeline/clean/demo.py" in str(caught.value)
    assert "clean(request)" in str(caught.value)


def test_a_module_without_the_entry_point_says_so(tmp_path: Path, monkeypatch) -> None:
    config = write_sources(tmp_path / "sources.yaml")
    # A module that exists but forgot its entry point: a real failure mode when a
    # new source is half-written, and a confusing AttributeError if unchecked.
    monkeypatch.setitem(sys.modules, "pipeline.ingest.demo", types.ModuleType("pipeline.ingest.demo"))
    with pytest.raises(SystemExit) as caught:
        main(["fetch", "--source", "demo", "--sources-config", str(config),
              "--data-root", str(tmp_path)])
    assert "does not define fetch(request)" in str(caught.value)


def test_an_import_error_inside_a_source_module_is_not_swallowed(tmp_path: Path, monkeypatch) -> None:
    # _module only converts a ModuleNotFoundError for the module it asked for. A
    # source module whose own import is broken must raise, not be reported as
    # "not written yet".
    config = write_sources(tmp_path / "sources.yaml")

    def explode(name: str) -> None:
        raise ModuleNotFoundError("No module named 'scipy'", name="scipy")

    monkeypatch.setattr(cli.importlib, "import_module", explode)
    with pytest.raises(ModuleNotFoundError):
        main(["fetch", "--source", "demo", "--sources-config", str(config),
              "--data-root", str(tmp_path)])


# ---------------------------------------------------------------------- secrets


def test_a_missing_key_fails_before_the_module_is_imported(tmp_path: Path, monkeypatch, capsys) -> None:
    # Independent of whether a real .env exists on this machine: the loader is
    # stubbed out and the variable removed, so the test asserts the CLI's ordering
    # rather than the developer's environment.
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: [])
    monkeypatch.setattr("pipeline.config.load_dotenv", lambda *a, **k: [])
    monkeypatch.delenv("NASS_API_KEY", raising=False)
    imported: list[str] = []
    monkeypatch.setattr(cli, "_module", lambda *a: imported.append(a[1]))

    assert main(["fetch", "--source", "nass_production", "--data-root", str(tmp_path)]) == 1
    assert imported == [], "the module must not be imported when the key is missing"
    error = capsys.readouterr().err
    assert "NASS_API_KEY is not set" in error
    assert "https://quickstats.nass.usda.gov/api" in error


def test_a_resolved_key_reaches_the_module_which_never_reads_the_environment(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NASS_API_KEY", "a-test-key")
    captured: list = []
    stub = types.ModuleType("pipeline.ingest.nass_production")
    stub.fetch = lambda request: captured.append(request) or []
    monkeypatch.setitem(sys.modules, "pipeline.ingest.nass_production", stub)

    assert main(["fetch", "--source", "nass_production", "--data-root", str(tmp_path)]) == 0
    assert captured[0].api_key == "a-test-key"


def test_a_keyless_source_is_handed_none_rather_than_an_empty_string(
        tmp_path: Path, monkeypatch) -> None:
    captured: list = []
    stub = types.ModuleType("pipeline.ingest.usdm")
    stub.fetch = lambda request: captured.append(request) or []
    monkeypatch.setitem(sys.modules, "pipeline.ingest.usdm", stub)

    main(["fetch", "--source", "usdm", "--data-root", str(tmp_path)])
    assert captured[0].api_key is None


# ------------------------------------------------------------------ pass-through


def test_scope_and_flags_reach_the_module_unchanged(tmp_path: Path, monkeypatch) -> None:
    captured: list = []
    stub = types.ModuleType("pipeline.ingest.usdm")
    stub.fetch = lambda request: captured.append(request) or []
    monkeypatch.setitem(sys.modules, "pipeline.ingest.usdm", stub)

    main(["fetch", "--source", "usdm", "--years", "2012-2013", "--months", "7",
          "--states", "ia,il", "--force", "--dry-run", "--data-root", str(tmp_path)])
    request = captured[0]
    assert request.years == (2012, 2013)
    assert request.months == (7,)
    assert request.states == ("IA", "IL")
    assert request.force is True
    # Interpreted by the module, never here.
    assert request.dry_run is True


def test_today_is_injected_rather_than_read_from_the_clock(tmp_path: Path, monkeypatch) -> None:
    captured: list = []
    stub = types.ModuleType("pipeline.ingest.usdm")
    stub.fetch = lambda request: captured.append(request) or []
    monkeypatch.setitem(sys.modules, "pipeline.ingest.usdm", stub)

    main(["fetch", "--source", "usdm", "--data-root", str(tmp_path)])
    # A default, but a real date -- a module must be able to prefer it over now().
    assert isinstance(captured[0].today, date)


def test_a_dry_run_reports_without_writing(tmp_path: Path, capsys) -> None:
    # usdm's dry run decides everything from config, so this exercises the real
    # module end to end with no network and no archive.
    assert main(["fetch", "--source", "usdm", "--states", "IA", "--years", "2012",
                 "--dry-run", "--data-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "would fetch 1 file(s)" in out and "IA/2012" in out
    assert not (tmp_path / "manifest.jsonl").exists()
    assert not (tmp_path / "raw").exists()


# ----------------------------------------------------------------- the commands


def test_check_config_validates_both_files_and_reports_key_status(capsys) -> None:
    assert main(["check-config"]) == 0
    out = capsys.readouterr().out
    from pipeline.config import (DEFAULT_GEOGRAPHY_PATH, DEFAULT_SOURCES_PATH,
                                 load_geography, load_sources)
    # Both counts derived: freezing either means this test fails on the one
    # change -- adding a source or a crop -- that ought to leave it passing.
    sources = len(load_sources(DEFAULT_SOURCES_PATH))
    crops = len(list(load_geography(DEFAULT_GEOGRAPHY_PATH)))
    assert f"{sources} sources" in out and f"{crops} crops" in out
    assert "config ok" in out


def test_check_config_never_prints_a_secret_value(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NASS_API_KEY", "super-secret-value")
    main(["check-config"])
    out = capsys.readouterr().out
    assert "super-secret-value" not in out
    assert "NASS_API_KEY set" in out  # the name, not the value


def test_a_bad_config_path_is_reported_rather_than_raised(tmp_path: Path, capsys) -> None:
    assert main(["check-config", "--sources-config", str(tmp_path / "nope.yaml")]) == 1
    assert "no such config file" in capsys.readouterr().err


def test_status_on_an_empty_archive_says_so(tmp_path: Path, capsys) -> None:
    assert main(["status", "--data-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "nothing archived yet" in out
    assert "processed tables: none" in out


def test_status_reports_what_is_archived_and_unconfigured(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("pipeline.config.load_dotenv", lambda *a, **k: [])
    monkeypatch.delenv("NASS_API_KEY", raising=False)
    from pipeline.storage import RawStore

    RawStore(tmp_path).save("usdm", "IA/2012", b"[]", url="https://example.test/x",
                            publication_date=date(2012, 12, 27))
    main(["status", "--data-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "usdm" in out and "1 keys" in out
    assert "newest publication 2012-12-27" in out
    assert "unconfigured secrets: NASS_API_KEY" in out
