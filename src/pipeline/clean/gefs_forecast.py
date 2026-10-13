"""Decode archived operational GEFS messages at the configured corn points."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ..calendar import PUBLICATION_COLUMN
from ..cli import CleanRequest
from ..gefs_archive import GefsArchiveError, decode_points
from ._common import archived

TABLE = "gefs_corn_point_forecast"
DECODE_WORKERS = 8


def clean(request: CleanRequest) -> list[str]:
    corn = request.geography.crop("corn")
    if not corn.points:
        raise SystemExit(
            "gefs_forecast: corn has no configured points in config/geography.yaml"
        )

    frames: list[pd.DataFrame] = []
    failures: list[str] = []

    def decode_record(item):
        record, body = item
        timestamp_status = record.params.get("archive_timestamp_status")
        if timestamp_status != "plausible_operational_upload":
            return None, (
                f"{record.key}: archive timestamp is {timestamp_status or 'missing'}"
            )
        try:
            frame = decode_points(
                body, corn.points, statistic=str(record.params["statistic"])
            )
        except (GefsArchiveError, KeyError) as exc:
            return None, f"{record.key}: {exc}"
        expected_lead = int(record.params["lead_hours"])
        if set(frame["lead_hours"]) != {expected_lead}:
            return None, (
                f"{record.key}: manifest says f{expected_lead:03d}, decoded "
                f"{sorted(frame['lead_hours'].unique())}"
            )
        available_at = pd.Timestamp(record.params["model_available_at"])
        if available_at.tzinfo is None:
            return None, f"{record.key}: model_available_at is not timezone-aware"
        archive_modified = pd.to_datetime(
            record.params.get("archive_last_modified"), utc=True
        )
        if pd.isna(archive_modified) or archive_modified > available_at:
            return None, (
                f"{record.key}: archive object was not verifiably present by "
                f"{available_at.isoformat()}"
            )
        frame["model_available_at"] = available_at
        frame[PUBLICATION_COLUMN] = available_at.normalize().tz_localize(None)
        frame["archive_last_modified"] = archive_modified
        frame["archive_timestamp_status"] = timestamp_status
        frame["source_sha256"] = record.sha256
        frame["source_key"] = record.key
        return frame, None

    source = archived(request.raw, request.spec.name)
    with ThreadPoolExecutor(max_workers=DECODE_WORKERS) as executor:
        decoded = executor.map(decode_record, source, buffersize=DECODE_WORKERS * 2)
        for frame, failure in decoded:
            if failure is not None:
                failures.append(failure)
            elif frame is not None:
                frames.append(frame)

    if not frames:
        detail = f" First failure: {failures[0]}" if failures else ""
        raise SystemExit(
            "gefs_forecast: no decodable archive -- run `python -m pipeline "
            "gefs-download --date YYYY-MM-DD --leads 6 12 18 24` first."
            + detail
        )

    table = pd.concat(frames, ignore_index=True)
    identity = ["initialized_at", "statistic", "lead_hours", "variable", "point"]
    table = (table.sort_values(identity + ["source_sha256"])
             .drop_duplicates(identity, keep="last")
             .sort_values(identity, ignore_index=True))
    maximum_distance = float(table["grid_distance_km"].max())
    print(
        f"  {len(table)} point-fields from {table['initialized_at'].nunique()} run(s), "
        f"{table['lead_hours'].nunique()} lead(s), "
        f"{', '.join(sorted(table['variable'].unique()))}"
    )
    print(
        f"    {table['point'].nunique()} corn points; farthest grid match "
        f"{maximum_distance:.1f} km; {len(failures)} excluded file(s)"
    )
    if failures:
        print(f"    first exclusion: {failures[0]}")
    if request.dry_run:
        print(f"  would write {request.processed.path(TABLE)}")
        return []
    return [str(request.processed.write(TABLE, table))]
