"""Talking to NOAA NCEI's nClimGrid-Daily county averages.

Two files per month matter, and they disagree on purpose:

  * the data CSV, whose name carries a status (`scaled` for a finished month,
    `prelim` for the month in progress) and which ALWAYS has 31 day columns
    whatever the month's length;
  * the version sidecar `ncdd-YYYYMM-version.txt`, which is the authority on what
    the month really covers and carries the GHCN-Daily build id -- the sharpest
    revision detector this source offers.

The two vocabularies do not match: the sidecar says "complete" where the filename
says "scaled". `status_for` translates, because believing the filename over the
sidecar is how a half-finished month gets treated as final.

    from pipeline.clients import nclimgrid
    version = nclimgrid.version(spec, 2012, 7)          # -> VersionInfo
    body, headers, status = nclimgrid.month(spec, "tmax", 2012, 7, version=version)
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator

from ..calendar import NCLIMGRID_PRELIM, NCLIMGRID_SCALED, expected_nclimgrid_status
from ..config import SourceSpec
from ..http import FetchError, fetch_with_headers

METADATA_FIELDS = 6
DAY_SLOTS = 31
FIELDS = METADATA_FIELDS + DAY_SLOTS

# "nClimGrid-Daily v1-0-0 complete for 2026-08-01 through 2026-08-31"
_HEADER = re.compile(
    r"^(?P<product>\S+)\s+(?P<version>\S+)\s+(?P<state>complete|prelim)\s+for\s+"
    r"(?P<start>\d{4}-\d{2}-\d{2})\s+through\s+(?P<end>\d{4}-\d{2}-\d{2})",
    re.MULTILINE,
)
_CREATED = re.compile(r"created on\s+(?P<created>\d{4}-\d{2}-\d{2})")
_SOFTWARE = re.compile(r"software\s+(?P<software>\S+)")
# Current format names a GHCN-Daily build; the pre-2017 bulk reprocessing only
# says when GHCNd was downloaded, so there is no build id to record for old months.
_BUILD = re.compile(r"from GHCN-Daily\s+(?P<build>\S+)")
_DOWNLOADED = re.compile(r"downloaded on\s+(?P<downloaded>.+?)\s*$", re.MULTILINE)


class NclimgridResponseError(Exception):
    """A response did not look like nClimGrid county averages."""


@dataclass(frozen=True)
class VersionInfo:
    """The month's version sidecar, parsed.

    `state` is the sidecar's own word ("complete" or "prelim"); `status` is the
    filename token it implies ("scaled" or "prelim"). `covers_end` is the real
    last day of data, which for a prelim month is EARLIER than the 31 day columns
    in the CSV suggest.
    """

    year: int
    month: int
    state: str
    covers_start: date
    covers_end: date
    created_on: date | None = None
    software: str | None = None
    build: str | None = None
    downloaded: str | None = None
    text: str = ""

    @property
    def status(self) -> str:
        return NCLIMGRID_SCALED if self.state == "complete" else NCLIMGRID_PRELIM

    @property
    def stamp(self) -> str:
        """A short vintage token for the raw manifest.

        The GHCN build id when the sidecar has one, otherwise the download date
        the old format gives instead, so every month gets something comparable.
        """
        vintage = self.build or self.downloaded or "unknown-build"
        return f"{self.state} {self.covers_start.isoformat()}..{self.covers_end.isoformat()} {vintage}"


def version_url(spec: SourceSpec, year: int, month: int) -> str:
    template = str(spec.params["version_url_template"])
    return template.format(year=year, month=month)


def data_url(spec: SourceSpec, variable: str, year: int, month: int, status: str) -> str:
    return spec.format_url(var=variable, year=year, month=month, status=status)


def version(spec: SourceSpec, year: int, month: int) -> tuple[VersionInfo, bytes, dict[str, str]]:
    """Fetch and parse a month's version sidecar."""
    target = version_url(spec, year, month)
    body, headers = fetch_with_headers(target, accept=spec.accept)
    return parse_version(body, year, month, url=target), body, headers


def parse_version(body: bytes | str, year: int, month: int, *, url: str = "") -> VersionInfo:
    """The sidecar's contents, in either of its two formats.

    Offline, so the tests can read tests/fixtures/nclimgrid directly.
    """
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    header = _HEADER.search(text)
    if not header:
        raise NclimgridResponseError(
            f"{url or 'version file'}: no '<product> <version> complete|prelim for <date> "
            f"through <date>' line; got {text[:120]!r}"
        )
    covers_start = date.fromisoformat(header["start"])
    covers_end = date.fromisoformat(header["end"])
    if (covers_start.year, covers_start.month) != (year, month):
        raise NclimgridResponseError(
            f"{url or 'version file'}: asked for {year}-{month:02d} but the file covers "
            f"{covers_start.isoformat()}..{covers_end.isoformat()}"
        )
    created = _CREATED.search(text)
    software = _SOFTWARE.search(text)
    build = _BUILD.search(text)
    downloaded = _DOWNLOADED.search(text)
    return VersionInfo(
        year=year,
        month=month,
        state=header["state"],
        covers_start=covers_start,
        covers_end=covers_end,
        created_on=date.fromisoformat(created["created"]) if created else None,
        software=software["software"] if software else None,
        build=build["build"] if build else None,
        downloaded=downloaded["downloaded"] if downloaded else None,
        text=text,
    )


