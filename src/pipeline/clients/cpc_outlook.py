"""Fetch and parse a CPC 6-10 or 8-14 day outlook: a zipped ESRI shapefile set.

One issuance per zip, near-daily from 2011-01-03. The parser's whole job is to
turn two incompatible shapefile schemas into one row shape, and to refuse a file
that is neither rather than guessing.

    bundle = client.read(body, product="610temp", issued=date(2012, 7, 10),
                         category="Above")       # category only for the early era
    for contour in bundle.contours:
        ...

WHAT A CONTOUR IS. CPC publishes nested probability contours for the LEADING
tercile, not a partition of the map. A point falls inside one to three polygons
and its forecast is the innermost -- the highest probability among those
containing it. A point inside none is not missing: it is near-climatological
odds, which is a value (zero anomaly) and not an absence. `anomaly` is therefore
signed against climatology rather than raw: a 60% Below contour is the opposite
of a 60% Above one, and averaging the raw numbers reads as agreement.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from ..config import SourceSpec
from ..http import fetch_with_headers

# The early era carries the category in the filename; the unified era in a field.
EARLY_CATEGORY = {"abv": "Above", "bel": "Below"}
CATEGORIES = ("Above", "Below", "Normal")

# The unified schema's attributes, and the early schema's. Presence of `Prob`
# decides which reader runs -- a filename is not evidence about file contents.
UNIFIED_FIELDS = ("Fcst_Date", "Start_Date", "End_Date", "Prob", "Cat")
EARLY_FIELDS = ("label", "Fcst_Date", "Valid_Per")


class OutlookError(Exception):
    """A payload that is not a readable CPC outlook."""


@dataclass(frozen=True)
class Contour:
    """One probability polygon, in whichever era it came from."""

    probability: float
    category: str
    anomaly: float
    rings: tuple[tuple[tuple[float, float], ...], ...]

    def contains(self, lat: float, lon: float) -> bool:
        """Whether a point falls inside, by even-odd rule across every ring.

        Coordinates are (lon, lat) in the file -- NAD83 degrees, GeoJSON order.
        This signature takes lat first to match config/geography.yaml, and swaps
        internally, because reading the file's pairs as (lat, lon) puts the corn
        belt in the Indian Ocean and silently matches nothing.
        """
        inside = False
        for ring in self.rings:
            if _in_ring(lon, lat, ring):
                inside = not inside
        return inside


@dataclass(frozen=True)
class Bundle:
    """Every contour in one issuance, plus the dates that make it point-in-time."""

    product: str
    issued: date
    valid_start: date
    valid_end: date
    era: str
    contours: tuple[Contour, ...]

    @property
    def lead_days(self) -> int:
        return (self.valid_start - self.issued).days


def url(spec: SourceSpec, product: str, issued: date, *, suffix: str = "") -> str:
    return spec.format_url(product=product, era=suffix,
                           date=issued.strftime(str(spec.params["date_format"])))


def index(spec: SourceSpec) -> dict[tuple[str, str], list[date]]:
    """Every dated file the archive actually holds, as {(product, suffix): [dates]}.

    The archive is near-daily but skips weekends and holidays unevenly, so a plan
    built from calendar dates is mostly 404s. One listing request answers it.

    Two things are deliberately excluded. `610temp_latest.zip` is a MOVING
    POINTER overwritten in place -- archiving it would record bytes that cannot be
    reproduced or dated. And a stray `610tempYYYYMMDD.zip` with no separator is a
    single anomaly rather than a third naming era, so it is skipped too: the
    regex below requires the separator.
    """
    body, _ = fetch_with_headers(str(spec.params["index_url"]), accept=spec.accept)
    text = body.decode("utf-8", errors="replace")
    products = {str(name) for name in spec.params["products"]}
    suffixes = {str(s) for s in spec.params["early_suffixes"]}
    found: dict[tuple[str, str], list[date]] = {}
    pattern = re.compile(r'href="(\d{3}(?:temp|prcp))(abv|bel)?_(\d{8})\.zip"')
    for product, suffix, stamp in pattern.findall(text):
        if product not in products or (suffix and suffix not in suffixes):
            continue
        try:
            issued = datetime.strptime(stamp, "%Y%m%d").date()
        except ValueError:
            continue
        found.setdefault((product, suffix or ""), []).append(issued)
    for dates in found.values():
        dates.sort()
    return found


def download(spec: SourceSpec, product: str, issued: date, *,
             suffix: str = "") -> tuple[bytes, Mapping[str, str], str]:
    target = url(spec, product, issued, suffix=suffix)
    body, headers = fetch_with_headers(target, accept=spec.accept)
    return body, headers, target


def read(body: bytes, *, product: str, issued: date | None = None,
         category: str | None = None,
         leads: tuple[int, int] | None = None) -> Bundle:
    """Parse a zipped shapefile set into one Bundle, whichever era it is.

    `category` is required for the early era and ignored for the unified one,
    where the file says so itself. `leads` is needed only for an EMPTY outlook --
    see below.
    """
    shapes, records, fields = _open(body)
    names = tuple(fields)
    if not records:
        return _read_empty(product, names, issued, leads)
    if "Prob" in names:
        return _read_unified(product, shapes, records, names)
    if "label" in names:
        if category is None:
            raise OutlookError(
                f"{product}: this is the early schema (fields {names}), which does not "
                f"carry a category -- pass category='Above' or 'Below' from the filename")
        return _read_early(product, shapes, records, names, category, issued)
    raise OutlookError(
        f"{product}: unrecognised outlook schema. Expected {UNIFIED_FIELDS} "
        f"or {EARLY_FIELDS}, found {names}")


# ------------------------------------------------------------------- the readers


def _read_empty(product: str, names: tuple[str, ...], issued: date | None,
                leads: tuple[int, int] | None) -> Bundle:
    """An outlook with no polygons at all, which is a FORECAST and not a failure.

    When CPC expects no significant departure anywhere in the CONUS it draws no
    contours, and the shapefile ships with zero records. Verified: 814prcp for
    2014-07-19 is 13 KB with shapes=0 while 814temp the same day has twelve.

    Treating this as corrupt is what stopped the first two backfill runs dead at
    that exact date, 1,999 files into 12,558. Every point is then at
    climatological odds -- which is what `best()` already returns for a point in
    no contour -- so the only thing missing is the dates, because those live in
    the attributes and there are no attributes. The issuance comes from the
    filename and the valid period from the product's configured lead times.

    The dbf's SCHEMA survives even with no records, so the era is still readable
    from the field names.
    """
    era = "unified" if "Prob" in names else "early"
    if issued is None or leads is None:
        raise OutlookError(
            f"{product}: the file holds no polygons, which is a valid forecast of "
            f"climatological odds everywhere -- but its dates live in the attributes, "
            f"so reading it needs `issued` from the filename and `leads` from config")
    low, high = int(leads[0]), int(leads[1])
    from datetime import timedelta
    return Bundle(product=product, issued=issued,
                  valid_start=issued + timedelta(days=low),
                  valid_end=issued + timedelta(days=high),
                  era=era, contours=())


def _read_unified(product: str, shapes: list[Any], records: list[list[Any]],
                  names: tuple[str, ...]) -> Bundle:
    contours: list[Contour] = []
    issued = start = end = None
    for shape, record in zip(shapes, records):
        row = dict(zip(names, record))
        issued = issued or _as_date(row["Fcst_Date"])
        start = start or _as_date(row["Start_Date"])
        end = end or _as_date(row["End_Date"])
        category = str(row["Cat"]).strip()
        if category not in CATEGORIES:
            raise OutlookError(f"{product}: unknown category {category!r}, "
                               f"expected one of {CATEGORIES}")
        probability = float(row["Prob"])
        contours.append(Contour(probability=probability, category=category,
                                anomaly=_anomaly(probability, category),
                                rings=_rings(shape)))
    if issued is None or start is None or end is None:
        raise OutlookError(f"{product}: no shapes, so the file carries no dates")
    return Bundle(product=product, issued=issued, valid_start=start, valid_end=end,
                  era="unified", contours=tuple(contours))


def _read_early(product: str, shapes: list[Any], records: list[list[Any]],
                names: tuple[str, ...], category: str, issued: date | None) -> Bundle:
    if category not in CATEGORIES:
        raise OutlookError(f"{product}: unknown category {category!r}")
    contours: list[Contour] = []
    read_issued = start = end = None
    for shape, record in zip(shapes, records):
        row = dict(zip(names, record))
        read_issued = read_issued or _as_date(row["Fcst_Date"])
        if start is None:
            start, end = _valid_period(str(row["Valid_Per"]), product)
        # The probability is a space-padded STRING here, not a number.
        probability = float(str(row["label"]).strip())
        contours.append(Contour(probability=probability, category=category,
                                anomaly=_anomaly(probability, category),
                                rings=_rings(shape)))
    resolved = read_issued or issued
    if resolved is None or start is None or end is None:
        raise OutlookError(f"{product}: no shapes, so the file carries no dates")
    return Bundle(product=product, issued=resolved, valid_start=start, valid_end=end,
                  era="early", contours=tuple(contours))


# ------------------------------------------------------------------------ helpers


def _open(body: bytes) -> tuple[list[Any], list[list[Any]], list[str]]:
    """The shapes, records and field names from the .shp inside a zip."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise OutlookError(f"not a zip archive ({exc})") from exc
    names = archive.namelist()
    stems = {name.rsplit(".", 1)[0] for name in names if name.lower().endswith(".shp")}
    if len(stems) != 1:
        raise OutlookError(
            f"expected exactly one .shp in the archive, found {sorted(stems)} among {names}")
    stem = stems.pop()
    try:
        import shapefile
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency is declared
        raise OutlookError("reading a CPC outlook needs pyshp (see requirements.txt)") from exc
    try:
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{stem}.shp")),
            dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
            shx=io.BytesIO(archive.read(f"{stem}.shx")),
        )
    except KeyError as exc:
        raise OutlookError(f"the archive is missing a shapefile component: {exc}") from exc
    return list(reader.shapes()), [list(r) for r in reader.records()], \
        [f[0] for f in reader.fields[1:]]


