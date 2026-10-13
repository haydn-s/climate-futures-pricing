"""Point-in-time admission tests for decoded operational GEFS archives."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from pipeline.clean import gefs_forecast


def _decoded() -> pd.DataFrame:
    initialized = pd.Timestamp("2024-07-15", tz="UTC")
    return pd.DataFrame([{
        "initialized_at": initialized,
        "valid_at": initialized + timedelta(hours=6),
        "lead_hours": 6,
        "statistic": "geavg",
        "variable": "tmax_c",
        "point": "Iowa",
        "point_lat": 42.0,
        "point_lon": -93.0,
        "grid_lat": 42.0,
        "grid_lon": -93.0,
        "grid_distance_km": 1.0,
        "value": 30.0,
        "source_units": "K",
    }])


def test_clean_excludes_archive_republications(harness, monkeypatch, capsys) -> None:
    common = {
        "statistic": "geavg",
        "lead_hours": 6,
        "model_available_at": "2024-07-15T07:00:00+00:00",
        "archive_last_modified": "2024-07-15T04:00:00+00:00",
    }
    harness.seed(
        "gefs_forecast", "20240715/00/geavg/f006", b"valid",
        params={**common, "archive_timestamp_status": "plausible_operational_upload"},
    )
    harness.seed(
        "gefs_forecast", "20240716/00/geavg/f006", b"republished",
        params={**common, "archive_timestamp_status": "archive_republication"},
    )
    harness.seed(
        "gefs_forecast", "20240717/00/geavg/f006", b"late",
        params={**common, "archive_timestamp_status": "late_operational_upload"},
    )
    monkeypatch.setattr(gefs_forecast, "decode_points", lambda *a, **k: _decoded())

    written = gefs_forecast.clean(harness.clean("gefs_forecast"))
    assert len(written) == 1
    result = harness.processed.read(gefs_forecast.TABLE)
    assert len(result) == 1
    assert set(result["archive_timestamp_status"]) == {"plausible_operational_upload"}
    assert "2 excluded file(s)" in capsys.readouterr().out