def status_for(spec: SourceSpec, year: int, month: int, today: date,
               info: VersionInfo | None = None) -> str:
    """Which filename status to try for a month: the sidecar's word, or the calendar's guess.

    The sidecar wins whenever we have it, because it is a statement of fact where
    pipeline.calendar's day-of-month rule is one observation rounded upwards.
    """
    if info is not None:
        return info.status
    return expected_nclimgrid_status(year, month, today)


def month(spec: SourceSpec, variable: str, year: int, month_number: int, *,
          today: date, info: VersionInfo | None = None) -> tuple[bytes, dict[str, str], str]:
    """Fetch one variable-month, falling back between scaled and prelim.

    A 404 here is a fact, not a hiccup: the month in progress exists ONLY as
    prelim (scaled 404s), and once scaled exists the prelim file is deleted
    outright, so either name can be the 404 depending on the day. Returns the
    status that actually answered -- the caller must record it with the bytes,
    because prelim values are not scaled to the monthly product and the two must
    never land in one series unlabelled.
    """
    preferred = status_for(spec, year, month_number, today, info)
    fallback = NCLIMGRID_PRELIM if preferred == NCLIMGRID_SCALED else NCLIMGRID_SCALED
    first_error: FetchError | None = None
    for status in (preferred, fallback):
        target = data_url(spec, variable, year, month_number, status)
        try:
            body, headers = fetch_with_headers(target, accept=spec.accept)
        except FetchError as exc:
            if exc.status != 404:
                raise
            first_error = first_error or exc
            continue
        return body, headers, status
    raise NclimgridResponseError(
        f"neither {preferred} nor {fallback} exists for {variable} {year}-{month_number:02d} "
        f"({first_error}). A month before 1951-01 or later than the current month has no file."
    )


def rows(body: bytes, *, variable: str, year: int, month: int, url: str = "",
         region_type: str = "cty") -> Iterator[tuple[str, str, list[str]]]:
    """(NCEI id, region name, 31 raw day strings) per county row, validated.

    Deliberately returns the day slots as strings and all 31 of them: deciding how
    many are real, and what -999.99 means, belongs to pipeline.clean.nclimgrid,
    which knows the month's length. Parsed with the csv module rather than split
    on commas, so a region name containing one cannot shift every day value by a
    column.
    """
    text = body.decode("utf-8", "replace")
    reader = csv.reader(io.StringIO(text))
    seen = 0
    for number, fields in enumerate(reader, start=1):
        if not fields or not any(field.strip() for field in fields):
            continue
        if len(fields) != FIELDS:
            raise NclimgridResponseError(
                f"{url or 'county file'}:{number}: expected {FIELDS} fields "
                f"({METADATA_FIELDS} metadata + {DAY_SLOTS} day slots), found {len(fields)}"
            )
        if fields[0].strip() != region_type:
            raise NclimgridResponseError(
                f"{url or 'county file'}:{number}: region type {fields[0].strip()!r}, "
                f"expected {region_type!r}"
            )
        if (int(fields[3]), int(fields[4])) != (year, month):
            raise NclimgridResponseError(
                f"{url or 'county file'}:{number}: row is {fields[3]}-{fields[4]}, "
                f"asked for {year}-{month:02d}"
            )
        if fields[5].strip().lower() != variable.lower():
            raise NclimgridResponseError(
                f"{url or 'county file'}:{number}: row holds {fields[5].strip()!r}, "
                f"asked for {variable!r}"
            )
        # str, never int: the identifier's leading digits are a state code and
        # "05" must not become 5.
        yield fields[1].strip(), fields[2].strip(), [value.strip() for value in fields[METADATA_FIELDS:]]
        seen += 1
    if not seen:
        raise NclimgridResponseError(f"{url or 'county file'}: no county rows")


def postal_from_name(region_name: str) -> str:
    """The authoritative state postal code, read from the "IA: Polk County" prefix.

    NCEI's own NCEI-to-FIPS cross-reference has its state_name column swapped for
    codes 11 and 12, so the name prefix -- not any name-keyed lookup -- is what
    can be trusted here.
    """
    prefix, separator, _ = region_name.partition(":")
    code = prefix.strip().upper()
    if not separator or len(code) != 2 or not code.isalpha():
        raise NclimgridResponseError(
            f"region name {region_name!r} does not start with a two-letter state prefix "
            f"like 'IA: Polk County'"
        )
    return code