def _rings(shape: Any) -> tuple[tuple[tuple[float, float], ...], ...]:
    """A polygon's rings as (lon, lat) tuples, split on `parts`.

    A CPC contour is routinely multi-part -- several disjoint regions, or an
    inner ring -- so testing only `points` treats the whole set as one ring and
    joins separate regions with a spurious edge.
    """
    points = [(float(x), float(y)) for x, y in shape.points]
    starts = list(shape.parts) or [0]
    bounds = list(starts) + [len(points)]
    rings = [tuple(points[bounds[index]:bounds[index + 1]])
             for index in range(len(bounds) - 1)]
    return tuple(ring for ring in rings if len(ring) >= 3)


def _in_ring(x: float, y: float, ring: tuple[tuple[float, float], ...]) -> bool:
    """Ray-casting point-in-polygon for one ring."""
    inside = False
    count = len(ring)
    for index in range(count):
        x0, y0 = ring[index]
        x1, y1 = ring[(index + 1) % count]
        if (y0 > y) != (y1 > y):
            crossing = x0 + (y - y0) / (y1 - y0) * (x1 - x0)
            if x < crossing:
                inside = not inside
    return inside


def _anomaly(probability: float, category: str) -> float:
    """Signed probability anomaly against climatology.

    CPC maps the leading tercile only, so a raw probability is not comparable
    across categories: 60% Below is the opposite of 60% Above. Above is positive,
    Below negative, and Normal is zero because a near-normal forecast says
    nothing about direction.
    """
    if category == "Normal":
        return 0.0
    excess = probability - 100.0 / 3.0
    return excess if category == "Above" else -excess


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for pattern in ("%m/%d/%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise OutlookError(f"{value!r} is not a date this source writes")


def _valid_period(text: str, product: str) -> tuple[date, date]:
    """Split the early era's one combined "MM/DD/YYYY - MM/DD/YYYY" string."""
    halves = [half.strip() for half in text.split("-")]
    if len(halves) != 2:
        raise OutlookError(f"{product}: cannot split valid period {text!r} into two dates")
    return _as_date(halves[0]), _as_date(halves[1])


def best(contours: Iterable[Contour], lat: float, lon: float) -> Contour | None:
    """The innermost contour containing a point, or None for no coverage.

    None means near-climatological odds, NOT missing data -- the caller should
    read it as a zero anomaly rather than dropping the point, or every quiet
    forecast disappears from the sample and only extremes remain.
    """
    containing = [contour for contour in contours if contour.contains(lat, lon)]
    if not containing:
        return None
    return max(containing, key=lambda contour: contour.probability)
