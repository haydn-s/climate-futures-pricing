"""Offline contract tests for the operational GEFS archive feasibility layer."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from pipeline import gefs_archive as gefs


INDEX = b"""\
63:9905757:d=2026100100:TMP:2 m above ground:24 hour fcst:ens mean
64:10131425:d=2026100100:RH:2 m above ground:24 hour fcst:ens mean
65:10323685:d=2026100100:TMAX:2 m above ground:18-24 hour max fcst:ens mean
66:10454630:d=2026100100:TMIN:2 m above ground:18-24 hour min fcst:ens mean
67:10681869:d=2026100100:UGRD:10 m above ground:24 hour fcst:ens mean
68:10924406:d=2026100100:VGRD:10 m above ground:24 hour fcst:ens mean
69:11165570:d=2026100100:APCP:surface:18-24 hour acc fcst:ens mean
70:11254644:d=2026100100:CSNOW:surface:18-24 hour ave fcst:ens mean
"""


def test_url_is_an_operational_cycle_not_a_reforecast(sources) -> None:
    spec = sources["gefs_forecast"]
    run = datetime(2026, 10, 1, 0, tzinfo=timezone.utc)
    index = gefs.index_url(spec, run, "geavg", 24)
    assert index.endswith(
        "/gefs.20261001/00/atmos/pgrb2ap5/geavg.t00z.pgrb2a.0p50.f024.idx"
    )
    assert gefs.data_url(index).endswith(".f024")


def test_pre_v12_and_invalid_leads_are_rejected(sources) -> None:
    spec = sources["gefs_forecast"]
    with pytest.raises(gefs.GefsArchiveError, match="predates operational GEFS v12"):
        gefs.index_url(
            spec, datetime(2020, 9, 23, 6, tzinfo=timezone.utc), "geavg", 24
        )
    with pytest.raises(gefs.GefsArchiveError, match="6-hour step"):
        gefs.index_url(
            spec, datetime(2026, 10, 1, 0, tzinfo=timezone.utc), "geavg", 25
        )


def test_index_offsets_define_exact_message_sizes() -> None:
    messages = gefs.parse_index(INDEX)
    assert len(messages) == 8
    assert messages[0].initialized_at == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert messages[2].variable == "TMAX"
    assert messages[2].bytes == 10454630 - 10323685
    assert messages[-1].end is None


def test_select_requires_every_configured_field(sources) -> None:
    messages = gefs.parse_index(INDEX)
    selected = gefs.select_messages(messages, sources["gefs_forecast"].params["selectors"])
    assert [item.variable for item in selected] == ["TMAX", "TMIN", "APCP"]
    with pytest.raises(gefs.GefsArchiveError, match="missing configured fields"):
        gefs.select_messages(messages, [{"variable": "SNOD", "level": "surface"}])


def test_nearby_ranges_are_coalesced_and_account_for_transfer_overhead(sources) -> None:
    messages = gefs.parse_index(INDEX)
    selected = gefs.select_messages(messages, sources["gefs_forecast"].params["selectors"])
    ranges = gefs.coalesce_ranges(selected, max_gap_bytes=600000)
    assert len(ranges) == 1
    assert ranges[0].header == "bytes=10323685-11254643"
    selected_bytes = sum(int(item.bytes) for item in selected)
    assert ranges[0].bytes > selected_bytes


def test_a_final_message_cannot_be_ranged_without_object_size() -> None:
    messages = gefs.parse_index(INDEX)
    with pytest.raises(gefs.GefsArchiveError, match="object size"):
        gefs.coalesce_ranges([messages[-1]])


def test_download_uses_audited_ranges_and_builds_a_multimessage_stream(
        sources, monkeypatch) -> None:
    headers = {"last-modified": "Thu, 01 Oct 2026 03:42:00 GMT"}
    monkeypatch.setattr(gefs, "fetch_with_headers", lambda *a, **k: (INDEX, headers))
    audit = gefs.audit_index(
        sources["gefs_forecast"], datetime(2026, 10, 1, tzinfo=timezone.utc),
        "geavg", 24,
    )
    seen = []

    def ranged(url, start, end, **kwargs):
        seen.append((url, start, end))
        size = end - start + 1
        return b"GRIB" + b"x" * (size - 8) + b"7777", {"etag": "one"}

    monkeypatch.setattr(gefs, "fetch_range", ranged)
    body, returned_headers = gefs.download_grib(audit)
    assert body.startswith(b"GRIB") and body.endswith(b"7777")
    assert len(body) == audit.transfer_bytes
    assert len(seen) == len(audit.ranges)
    assert returned_headers == ({"etag": "one"},)


def test_download_rejects_ranges_that_cut_through_grib_messages(sources, monkeypatch) -> None:
    monkeypatch.setattr(gefs, "fetch_with_headers", lambda *a, **k: (INDEX, {}))
    audit = gefs.audit_index(
        sources["gefs_forecast"], datetime(2026, 10, 1, tzinfo=timezone.utc),
        "geavg", 24,
    )
    monkeypatch.setattr(
        gefs, "fetch_range", lambda *a, **k: (b"not a grib message", {})
    )
    with pytest.raises(gefs.GefsArchiveError, match="GRIB boundaries"):
        gefs.download_grib(audit)


class FakeCodes:
    def __init__(self, messages):
        self.messages = list(messages)
        self.index = 0
        self.released = []

    def codes_new_from_message(self, body):
        if self.index == len(self.messages):
            return None
        handle = self.messages[self.index]
        self.index += 1
        return handle

    @staticmethod
    def codes_get(handle, key):
        return handle[key]

    @staticmethod
    def codes_grib_find_nearest(handle, lat, lon, npoints=1):
        return (SimpleNamespace(
            lat=round(lat * 2) / 2,
            lon=(round(lon * 2) / 2) % 360,
            value=handle["value"],
            distance=12.0,
            index=1,
        ),)

    def codes_release(self, handle):
        self.released.append(handle)


def grib_message(short_name, units, value):
    return {
        "totalLength": 1,
        "shortName": short_name,
        "units": units,
        "dataDate": 20240715,
        "dataTime": 0,
        "validityDate": 20240716,
        "validityTime": 0,
        "startStep": 18,
        "endStep": 24,
        "value": value,
    }


def test_decoder_filters_coalescing_fields_and_standardizes_units(geography) -> None:
    messages = [
        grib_message("tmax", "K", 303.15),
        grib_message("tmin", "K", 293.15),
        grib_message("10u", "m s**-1", 4.0),
        grib_message("10v", "m s**-1", 2.0),
        grib_message("tp", "kg m**-2", 12.5),
    ]
    codes = FakeCodes(messages)
    points = geography.crop("corn").points[:2]
    table = gefs.decode_points(
        b"x" * len(messages), points, statistic="geavg", codes_module=codes
    )
    assert len(table) == 6
    assert set(table["variable"]) == {"tmax_c", "tmin_c", "precip_mm"}
    assert table.query("variable == 'tmax_c'")["value"].tolist() == pytest.approx([30, 30])
    assert table.query("variable == 'tmin_c'")["value"].tolist() == pytest.approx([20, 20])
    assert table.query("variable == 'precip_mm'")["value"].tolist() == [12.5, 12.5]
    assert table["grid_lon"].between(-180, 180).all()
    assert len(codes.released) == len(messages)


def test_temperature_spread_is_not_shifted_by_kelvin_offset(geography) -> None:
    codes = FakeCodes([
        grib_message("tmax", "K", 2.5),
        grib_message("tmin", "K", 1.5),
        grib_message("tp", "kg m**-2", 4.0),
    ])
    table = gefs.decode_points(
        b"xxx", geography.crop("corn").points[:1],
        statistic="gespr", codes_module=codes,
    )
    assert table.query("variable == 'tmax_c'")["value"].iloc[0] == 2.5


def test_decoder_refuses_a_selection_missing_a_target_field(geography) -> None:
    codes = FakeCodes([
        grib_message("tmax", "K", 303.15),
        grib_message("tmin", "K", 293.15),
    ])
    with pytest.raises(gefs.GefsArchiveError, match="missing decoded fields: tp"):
        gefs.decode_points(
            b"xx", geography.crop("corn").points[:1],
            statistic="geavg", codes_module=codes,
        )


def test_decoder_refuses_a_non_six_hour_target_interval(geography) -> None:
    messages = [
        grib_message("tmax", "K", 303.15),
        grib_message("tmin", "K", 293.15),
        grib_message("tp", "kg m**-2", 2.0),
    ]
    messages[0]["startStep"] = 12
    codes = FakeCodes(messages)
    with pytest.raises(gefs.GefsArchiveError, match="required six-hour interval"):
        gefs.decode_points(
            b"xxx", geography.crop("corn").points[:1],
            statistic="geavg", codes_module=codes,
        )


def test_audit_reports_source_publication_time_and_full_run_estimate(
        sources, monkeypatch) -> None:
    headers = {"last-modified": "Thu, 01 Oct 2026 03:42:00 GMT"}
    monkeypatch.setattr(gefs, "fetch_with_headers", lambda *a, **k: (INDEX, headers))
    report = gefs.run_audit(
        sources["gefs_forecast"], date(2026, 10, 1), 0, [24], ["geavg", "gespr"]
    )
    assert report["sample_indexes"] == 2
    assert report["latest_sample_last_modified"] == "2026-10-01T03:42:00+00:00"
    assert report["full_leads_per_statistic"] == 60
    assert report["estimate_through_hour"] == 360
    assert report["estimated_transfer_bytes_per_run"] == (
        report["sample_transfer_bytes"] * 60
    )
    assert report["estimated_selected_bytes_per_run"] == (
        report["sample_selected_bytes"] * 60
    )
    assert report["archive_timestamp_status_counts"] == {
        "plausible_operational_upload": 2
    }
    assert report["conservative_availability_hours"] == 7


def test_a_same_day_upload_after_the_cutoff_is_late(sources, monkeypatch) -> None:
    headers = {"last-modified": "Thu, 01 Oct 2026 09:00:00 GMT"}
    monkeypatch.setattr(gefs, "fetch_with_headers", lambda *a, **k: (INDEX, headers))
    audit = gefs.audit_index(
        sources["gefs_forecast"], datetime(2026, 10, 1, tzinfo=timezone.utc),
        "geavg", 24,
    )
    assert audit.archive_timestamp_status == "late_operational_upload"


def test_audit_can_estimate_a_bounded_first_stage(sources, monkeypatch) -> None:
    monkeypatch.setattr(gefs, "fetch_with_headers", lambda *a, **k: (INDEX, {}))
    report = gefs.run_audit(
        sources["gefs_forecast"], date(2026, 10, 1), 0, [24], ["geavg"],
        through_hour=240,
    )
    assert report["full_leads_per_statistic"] == 40
    assert report["estimate_through_hour"] == 240
    with pytest.raises(gefs.GefsArchiveError, match="estimate horizon"):
        gefs.run_audit(
            sources["gefs_forecast"], date(2026, 10, 1), 0, [24], ["geavg"],
            through_hour=241,
        )
