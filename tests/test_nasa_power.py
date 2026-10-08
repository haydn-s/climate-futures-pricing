"""NASA POWER: the tail is padded, not truncated, and header.end does not say so.

This source is the only one that can serve a crop with no US county geography,
which is what cocoa is. Its two dangerous properties both concern the edge of the
record: a window running past the available data comes back FILLED with -999.0
rather than shortened, and `header.end` is clamped to the server's today rather
than to the last day that has a value -- so it overstates coverage by exactly the
latency. Only the last non-fill value is authoritative, and that is what most of
this file is about.

The second theme is the publication date. This endpoint sends no Last-Modified, no
ETag and no version document, so the lag in config/sources.yaml is an assumption
rather than an observation of any one record. It is asserted here so that changing
it is a deliberate act with a failing test attached.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pipeline.calendar import PUBLICATION_COLUMN, as_of
from pipeline.clean import nasa_power as clean_power
from pipeline.clients import nasa_power as client
from pipeline.ingest import nasa_power as ingest_power


@pytest.fixture
def power(fixtures: Path) -> Path:
    return fixtures / "power"


@pytest.fixture
def soubre(power: Path) -> bytes:
    return (power / "point_soubre_2012-07_verbatim.json").read_bytes()


# ----------------------------------------------------------------------- the URL


def test_the_url_sends_yyyymmdd_integers(sources) -> None:
    url = client.url(sources["nasa_power"], lat=5.8, lon=-6.6,
                     start=date(2012, 7, 1), end=date(2012, 7, 31))
    assert "start=20120701" in url and "end=20120731" in url
    assert "latitude=5.8" in url and "longitude=-6.6" in url


def test_a_window_before_the_record_is_refused_without_a_request(sources) -> None:
    # The 422 body carries the useful message, and pipeline.http does not keep
    # error bodies -- so this is checked here, where the record's own bound is known.
    with pytest.raises(client.PowerResponseError, match="starts 1981-01-01"):
        client.url(sources["nasa_power"], lat=42.0, lon=-93.6,
                   start=date(1970, 1, 1), end=date(1970, 2, 1))


@pytest.mark.parametrize("lat,lon", [(99.0, -93.6), (42.0, 200.0), (-91.0, 0.0)])
def test_an_impossible_coordinate_is_refused(sources, lat, lon) -> None:
    with pytest.raises(client.PowerResponseError, match="latitude/longitude"):
        client.url(sources["nasa_power"], lat=lat, lon=lon,
                   start=date(2012, 7, 1), end=date(2012, 7, 31))


def test_a_reversed_window_is_refused(sources) -> None:
    with pytest.raises(client.PowerResponseError, match="ends before it starts"):
        client.url(sources["nasa_power"], lat=5.8, lon=-6.6,
                   start=date(2012, 7, 31), end=date(2012, 7, 1))


# ----------------------------------------------------------------------- parsing


def test_a_complete_month_parses_with_its_units_and_vintage(soubre) -> None:
    answer = client.parse(soubre, url="fixture")
    assert answer.last_observed == date(2012, 7, 31)
    assert answer.fill_value == -999.0
    assert answer.time_standard == "LST"  # local solar time, not UTC
    assert answer.units == {"T2M_MAX": "C", "T2M_MIN": "C", "PRECTOTCORR": "mm/day"}
    assert len(answer.series["T2M_MAX"]) == 31
    assert all(value is not None for value in answer.series["T2M_MAX"].values())


def test_the_coordinates_are_unpacked_longitude_first(soubre) -> None:
    # GeoJSON order. Read as (lat, lon), Soubre's [-6.6, 5.8, 195.92] is in the
    # Atlantic rather than in Cote d'Ivoire.
    answer = client.parse(soubre, url="fixture")
    assert (answer.latitude, answer.longitude) == (5.8, -6.6)
    assert answer.elevation == pytest.approx(195.92)


def test_the_padded_tail_is_masked_and_coverage_stops_where_the_data_does(power: Path) -> None:
    answer = client.parse((power / "point_iowa_current_padded_tail.json").read_bytes(), url="fixture")
    # Requested through 2026-10-31; header.end is the server's today, not the
    # requested end and not the data's end -- it overstates coverage by the
    # latency, which is the whole trap. Re-captured when T2M_MIN was added to the
    # parameter list, so the window moved; the shape of the trap did not.
    assert answer.header_end == "20261008"
    assert len(answer.series["T2M_MAX"]) == 8
    assert answer.last_observed == date(2026, 10, 4), "four days of padding"
    filled = [day for day, value in answer.series["T2M_MAX"].items() if value is None]
    assert filled == [date(2026, 10, d) for d in range(5, 9)]
    # The sentinel is gone, so nothing downstream can average it by accident.
    assert -999.0 not in set(answer.series["T2M_MAX"].values())


def test_coverage_is_the_earliest_last_day_across_variables(soubre) -> None:
    # The minimum, not the maximum: a file is knowable only up to the point where
    # all of it is there, and the tail is padded rather than absent.
    answer = client.parse(soubre, url="fixture")
    trimmed = dict(answer.series)
    trimmed["PRECTOTCORR"] = {day: (None if day.day > 20 else value)
                              for day, value in trimmed["PRECTOTCORR"].items()}
    assert client._last_observed(trimmed) == date(2012, 7, 20)


def test_the_reanalysis_behind_the_series_changes_with_the_era(power: Path) -> None:
    old = client.parse((power / "point_iowa_1981-01_merra2.json").read_bytes())
    new = client.parse((power / "point_iowa_current_padded_tail.json").read_bytes())
    assert "MERRA2" in old.sources and "GEOSIT" in new.sources
    assert old.stamp != new.stamp


def test_the_sources_list_is_sorted_so_the_vintage_stamp_cannot_flap(power: Path) -> None:
    # Two otherwise identical requests return the array in different orders, which
    # would make a stamp comparison report a reprocessing that did not happen.
    answer = client.parse((power / "point_iowa_current_padded_tail.json").read_bytes())
    assert list(answer.sources) == sorted(answer.sources)


@pytest.mark.parametrize("name,expected", [
    ("error_422_before_1981.json", "1981/01/01"),
    ("error_422_bad_parameter.json", "T2M_MAXX"),
    ("error_422_dashed_dates.json", "start"),
])
def test_both_error_shapes_raise_with_their_own_message(power: Path, name, expected) -> None:
    with pytest.raises(client.PowerResponseError) as caught:
        client.parse((power / name).read_bytes(), url="fixture")
    assert expected in str(caught.value)


def test_a_prose_header_is_not_mistaken_for_a_successful_one(power: Path) -> None:
    # On 200 `header` is an object; on POWER's own 422 it is a string. Anything
    # that reaches for header["end"] without checking indexes a string instead.
    import json
    payload = json.loads((power / "error_422_before_1981.json").read_bytes())
    assert isinstance(payload["header"], str)
    with pytest.raises(client.PowerResponseError, match="POWER refused"):
        client.parse((power / "error_422_before_1981.json").read_bytes())


def test_a_response_with_no_series_is_refused() -> None:
    with pytest.raises(client.PowerResponseError, match="properties.parameter"):
        client.parse(b'{"header": {"fill_value": -999.0}}')


# ----------------------------------------------------------------------- ingest


def test_a_dry_run_plans_a_key_per_point_year(harness, no_network) -> None:
    records = ingest_power.fetch(harness.fetch(
        "nasa_power", years=(2012, 2013), dry_run=True, today=date(2026, 9, 27)))
    # One key per point-year, every crop that declares points, in config order.
    # Derived rather than frozen: this list grew when corn gained points and again
    # when soybeans did, and each time a literal would have failed for the wrong
    # reason. What is under test is the key SHAPE and the ordering, not the roster.
    from pipeline.ingest._common import slug
    expected = [f"{crop.name}/{slug(point.name)}/{year}"
                for crop in harness.geography for point in crop.points
                for year in (2012, 2013)]
    assert expected, "the shipped geography declares no points at all"
    assert [r.key for r in records] == expected
    assert not harness.raw.manifest_path.exists()


def test_a_point_name_becomes_a_stable_store_key() -> None:
    from pipeline.ingest._common import slug
    assert slug("Soubre") == "soubre"
    assert slug("San José") == "san-jose"
    # The property that matters: diacritics and apostrophes are not usable as path
    # components, and the two spellings of the same country must not produce two
    # different keys for the same point.
    assert slug("Côte d'Ivoire") == slug("Cote d'Ivoire") == "cote-d-ivoire"
    with pytest.raises(ValueError):
        slug("...")


def test_the_publication_date_is_the_last_real_day_plus_the_configured_lag(
        harness, power: Path) -> None:
    request = harness.fetch("nasa_power", today=date(2026, 9, 27))
    assert request.spec.publication_lag_days == 5

    complete = client.parse((power / "point_soubre_2012-07_verbatim.json").read_bytes())
    assert ingest_power._published(request, complete) == date(2012, 8, 5)

    padded = client.parse((power / "point_iowa_current_padded_tail.json").read_bytes())
    # Last real day is 10-04, NOT header.end of 10-08.
    assert ingest_power._published(request, padded) == date(2026, 10, 9)


def test_a_year_is_complete_only_when_real_values_reach_its_last_day(harness, power: Path) -> None:
    crop = harness.geography.crop("cocoa")
    point = crop.points[0]
    padded = client.parse((power / "point_iowa_current_padded_tail.json").read_bytes())
    params = ingest_power._params(crop, point, padded, date(2026, 1, 1), date(2026, 12, 31))
    assert params["complete"] is False
    assert params["last_observed"] == "2026-10-04"
    # The readable name travels in params; the key carries the slug.
    assert params["point"] == "Soubre" and params["country"] == "Cote d'Ivoire"

    whole = client.parse((power / "point_soubre_2012-07_verbatim.json").read_bytes())
    assert ingest_power._params(crop, point, whole, date(2012, 1, 1),
                               date(2012, 12, 31))["complete"] is False
    # ... because July is not December: completeness is about the year's last day.


def test_a_crop_with_no_points_is_reported_rather_than_silently_skipped(harness, no_network,
                                                                       monkeypatch) -> None:
    import dataclasses

    from pipeline.config import Geography
    # Built point-less here rather than borrowing whichever crop happens to
    # declare no points: corn used to be that crop and no longer is, which broke
    # this guard without breaking the behaviour it protects.
    pointless = dataclasses.replace(harness.geography.crop("corn"), points=())
    assert not pointless.points
    request = harness.fetch("nasa_power", dry_run=True,
                            geography=Geography(crops={"corn": pointless}))
    with pytest.raises(SystemExit, match="no crop .* declares points"):
        ingest_power.fetch(request)


# ------------------------------------------------------------------------ clean


def seed(harness, power: Path, name: str, key: str, **params) -> None:
    harness.seed("nasa_power", key, (power / name).read_bytes(),
                 params={"crop": "cocoa", "point": "Soubre", "country": "Cote d'Ivoire", **params})


def test_clean_builds_a_point_day_table_with_units_in_the_names(harness, power: Path) -> None:
    seed(harness, power, "point_soubre_2012-07_verbatim.json", "cocoa/soubre/2012")
    clean_power.clean(harness.clean("nasa_power"))
    table = harness.processed.read(clean_power.TABLE)

    assert len(table) == 31
    assert {"tmax_c", "tmin_c", "precip_mm"} <= set(table.columns)
    assert (table["lat"] == 5.8).all() and (table["lon"] == -6.6).all()
    assert table["source_model"].iloc[0] == "MERRA2+POWER"


def test_clean_computes_a_publication_date_per_row(harness, power: Path) -> None:
    seed(harness, power, "point_soubre_2012-07_verbatim.json", "cocoa/soubre/2012")
    clean_power.clean(harness.clean("nasa_power"))
    table = harness.processed.read(clean_power.TABLE)
    # Fixed offset, so unlike every other source here the lag is applied per row
    # rather than per file.
    assert (table[PUBLICATION_COLUMN] - table["date"]).dt.days.eq(5).all()


def test_clean_drops_the_padded_tail_rather_than_keeping_future_dated_rows(harness, power: Path) -> None:
    seed(harness, power, "point_iowa_current_padded_tail.json", "cocoa/soubre/2026")
    clean_power.clean(harness.clean("nasa_power"))
    table = harness.processed.read(clean_power.TABLE)
    assert table["date"].max().date() == date(2026, 10, 4)
    assert len(table) == 4
    # No row may carry a publication date for a day that holds no observation.
    assert not table[["tmax_c", "tmin_c", "precip_mm"]].isna().all(axis=1).any()


def test_as_of_hides_what_had_not_been_published(harness, power: Path) -> None:
    seed(harness, power, "point_iowa_current_padded_tail.json", "cocoa/soubre/2026")
    clean_power.clean(harness.clean("nasa_power"))
    table = harness.processed.read(clean_power.TABLE)
    # On 2026-10-08, the 5-day lag means nothing after 10-03 was knowable.
    visible = as_of(table, PUBLICATION_COLUMN, "2026-10-08")
    assert visible["date"].max().date() == date(2026, 10, 3)


def test_clean_names_the_variable_it_could_not_find(harness, power: Path) -> None:
    # Asking for a variable the archived bytes do not hold must name it and say to
    # re-fetch, rather than producing a table with a silently absent column.
    import dataclasses

    from pipeline.cli import CleanRequest

    seed(harness, power, "point_soubre_2012-07_verbatim.json", "cocoa/soubre/2012")
    spec = harness.sources["nasa_power"]
    broken = dataclasses.replace(spec, params={
        **spec.params, "variables": {"tmax_c": "T2M_MAX", "humidity": "RH2M"}})
    request = CleanRequest(spec=broken, geography=harness.geography,
                           raw=harness.raw, processed=harness.processed)
    with pytest.raises(SystemExit, match="RH2M"):
        clean_power.clean(request)


def test_clean_on_an_empty_archive_says_what_to_run(harness) -> None:
    with pytest.raises(SystemExit, match="nothing archived yet"):
        clean_power.clean(harness.clean("nasa_power"))
