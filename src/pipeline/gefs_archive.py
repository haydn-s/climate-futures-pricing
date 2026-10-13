"""Inspect NOAA GEFS operational forecast indexes before a large backfill.

GEFS GRIB2 files are accompanied by a small ``.idx`` file whose rows identify
the byte offset of every message. This module parses that source-owned inventory
and plans exact HTTP ranges for the corn fields in ``config/sources.yaml``. It
does not decode GRIB or write data; its first job is to answer whether the
operational archive is complete, point-in-time, and affordable to retrieve.

The distinction matters. GEFS reforecasts are retrospective model runs. The URLs
here are dated operational cycles, and the response Last-Modified timestamp is
the evidence for when the archived object became available.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, Mapping, Sequence

from .config import SourceSpec
from .http import fetch_range, fetch_with_headers


class GefsArchiveError(ValueError):
    """The archive index or requested GEFS selection is internally inconsistent."""


SHORT_NAME_TO_VARIABLE = {
    "tmax": ("tmax_c", "K"),
    "tmin": ("tmin_c", "K"),
    "tp": ("precip_mm", "kg m**-2"),
}


@dataclass(frozen=True)
class Message:
    number: int
    offset: int
    end: int | None
    initialized_at: datetime
    variable: str
    level: str
    forecast: str
    statistic: str

    @property
    def bytes(self) -> int | None:
        return None if self.end is None else self.end - self.offset + 1


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int
    messages: tuple[Message, ...]

    @property
    def bytes(self) -> int:
        return self.end - self.start + 1

    @property
    def header(self) -> str:
        return f"bytes={self.start}-{self.end}"


@dataclass(frozen=True)
class IndexAudit:
    initialized_at: datetime
    lead_hours: int
    statistic: str
    url: str
    last_modified: datetime | None
    index_messages: int
    selected_messages: int
    selected_bytes: int
    transfer_bytes: int
    archive_delay_hours: float | None
    archive_timestamp_status: str
    ranges: tuple[ByteRange, ...]

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["initialized_at"] = self.initialized_at.isoformat()
        payload["last_modified"] = (
            self.last_modified.isoformat() if self.last_modified else None
        )
        payload["ranges"] = [
            {
                "start": item.start,
                "end": item.end,
                "bytes": item.bytes,
                "header": item.header,
                "fields": [f"{message.variable}:{message.level}" for message in item.messages],
            }
            for item in self.ranges
        ]
        return payload


def index_url(spec: SourceSpec, initialized_at: datetime, statistic: str,
              lead_hours: int) -> str:
    initialized_at = _as_utc(initialized_at)
    _validate_run(spec, initialized_at, statistic, lead_hours)
    return spec.format_url(
        date=initialized_at.strftime("%Y%m%d"),
        cycle=initialized_at.hour,
        statistic=statistic,
        lead=lead_hours,
    )


def data_url(index: str) -> str:
    if not index.endswith(".idx"):
        raise GefsArchiveError(f"GEFS index URL must end in .idx: {index}")
    return index[:-4]


def parse_index(body: bytes | str) -> tuple[Message, ...]:
    """Parse wgrib2-style rows and derive each message's inclusive end byte."""
    text = body.decode("utf-8", errors="strict") if isinstance(body, bytes) else body
    pending: list[tuple[int, int, datetime, str, str, str, str]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split(":", 6)
        if len(parts) != 7:
            raise GefsArchiveError(
                f"GEFS index line {line_number}: expected 7 colon-delimited fields, "
                f"found {len(parts)}"
            )
        try:
            number = int(parts[0])
            offset = int(parts[1])
            initialized = datetime.strptime(parts[2], "d=%Y%m%d%H").replace(
                tzinfo=timezone.utc
            )
        except ValueError as exc:
            raise GefsArchiveError(
                f"GEFS index line {line_number}: invalid number, offset, or run stamp"
            ) from exc
        pending.append((number, offset, initialized, *parts[3:]))

    if not pending:
        raise GefsArchiveError("GEFS index is empty")
    for previous, current in zip(pending, pending[1:]):
        if current[0] != previous[0] + 1:
            raise GefsArchiveError(
                f"GEFS index message numbers jump from {previous[0]} to {current[0]}"
            )
        if current[1] <= previous[1]:
            raise GefsArchiveError(
                f"GEFS index offsets are not increasing at message {current[0]}"
            )
        if current[2] != previous[2]:
            raise GefsArchiveError("GEFS index mixes more than one initialization time")

    messages: list[Message] = []
    for index, item in enumerate(pending):
        number, offset, initialized, variable, level, forecast, statistic = item
        end = pending[index + 1][1] - 1 if index + 1 < len(pending) else None
        messages.append(Message(
            number=number,
            offset=offset,
            end=end,
            initialized_at=initialized,
            variable=variable,
            level=level,
            forecast=forecast,
            statistic=statistic,
        ))
    return tuple(messages)


def select_messages(messages: Iterable[Message],
                    selectors: Sequence[Mapping[str, str]]) -> tuple[Message, ...]:
    wanted = {(str(item["variable"]), str(item["level"])) for item in selectors}
    selected = tuple(
        message for message in messages
        if (message.variable, message.level) in wanted
    )
    found = {(message.variable, message.level) for message in selected}
    missing = sorted(wanted - found)
    if missing:
        rendered = ", ".join(f"{variable}:{level}" for variable, level in missing)
        raise GefsArchiveError(f"GEFS index is missing configured fields: {rendered}")
    duplicates = sorted(item for item in found if sum(
        (message.variable, message.level) == item for message in selected
    ) != 1)
    if duplicates:
        rendered = ", ".join(f"{variable}:{level}" for variable, level in duplicates)
        raise GefsArchiveError(f"GEFS index has ambiguous configured fields: {rendered}")
    return tuple(sorted(selected, key=lambda message: message.offset))


def coalesce_ranges(messages: Iterable[Message], *, max_gap_bytes: int = 0
                    ) -> tuple[ByteRange, ...]:
    if max_gap_bytes < 0:
        raise GefsArchiveError("max_gap_bytes cannot be negative")
    selected = sorted(messages, key=lambda message: message.offset)
    if not selected:
        return ()
    if any(message.end is None for message in selected):
        raise GefsArchiveError(
            "cannot range-fetch the final index message without the GRIB object size"
        )

    groups: list[list[Message]] = [[selected[0]]]
    for message in selected[1:]:
        previous = groups[-1][-1]
        assert previous.end is not None
        gap = message.offset - previous.end - 1
        if gap <= max_gap_bytes:
            groups[-1].append(message)
        else:
            groups.append([message])
    return tuple(ByteRange(
        start=group[0].offset,
        end=int(group[-1].end),
        messages=tuple(group),
    ) for group in groups)


def audit_index(spec: SourceSpec, initialized_at: datetime, statistic: str,
                lead_hours: int) -> IndexAudit:
    """Fetch one small index and report exact ranges; never downloads GRIB data."""
    target = index_url(spec, initialized_at, statistic, lead_hours)
    body, headers = fetch_with_headers(target, accept=spec.accept)
    messages = parse_index(body)
    expected = _as_utc(initialized_at)
    if messages[0].initialized_at != expected:
        raise GefsArchiveError(
            f"URL requests {expected.isoformat()} but index contains "
            f"{messages[0].initialized_at.isoformat()}"
        )
    selected = select_messages(messages, spec.params["selectors"])
    ranges = coalesce_ranges(
        selected, max_gap_bytes=int(spec.params.get("coalesce_gap_bytes", 0))
    )
    last_modified = _last_modified(headers)
    archive_delay = (
        (last_modified - expected).total_seconds() / 3600 if last_modified else None
    )
    availability_hours = int(spec.params["conservative_availability_hours"])
    if archive_delay is None:
        timestamp_status = "missing"
    elif 0 <= archive_delay <= availability_hours:
        timestamp_status = "plausible_operational_upload"
    elif availability_hours < archive_delay <= 24:
        timestamp_status = "late_operational_upload"
    else:
        timestamp_status = "archive_republication"
    return IndexAudit(
        initialized_at=expected,
        lead_hours=lead_hours,
        statistic=statistic,
        url=target,
        last_modified=last_modified,
        index_messages=len(messages),
        selected_messages=len(selected),
        selected_bytes=sum(int(message.bytes) for message in selected),
        transfer_bytes=sum(item.bytes for item in ranges),
        archive_delay_hours=archive_delay,
        archive_timestamp_status=timestamp_status,
        ranges=ranges,
    )


def download_grib(audit: IndexAudit) -> tuple[bytes, tuple[Mapping[str, str], ...]]:
    """Download audited messages only and return a valid multi-message GRIB stream.

    A coalesced range can contain intervening messages, which is intentional and
    recorded by the audit. Each range still starts and ends on a GRIB message
    boundary. Concatenating ranges remains a stream ecCodes can read in order.
    """
    target = data_url(audit.url)
    pieces: list[bytes] = []
    response_headers: list[Mapping[str, str]] = []
    for byte_range in audit.ranges:
        body, headers = fetch_range(
            target, byte_range.start, byte_range.end,
            accept="application/octet-stream",
        )
        if not body.startswith(b"GRIB") or not body.endswith(b"7777"):
            raise GefsArchiveError(
                f"GEFS range {byte_range.header} does not start and end on GRIB boundaries"
            )
        pieces.append(body)
        response_headers.append(headers)
    combined = b"".join(pieces)
    if not combined:
        raise GefsArchiveError("GEFS selection produced no byte ranges")
    return combined, tuple(response_headers)


def decode_points(body: bytes, points: Sequence[object], *, statistic: str,
                  codes_module=None):
    """Decode target messages at configured points into a standardized table.

    The coalesced byte range also carries 10 m wind messages. Selection is by
    decoded GRIB short name, not message position, so those transfer-only fields
    cannot leak into the feature table. Temperatures become Celsius; GEFS total
    precipitation's kg m**-2 is numerically millimetres of water.
    """
    import pandas as pd

    if codes_module is None:
        try:
            import eccodes as codes_module
        except ModuleNotFoundError as exc:
            raise GefsArchiveError(
                "decoding GEFS needs the optional dependencies in "
                "requirements-gefs.txt"
            ) from exc

    rows: list[dict] = []
    decoded: set[str] = set()
    message_count = 0
    offset = 0
    while offset < len(body):
        handle = codes_module.codes_new_from_message(body[offset:])
        if handle is None:
            raise GefsArchiveError(f"ecCodes stopped with {len(body) - offset} unread bytes")
        message_count += 1
        try:
            length = int(codes_module.codes_get(handle, "totalLength"))
            if length <= 0 or offset + length > len(body):
                raise GefsArchiveError(
                    f"invalid GRIB message length {length} at byte {offset}"
                )
            offset += length
            short_name = str(codes_module.codes_get(handle, "shortName"))
            if short_name not in SHORT_NAME_TO_VARIABLE:
                continue
            variable, expected_units = SHORT_NAME_TO_VARIABLE[short_name]
            units = str(codes_module.codes_get(handle, "units"))
            if units != expected_units:
                raise GefsArchiveError(
                    f"GEFS {short_name} units changed from {expected_units!r} to {units!r}"
                )
            initialized = _grib_datetime(codes_module, handle, "dataDate", "dataTime")
            valid = _grib_datetime(codes_module, handle, "validityDate", "validityTime")
            start_step = int(codes_module.codes_get(handle, "startStep"))
            end_step = int(codes_module.codes_get(handle, "endStep"))
            if start_step != end_step - 6:
                raise GefsArchiveError(
                    f"GEFS {short_name} interval f{start_step:03d}-f{end_step:03d} "
                    "is not the required six-hour interval"
                )
            if valid != initialized + _hours(end_step):
                raise GefsArchiveError(
                    f"GEFS {short_name} validity {valid.isoformat()} does not equal "
                    f"initialization + f{end_step:03d}"
                )
            decoded.add(short_name)
            for point in points:
                nearest = codes_module.codes_grib_find_nearest(
                    handle, float(point.lat), float(point.lon), npoints=1
                )[0]
                value = float(nearest.value)
                # A temperature mean is absolute Kelvin and needs an offset; an
                # ensemble spread is a difference, for which 1 K == 1 degC.
                if short_name in {"tmax", "tmin"} and statistic == "geavg":
                    value -= 273.15
                rows.append({
                    "initialized_at": initialized,
                    "valid_at": valid,
                    "interval_start_hours": start_step,
                    "lead_hours": end_step,
                    "statistic": statistic,
                    "variable": variable,
                    "point": str(point.name),
                    "point_lat": float(point.lat),
                    "point_lon": float(point.lon),
                    "grid_lat": float(nearest.lat),
                    "grid_lon": _signed_lon(float(nearest.lon)),
                    "grid_distance_km": float(nearest.distance),
                    "value": value,
                    "units": "degC" if short_name in {"tmax", "tmin"} else "mm",
                })
        finally:
            codes_module.codes_release(handle)

    if not message_count:
        raise GefsArchiveError("ecCodes found no GRIB messages in the archived selection")
    missing = sorted(set(SHORT_NAME_TO_VARIABLE) - decoded)
    if missing:
        raise GefsArchiveError(
            f"archived GEFS selection is missing decoded fields: {', '.join(missing)}"
        )
    table = pd.DataFrame(rows)
    expected_rows = len(points) * len(SHORT_NAME_TO_VARIABLE)
    if len(table) != expected_rows:
        raise GefsArchiveError(
            f"decoded {len(table)} point-fields, expected {expected_rows}; "
            "the archive may contain duplicate target messages"
        )
    if (table["initialized_at"].nunique() != 1
            or table["valid_at"].nunique() != 1
            or table["lead_hours"].nunique() != 1):
        raise GefsArchiveError(
            "archived GEFS target messages do not share one run and lead"
        )
    if len(table) and table["grid_distance_km"].max() > 50:
        raise GefsArchiveError(
            f"nearest GEFS point is {table['grid_distance_km'].max():.1f} km away; "
            "configured coordinates or grid may have changed"
        )
    return table.sort_values(["lead_hours", "variable", "point"], ignore_index=True)


def _grib_datetime(codes_module, handle, date_key: str, time_key: str) -> datetime:
    raw_date = int(codes_module.codes_get(handle, date_key))
    raw_time = int(codes_module.codes_get(handle, time_key))
    try:
        return datetime.strptime(
            f"{raw_date:08d}{raw_time:04d}", "%Y%m%d%H%M"
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise GefsArchiveError(
            f"invalid GRIB timestamp {date_key}={raw_date}, {time_key}={raw_time}"
        ) from exc


def _hours(value: int):
    from datetime import timedelta
    return timedelta(hours=value)


def _signed_lon(value: float) -> float:
    return value - 360.0 if value > 180.0 else value


def run_audit(spec: SourceSpec, run_date: date, cycle: int,
              leads: Sequence[int], statistics: Sequence[str],
              through_hour: int | None = None) -> dict:
    initialized = datetime(
        run_date.year, run_date.month, run_date.day, cycle, tzinfo=timezone.utc
    )
    if not leads:
        raise GefsArchiveError("at least one GEFS lead is required")
    audits = [
        audit_index(spec, initialized, statistic, lead)
        for statistic in statistics for lead in leads
    ]
    sampled_leads = len(set(leads))
    step = int(spec.params["forecast_step_hours"])
    configured_maximum = int(spec.params["max_forecast_hour"])
    maximum = configured_maximum if through_hour is None else int(through_hour)
    if maximum <= 0 or maximum > configured_maximum or maximum % step:
        raise GefsArchiveError(
            f"GEFS estimate horizon must be a positive {step}-hour step through "
            f"{configured_maximum}"
        )
    full_leads = maximum // step
    sample_transfer = sum(item.transfer_bytes for item in audits)
    sample_selected = sum(item.selected_bytes for item in audits)
    estimated_run_transfer = round(sample_transfer / sampled_leads * full_leads)
    estimated_run_selected = round(sample_selected / sampled_leads * full_leads)
    estimated_range_requests = round(
        sum(len(item.ranges) for item in audits) / sampled_leads * full_leads
    )
    latest_publication = max(
        (item.last_modified for item in audits if item.last_modified), default=None
    )
    return {
        "source": spec.name,
        "operational_run": initialized.isoformat(),
        "statistics": list(statistics),
        "sample_leads": list(leads),
        "sample_indexes": len(audits),
        "sample_transfer_bytes": sample_transfer,
        "sample_selected_bytes": sample_selected,
        "full_leads_per_statistic": full_leads,
        "estimate_through_hour": maximum,
        "estimated_transfer_bytes_per_run": estimated_run_transfer,
        "estimated_selected_bytes_per_run": estimated_run_selected,
        "estimated_range_requests_per_run": estimated_range_requests,
        "conservative_availability_hours": int(
            spec.params["conservative_availability_hours"]
        ),
        "archive_timestamp_status_counts": {
            status: sum(item.archive_timestamp_status == status for item in audits)
            for status in sorted({item.archive_timestamp_status for item in audits})
        },
        "latest_sample_last_modified": (
            latest_publication.isoformat() if latest_publication else None
        ),
        "audits": [item.as_dict() for item in audits],
    }


def _validate_run(spec: SourceSpec, initialized: datetime, statistic: str,
                  lead_hours: int) -> None:
    operational_from = _as_utc(datetime.fromisoformat(str(spec.params["operational_from"])))
    if initialized < operational_from:
        raise GefsArchiveError(
            f"{initialized.isoformat()} predates operational GEFS v12 "
            f"({operational_from.isoformat()})"
        )
    cycles = tuple(int(value) for value in spec.params["cycles"])
    if initialized.hour not in cycles or initialized.minute or initialized.second:
        raise GefsArchiveError(f"GEFS cycle must be one of {cycles} UTC")
    statistics = tuple(str(value) for value in spec.params["statistics"])
    if statistic not in statistics:
        raise GefsArchiveError(f"GEFS statistic must be one of {statistics}")
    step = int(spec.params["forecast_step_hours"])
    maximum = int(spec.params["max_forecast_hour"])
    if lead_hours <= 0 or lead_hours > maximum or lead_hours % step:
        raise GefsArchiveError(
            f"GEFS lead must be a positive {step}-hour step through {maximum}"
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise GefsArchiveError("GEFS initialization must be timezone-aware")
    return value.astimezone(timezone.utc)


def _last_modified(headers: Mapping[str, str]) -> datetime | None:
    raw = headers.get("last-modified") or headers.get("Last-Modified")
    if not raw:
        return None
    try:
        result = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else result.replace(
        tzinfo=timezone.utc
    )
