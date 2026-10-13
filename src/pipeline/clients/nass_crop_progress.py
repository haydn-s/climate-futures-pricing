"""Read USDA ESMIS Crop Progress catalogs and original text releases.

The release archive is the point-in-time source.  Each report contains the
values published that week, including its contemporaneous previous-week,
previous-year, and five-year comparisons.  That is preferable to reconstructing
history from a database that may contain later revisions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from ..http import fetch

ALLOWED_HOSTS = frozenset({
    "esmis.nal.usda.gov",
    "downloads.usda.library.cornell.edu",
    "usda.library.cornell.edu",
})
RELEASE_TEXT = re.compile(
    r"^(?P<date>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{4})\s+-\s+txt$"
)
RELEASED = re.compile(
    r"\bReleased\s+(?P<date>[A-Z][a-z]+\s+\d{1,2},\s+\d{4})\b",
    re.IGNORECASE,
)
TITLE = re.compile(
    r"^Corn\s+(?P<stage>[A-Za-z ]+?)\s+[-–]\s+Selected States"
    r"(?::\s*Week Ending\s+(?P<week>.+?))?\s*$",
    re.IGNORECASE,
)
ANY_TITLE = re.compile(
    r"^[A-Za-z][A-Za-z ]+?\s+[-–]\s+Selected States(?:\s*:.*)?$",
    re.IGNORECASE,
)
ROW = re.compile(r"^\s*(?P<label>.+?)\s*\.{2,}\s*:\s*(?P<values>.*?)\s*$")
VALUE = re.compile(r"\(NA\)|\d+(?:\.\d+)?|-", re.IGNORECASE)
PAGE = re.compile(r"(?:[?&]page=)(\d+)")
CONDITION_CATEGORIES = ("very_poor", "poor", "fair", "good", "excellent")
PROGRESS_COMPARISONS = (
    "previous_year", "previous_week", "current", "five_year_average",
)
EASTERN = ZoneInfo("America/New_York")


class CropProgressError(ValueError):
    """An archive page or report changed shape or contradicted its metadata."""


@dataclass(frozen=True)
class Release:
    release_date: date
    url: str


@dataclass(frozen=True)
class Catalog:
    releases: tuple[Release, ...]
    last_page: int


@dataclass(frozen=True)
class Observation:
    release_date: date
    reference_date: date
    available_at: datetime
    measure: str
    stage: str
    region: str
    region_type: str
    comparison: str
    category: str
    value: float | None
    quality_status: str = "ok"


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchor: str | None = None
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() == "a":
            self.anchor = dict(attrs).get("href")
            self.text = []

    def handle_data(self, data: str) -> None:
        if self.anchor is not None:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self.anchor is not None:
            self.links.append((self.anchor, " ".join("".join(self.text).split())))
            self.anchor = None
            self.text = []


def parse_catalog(body: bytes, *, url: str) -> Catalog:
    """Release-text links and the largest page number in one catalog page."""
    parser = _Links()
    try:
        parser.feed(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CropProgressError(f"{url}: catalog is not valid UTF-8 HTML") from exc
    releases: dict[date, Release] = {}
    last_page = 0
    for href, label in parser.links:
        target = urljoin(url, href)
        page = PAGE.search(target)
        if page:
            last_page = max(last_page, int(page.group(1)))
        match = RELEASE_TEXT.match(label)
        if not match or not urlsplit(target).path.casefold().endswith(".txt"):
            continue
        host = (urlsplit(target).hostname or "").casefold()
        if host not in ALLOWED_HOSTS:
            raise CropProgressError(f"{url}: refusing release link on host {host!r}")
        released = datetime.strptime(match["date"], "%b %d %Y").date()
        existing = releases.get(released)
        if existing is not None and existing.url != target:
            raise CropProgressError(
                f"{url}: more than one text release is listed for {released}"
            )
        releases[released] = Release(released, target)
    return Catalog(tuple(sorted(releases.values(), key=lambda item: item.release_date,
                                reverse=True)), last_page)


def catalog(url: str) -> Catalog:
    return parse_catalog(fetch(url, accept="text/html"), url=url)


def report(url: str, *, expected_release: date | None = None) -> tuple[bytes, list[Observation]]:
    body = fetch(url, accept="text/plain")
    return body, parse_report(body, expected_release=expected_release, url=url)


def parse_report(body: bytes, *, expected_release: date | None = None,
                 url: str = "") -> list[Observation]:
    """Corn progress and condition values from one archived text report."""
    text = _decode(body, url)
    released_match = RELEASED.search(text)
    if not released_match:
        if expected_release is not None and _is_release_notice(text):
            return []
        raise CropProgressError(f"{url or 'report'}: no USDA release date")
    released = datetime.strptime(released_match["date"].title(), "%B %d, %Y").date()
    if expected_release is not None and released != expected_release:
        raise CropProgressError(
            f"{url or 'report'}: body says {released}, catalog says {expected_release}"
        )
    reference = _previous_sunday(released)
    available = datetime.combine(released, time(16, 0), EASTERN).astimezone(timezone.utc)
    lines = text.splitlines()
    boundaries = [number for number, line in enumerate(lines)
                  if ANY_TITLE.match(line.strip())]
    observations: list[Observation] = []
    for index, start in enumerate(boundaries):
        match = TITLE.match(lines[start].strip())
        if match is None:
            continue
        end = boundaries[index + 1] if index + 1 < len(boundaries) else len(lines)
        raw_stage = " ".join(match["stage"].split()).casefold()
        rows = _rows(lines[start + 1:end])
        if raw_stage == "condition":
            observations.extend(_condition_rows(
                rows, released=released, reference=reference, available=available
            ))
        else:
            observations.extend(_progress_rows(
                rows, stage=_stage(raw_stage), released=released,
                reference=reference, available=available
            ))
    has_corn_title = any(TITLE.match(lines[number].strip()) for number in boundaries)
    if not observations and expected_release is not None and boundaries and not has_corn_title:
        return []
    if not observations:
        raise CropProgressError(f"{url or 'report'}: no readable corn tables")
    _validate(observations, url or "report")
    return observations


def _is_release_notice(text: str) -> bool:
    """A dated cancellation/delay notice listed in the release catalog as TXT."""
    folded = " ".join(text.casefold().split())
    markers = (
        " delayed", " delay ", "postpon", "cancel", "suspend",
        "will not be issued", "not be released",
    )
    return (
        "crop progress" in folded
        and "selected states" not in folded
        and any(marker in folded for marker in markers)
    )


def document_type(body: bytes, observations: list[Observation]) -> str:
    """Classification recorded in the raw manifest for a validated catalog item."""
    if observations:
        return "report"
    text = _decode(body, "report")
    return "release_notice" if _is_release_notice(text) else "report_without_corn"


def _decode(body: bytes, url: str) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CropProgressError(f"{url or 'report'}: text encoding is not UTF-8 or cp1252")


def _previous_sunday(released: date) -> date:
    days = (released.weekday() + 1) % 7
    return released - timedelta(days=days or 7)


def _rows(lines: list[str]) -> list[tuple[str, list[float | None]]]:
    output = []
    for line in lines:
        match = ROW.match(line)
        if not match:
            continue
        values = [_number(value) for value in VALUE.findall(match["values"])]
        if values:
            output.append((" ".join(match["label"].split()), values))
    return output


def _number(value: str) -> float | None:
    if value.upper() == "(NA)":
        return None
    if value == "-":
        return 0.0
    return float(value)


def _region(label: str) -> tuple[str, str, str]:
    folded = label.casefold()
    if folded == "previous week":
        return "selected_states", "selected_states", "previous_week"
    if folded == "previous year":
        return "selected_states", "selected_states", "previous_year"
    if re.fullmatch(r"\d+ states", folded):
        return "selected_states", "selected_states", "current"
    return label, "state", "current"


def _condition_rows(rows, *, released: date, reference: date,
                    available: datetime) -> list[Observation]:
    output = []
    for label, values in rows:
        if len(values) != 5:
            continue
        region, region_type, comparison = _region(label)
        for category, value in zip(CONDITION_CATEGORIES, values, strict=True):
            output.append(Observation(
                released, reference, available, "condition", "condition",
                region, region_type, comparison, category, value,
            ))
    return output


def _progress_rows(rows, *, stage: str, released: date, reference: date,
                   available: datetime) -> list[Observation]:
    output = []
    for label, values in rows:
        if len(values) != 4 or label.casefold().startswith("previous "):
            continue
        region, region_type, _ = _region(label)
        for comparison, value in zip(PROGRESS_COMPARISONS, values, strict=True):
            output.append(Observation(
                released, reference, available, "progress", stage, region,
                region_type, comparison, "percent", value,
            ))
    return output


def _stage(value: str) -> str:
    aliases = {"doughing": "dough", "dent": "dented"}
    return aliases.get(value, value.replace(" ", "_"))


def _validate(observations: list[Observation], source: str) -> None:
    for observation in observations:
        if observation.value is not None and not 0 <= observation.value <= 100:
            raise CropProgressError(
                f"{source}: {observation.measure} value outside 0..100: "
                f"{observation.value}"
            )
    groups: dict[tuple, list[int]] = {}
    for index, item in enumerate(observations):
        if item.measure == "condition":
            key = (item.region, item.comparison)
            groups.setdefault(key, []).append(index)
    for key, indices in groups.items():
        values = [observations[index].value for index in indices]
        if len(values) == 5 and all(value is not None for value in values):
            total = sum(value for value in values if value is not None)
            if abs(total - 100.0) > 0.01:
                if key[0] == "selected_states":
                    raise CropProgressError(
                        f"{source}: aggregate condition percentages for {key} "
                        f"sum to {total}, not 100"
                    )
                status = f"source_condition_sum_{total:g}"
                for index in indices:
                    observations[index] = replace(
                        observations[index], quality_status=status
                    )
