"""Archive NASA POWER daily point data, one crop-point-year per key.

    PYTHONPATH=src python -m pipeline fetch --source nasa_power
    PYTHONPATH=src python -m pipeline fetch --source nasa_power --years 2012 --dry-run

The points come from config/geography.yaml, so this fetches whatever crops
declare them -- cocoa's four West African locations today, and the corn belt too
if points are ever added back to corn. A crop described only by US states is
skipped: nClimGrid covers those far better than a single point can.

A point-year is the archiving unit so that a reanalysis reprocessing of one year
shows up as a new version of that year, instead of being buried inside a
re-fetched 26-year blob. A finished year that came back complete is never
re-fetched; the year in progress always is, because it grows by a day at a time.

Expect the CURRENT year to gain a version on every run, and not only from the new
day: POWER serializes header.sources in an unstable order, so the bytes differ
between two otherwise identical responses. The bytes are archived verbatim anyway
-- that is the point of the archive -- and the version STAMP is what to compare,
because pipeline.clients.nasa_power sorts the source list before building it.
(pipeline.ingest.usdm takes the other route and re-serialises sorted, because
there the row order is the only thing that flaps and the numbers are final.)
"""

from __future__ import annotations

from datetime import date, timedelta

from ..cli import FetchRequest
from ..clients import nasa_power as client
from ..config import CropGeography, PointRef
from ..storage import RawRecord
from ._common import pause, planned, resolve_years, slug


def fetch(request: FetchRequest) -> list[RawRecord]:
    params = request.spec.params
    first = date.fromisoformat(str(params["first_date"]))
    years = resolve_years(request.years, first=first.year, last=request.today.year,
                          default_start=int(params.get("default_start_year", 2000)))
    places = [(crop, point) for crop in request.geography for point in crop.points]
    if not places:
        raise SystemExit(
            "nasa_power: no crop in config/geography.yaml declares points. This source is "
            "point-based; add points to a crop (see the cocoa entry) to fetch for it."
        )
    if request.states:
        print("  note: --states does not narrow this source; it is keyed on points, not states")

    records: list[RawRecord] = []
    for crop, point in places:
        for year in years:
            key = f"{crop.name}/{slug(point.name)}/{year}"
            start = max(first, date(year, 1, 1))
            # Clamped to today so the URL says what we actually want. The service
            # would clamp it anyway, but it pads the difference with fill values
            # rather than dropping it, which is not the same thing.
            end = min(date(year, 12, 31), request.today)
            if end < start:
                continue

            if request.dry_run:
                records.append(planned(
                    request.raw, request.spec.name, key,
                    client.url(request.spec, lat=point.lat, lon=point.lon, start=start, end=end),
                ))
                continue
            previous = request.raw.latest(request.spec.name, key)
            if (not request.force and previous is not None
                    and previous.params.get("complete") and year < request.today.year):
                continue

            answer, body, target = client.daily(
                request.spec, lat=point.lat, lon=point.lon, start=start, end=end,
            )
            records.append(request.raw.save(
                request.spec.name, key, body,
                url=target,
                params=_params(crop, point, answer, start, end),
                publication_date=_published(request, answer),
                version_stamp=answer.stamp,
            ))
            pause()
    return records


def _params(crop: CropGeography, point: PointRef, answer: client.PowerDaily,
            start: date, end: date) -> dict:
    """What the manifest records about a point-year besides the bytes.

    `complete` is the skip signal for later runs and is deliberately strict: the
    year is complete only when real values reach its last day, never merely
    because the response covered the window that was asked for.
    """
    return {
        "crop": crop.name,
        "point": point.name,  # the readable original; the key carries the slug
        "country": point.country,
        "requested_lat": point.lat,
        "requested_lon": point.lon,
        "elevation_m": answer.elevation,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "header_end": answer.header_end,
        "last_observed": answer.last_observed.isoformat() if answer.last_observed else None,
        "complete": bool(answer.last_observed and answer.last_observed >= date(start.year, 12, 31)),
        "api_version": answer.api_version,
        "sources": list(answer.sources),
        "units": dict(answer.units),
        "time_standard": answer.time_standard,
    }


def _published(request: FetchRequest, answer: client.PowerDaily) -> date | None:
    """When the whole point-year became public: its last real day plus the source's lag.

    The lag is an observed 3-5 days rather than a schedule, and
    config/sources.yaml records why 5 was chosen and which way it biases this
    project's timing claim. Note the file's own header.end cannot be used here: it
    is clamped to the server's today, not to the last day that has data.
    """
    if answer.last_observed is None:
        return None
    lag = request.spec.publication_lag_days or 0
    return answer.last_observed + timedelta(days=lag)
