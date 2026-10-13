"""Decode archived operational GEFS messages at the configured corn points."""

from __future__ import annotations

import pandas as pd

from ..calendar import PUBLICATION_COLUMN
from ..cli import CleanRequest
from ..gefs_archive import GefsArchiveError, decode_points
from ._common import archived

TABLE = "gefs_corn_point_forecast"


def clean(request: CleanRequest) -> list[str]:
    corn = request.geography.crop("corn")
    if not corn.points:
        raise SystemExit(
            "gefs_forecast: corn has no configured points in config/geography.yaml"
        )

    frames: list[pd.DataFrame] = []
    failures: list[str] = []
    for record, body in archived(request.raw, request.spec.name):
        timestamp_status = record.params.get("archive_timestamp_status")
        if timestamp_status != "plausible_operational_upload":
            failures.append(
                f"{record.key}: archive timestamp is {timestamp_status or 'missing'}"
            )
            continue
        try:
            frame = decode_points(
                body, corn.points, statistic=str(record.params["statistic"])
            )
        except (GefsArchiveError, KeyError) as exc:
            failures.append(f"{record.key}: {exc}")
            continue
        expected_lead = int(record.params["lead_hours"])
        if set(frame["lead_hours"]) != {expected_lead}:
            failures.append(
                f"{record.key}: manifest says f{expected_lead:03d}, decoded "
                f"{sorted(frame['lead_hours'].unique())}"
            )
            continue
        available_at = pd.Timestamp(record.params["model_available_at"])
        if available_at.tzinfo is None:
            failures.append(f"{record.key}: model_available_at is not timezone-aware")
            continue
        archive_modified = pd.to_datetime(
            record.params.get("archive_last_modified"), utc=True
        )
        if pd.isna(archive_modified) or archive_modified > available_at:
            failures.append(
                f"{record.key}: archive object was not verifiably present by "
                f"{available_at.isoformat()}"
            )
            continue
        frame["model_available_at"] = available_at
        frame[PUBLICATION_COLUMN] = available_at.normalize().tz_localize(None)
        frame["archive_last_modified"] = archive_modified
        frame["archive_timestamp_status"] = timestamp_status
        frame["source_sha256"] = record.sha256
        frame["source_key"] = record.key
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
