"""The raw archive: round trip, revision accumulation, and what the manifest holds.

The point-in-time claim this project makes only survives if a re-fetch can never
quietly replace an earlier one, so the central tests here save the same key twice
with different bytes and assert the first copy is still on disk, unmodified, with
both versions in the manifest.

Real bytes are used where a fixture exists (an nClimGrid month), so the archive is
exercised on a file with the quirks it will meet in production rather than on a
convenient short string. Nothing here touches the network.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from pipeline.storage import ProcessedStore, RawStore, sha256_hex

FIXTURE = "nclimgrid/tmax-201207-cty-scaled.csv"
URL = ("https://www.ncei.noaa.gov/data/nclimgrid-daily/access/averages/2012/"
       "tmax-201207-cty-scaled.csv")


class Clock:
    """An injected clock, so fetched_at is asserted rather than hoped for."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(hours=1)
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 9, 16, 11, 0, tzinfo=UTC))


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> RawStore:
    return RawStore(tmp_path / "data", clock=clock)


def manifest_lines(store: RawStore) -> list[dict]:
    return [json.loads(line) for line in store.manifest_path.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------- round trip


def test_save_writes_a_content_addressed_file_and_one_manifest_line(
    store: RawStore, fixtures: Path
) -> None:
    body = (fixtures / FIXTURE).read_bytes()
    record = store.save("nclimgrid", "tmax-201207", body, url=URL,
                        params={"var": "tmax", "status": "scaled"},
                        publication_date=date(2022, 9, 1),
                        version_stamp="complete 2012-07-01..2012-07-31")

    digest = sha256_hex(body)
    assert record.path == store.root / "raw" / "nclimgrid" / "tmax-201207" / f"{digest[:8]}.csv"
    assert record.path.read_bytes() == body
    assert (record.sha256, record.bytes) == (digest, len(body))

    lines = manifest_lines(store)
    assert len(lines) == 1
    assert lines[0]["path"] == f"raw/nclimgrid/tmax-201207/{digest[:8]}.csv"  # relative: survives a move
    assert lines[0]["publication_date"] == "2022-09-01"
    assert lines[0]["params"] == {"var": "tmax", "status": "scaled"}


def test_fetched_at_is_timezone_aware_utc_and_separate_from_publication(store: RawStore) -> None:
    record = store.save("usdm", "IA-2012-07", b"[]", url="https://example.test/usdm.json",
                        publication_date=date(2012, 7, 19))
    assert record.fetched_at.tzinfo is not None
    assert record.fetched_at.utcoffset() == timedelta(0)
    # Ours and theirs are different fields, and neither can stand in for the other.
    assert record.fetched_at.date() != record.publication_date


def test_a_source_without_a_publication_date_records_none_rather_than_a_guess(store: RawStore) -> None:
    record = store.save("owid_yields", "maize-yields", b"entity,code,year\n",
                        url="https://ourworldindata.org/grapher/maize-yields.csv")
    assert record.publication_date is None
    assert manifest_lines(store)[0]["publication_date"] is None


def test_records_round_trip_through_the_manifest(store: RawStore, fixtures: Path) -> None:
    body = (fixtures / FIXTURE).read_bytes()
    saved = store.save("nclimgrid", "tmax-201207", body, url=URL,
                       params={"status": "scaled"}, publication_date=date(2022, 9, 1),
                       version_stamp="ghcn 3.34")
    loaded = store.latest("nclimgrid", "tmax-201207")
    assert loaded == saved


# --------------------------------------------------------------- revisions


def test_changed_content_adds_a_version_and_leaves_the_old_numbers_alone(store: RawStore) -> None:
    first = store.save("nclimgrid", "tmax-202609", b"first-vintage", url=URL)
    second = store.save("nclimgrid", "tmax-202609", b"revised-vintage", url=URL)

    assert first.path != second.path
    assert first.path.read_bytes() == b"first-vintage"   # never overwritten
    assert second.path.read_bytes() == b"revised-vintage"
    assert len(list(first.path.parent.iterdir())) == 2

    versions = store.versions("nclimgrid", "tmax-202609")
    assert [record.sha256 for record in versions] == [first.sha256, second.sha256]
    assert store.latest("nclimgrid", "tmax-202609").sha256 == second.sha256
    assert len(manifest_lines(store)) == 2


def test_identical_content_is_one_version_but_still_a_recorded_fetch(store: RawStore) -> None:
    # NOAA rebuilds prelim files daily, so "the same bytes again today" is a real
    # observation worth keeping -- but it is not a new version of the data.
    first = store.save("nclimgrid", "tmax-202609", b"prelim", url=URL)
    again = store.save("nclimgrid", "tmax-202609", b"prelim", url=URL)

    assert again.path == first.path
    assert len(list(first.path.parent.iterdir())) == 1
    assert len(store.versions("nclimgrid", "tmax-202609")) == 1
    assert len(store.history("nclimgrid", "tmax-202609")) == 2
    assert store.latest("nclimgrid", "tmax-202609").fetched_at == again.fetched_at


def test_a_reverted_source_is_reported_as_currently_serving_the_old_bytes(store: RawStore) -> None:
    store.save("usdm", "IA", b"v1", url="https://example.test/u.json")
    store.save("usdm", "IA", b"v2", url="https://example.test/u.json")
    reverted = store.save("usdm", "IA", b"v1", url="https://example.test/u.json")

    assert len(store.versions("usdm", "IA")) == 2            # two distinct contents ever seen
    assert len(store.history("usdm", "IA")) == 3             # three fetches
    assert store.latest("usdm", "IA").sha256 == reverted.sha256


def test_has_and_keys_report_what_is_archived(store: RawStore) -> None:
    assert not store.has("usdm", "IA")
    store.save("usdm", "IA", b"[]", url="https://example.test/u.json")
    store.save("usdm", "IL", b"[]", url="https://example.test/u.json")
    assert store.has("usdm", "IA") and not store.has("usdm", "NE")
    assert store.keys("usdm") == ["IA", "IL"]
    assert store.sources() == ["usdm"]


def test_a_nested_key_becomes_nested_directories(store: RawStore) -> None:
    record = store.save("usdm", "IA/2012", b"[]", url="https://example.test/u.json")
    assert record.path.parent == store.root / "raw" / "usdm" / "IA" / "2012"
    assert store.keys("usdm") == ["IA/2012"]


def test_a_key_cannot_escape_the_store(store: RawStore) -> None:
    for bad in ("../evil", "/absolute", "", "has space", "..", "a//b"):
        with pytest.raises(ValueError, match="not usable as a path"):
            store.save("usdm", bad, b"x", url="https://example.test/u.json")


# --------------------------------------------------------------- secrets


def test_an_api_key_in_the_url_never_reaches_the_record_or_the_manifest(store: RawStore) -> None:
    secret = "abcdef12-3456-7890-abcd-ef1234567890"
    url = f"https://quickstats.nass.usda.gov/api/api_GET/?key={secret}&commodity_desc=CORN"
    record = store.save("nass_production", "corn-IA-2012", b"{}", url=url)

    assert secret not in record.url
    assert "key=REDACTED" in record.url
    assert "commodity_desc=CORN" in record.url
    assert secret not in store.manifest_path.read_text()


# --------------------------------------------------------------- manifest integrity


def test_a_corrupt_manifest_line_stops_the_read_instead_of_being_skipped(store: RawStore) -> None:
    store.save("usdm", "IA", b"[]", url="https://example.test/u.json")
    with open(store.manifest_path, "a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    with pytest.raises(ValueError, match="malformed manifest line"):
        store.history("usdm")


def test_an_absent_manifest_reads_as_empty(tmp_path: Path) -> None:
    empty = RawStore(tmp_path / "nothing-here")
    assert empty.history() == [] and empty.latest("usdm", "IA") is None


def test_a_save_after_a_read_is_visible(store: RawStore) -> None:
    # The manifest is memoised so a long fetch can check latest() per file; a save
    # has to extend that cache rather than be hidden by it.
    assert store.latest("usdm", "IA") is None
    first = store.save("usdm", "IA", b"v1", url="https://example.test/u.json")
    assert store.latest("usdm", "IA") == first
    second = store.save("usdm", "IA", b"v2", url="https://example.test/u.json")
    assert store.latest("usdm", "IA") == second
    assert len(store.history()) == 2


def test_a_second_store_sees_what_the_first_wrote(tmp_path: Path) -> None:
    root = tmp_path / "data"
    RawStore(root).save("usdm", "IA", b"v1", url="https://example.test/u.json")
    assert len(RawStore(root).history("usdm", "IA")) == 1


# --------------------------------------------------------------- processed tables


def test_processed_tables_round_trip_with_their_index(tmp_path: Path) -> None:
    processed = ProcessedStore(tmp_path / "data")
    frame = pd.DataFrame(
        {"d2": [0.0, 100.0], "publication_date": [date(2012, 7, 19), date(2012, 7, 26)]},
        index=pd.DatetimeIndex(["2012-07-17", "2012-07-24"], name="map_date"),
    )
    path = processed.write("usdm_county_weekly", frame)
    assert path == tmp_path / "data" / "processed" / "usdm_county_weekly.parquet"

    back = processed.read("usdm_county_weekly")
    assert back.index.name == "map_date"
    assert list(back["d2"]) == [0.0, 100.0]
    assert processed.names() == ["usdm_county_weekly"]


def test_processed_write_replaces_rather_than_versions(tmp_path: Path) -> None:
    # Processed tables are derived and re-derivable, unlike the raw archive.
    processed = ProcessedStore(tmp_path / "data")
    processed.write("demo", pd.DataFrame({"a": [1]}))
    processed.write("demo", pd.DataFrame({"a": [1, 2]}))
    assert len(processed.read("demo")) == 2
    assert len(list((tmp_path / "data" / "processed").iterdir())) == 1


def test_reading_a_missing_table_lists_what_exists(tmp_path: Path) -> None:
    processed = ProcessedStore(tmp_path / "data")
    processed.write("corn_weather_daily", pd.DataFrame({"a": [1]}))
    with pytest.raises(FileNotFoundError, match="corn_weather_daily"):
        processed.read("cocoa_weather_daily")
