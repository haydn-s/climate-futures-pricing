"""Turn archived CPC outlooks into one row per point per issuance.

    PYTHONPATH=src python -m pipeline clean --source cpc_outlook

Writes `cpc_outlook_point_daily`: for every crop point and every archived
issuance, the probability contour covering that point, as a signed anomaly
against climatology.

TWO ABSENCES THAT MEAN DIFFERENT THINGS, and conflating them is the trap here.

* A point inside no contour is a real forecast of near-climatological odds. Its
  anomaly is 0.0 and it belongs in the table. Dropping these would leave only
  the weeks CPC drew a contour over, i.e. only the forecasts that said something,
  which turns "the forecast was unremarkable" into "there was no forecast" and
  biases every average towards the extremes.
* A point OUTSIDE THE FORECAST DOMAIN has no forecast at all. CPC covers the
  CONUS, so cocoa's West African points fall outside every contour for the same
  reason an unremarkable Iowa does -- and `best()` cannot tell them apart. Read
  naively, Cote d'Ivoire would acquire 4,900 confident forecasts of normal
  weather. Points outside the domain are therefore excluded here by an explicit
  bounds check and reported, not silently zeroed.

A crop with US states but no points (soybeans before its points were added,
wheat still) cannot be read by a point-in-polygon source and is reported rather
than quietly contributing nothing.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..cli import CleanRequest
from ..clients import cpc_outlook as client
from ..config import CropGeography
from ._common import archived

TABLE = "cpc_outlook_point_daily"
PUBLICATION_COLUMN = "publication_date"

# The CONUS, generously bounded. Only used to tell "no contour here" apart from
# "this source does not cover this continent"; it is not a precision mask.
CONUS_LAT = (24.0, 50.0)
CONUS_LON = (-125.0, -66.0)


def clean(request: CleanRequest) -> list[str]:
    points = list(_points(request.geography))
    if not points:
        raise SystemExit(
            "cpc_outlook: no crop in config/geography.yaml declares points inside the "
            "CONUS, and this source is a point-in-polygon read of US forecast maps. "
            "Add points to a crop that has US states.")

    rows: list[dict] = []
    unreadable: list[str] = []
    for record, body in archived(request.raw, request.spec.name):
        product = str(record.params.get("product") or record.key.split("/")[0])
        category = record.params.get("category")
        try:
            bundle = client.read(body, product=product,
                                 issued=_issued(record), category=category)
        except client.OutlookError as exc:
            unreadable.append(f"{record.key}: {exc}")
            continue
        for crop, point in points:
            contour = client.best(bundle.contours, point.lat, point.lon)
            rows.append({
                "crop": crop.name,
                "point": point.name,
                "lat": point.lat,
                "lon": point.lon,
                "product": bundle.product,
                "variable": "temp" if "temp" in bundle.product else "prcp",
                "horizon": bundle.product[:3],
                "issued": pd.Timestamp(bundle.issued),
                "valid_start": pd.Timestamp(bundle.valid_start),
                "valid_end": pd.Timestamp(bundle.valid_end),
                "lead_days": bundle.lead_days,
                # No contour over a CONUS point is a forecast of normal odds.
                "category": contour.category if contour else "Normal",
                "probability": contour.probability if contour else None,
                "anomaly": contour.anomaly if contour else 0.0,
                "covered": contour is not None,
                "era": bundle.era,
                PUBLICATION_COLUMN: pd.Timestamp(bundle.issued),
            })

    if not rows:
        raise SystemExit("cpc_outlook: nothing archived yet -- run fetch first")

    table = pd.DataFrame(rows)
    # The early era splits one issuance across an above file and a below file, so
    # a point gets two rows for the same product-day. Keep the one that actually
    # says something: the larger absolute anomaly. A tie means both said normal.
    table["_rank"] = table["anomaly"].abs()
    table = (table.sort_values(["crop", "point", "product", "issued", "_rank"])
             .drop_duplicates(subset=["crop", "point", "product", "issued"], keep="last")
             .drop(columns="_rank")
             .sort_values(["crop", "point", "product", "issued"], ignore_index=True))

    path = request.processed.write(TABLE, table)
    covered = int(table["covered"].sum())
    print(f"  {len(table)} point-forecasts from {table['issued'].nunique()} issuance(s), "
          f"{table['crop'].nunique()} crop(s) x {table['point'].nunique()} point(s), "
          f"{', '.join(sorted(table['product'].unique()))}, "
          f"{table['issued'].min().date()}..{table['issued'].max().date()}")
    print(f"    {covered} inside a contour, {len(table) - covered} at climatological odds "
          f"(anomaly 0, which is a forecast and not a gap)")
    eras = table.groupby("era").size().to_dict()
    print(f"    eras: {eras}")
    if unreadable:
        print(f"    {len(unreadable)} unreadable file(s): {unreadable[0]}"
              + (f" (+{len(unreadable) - 1} more)" if len(unreadable) > 1 else ""))
    return [str(path)]


def _points(geography) -> list[tuple[CropGeography, object]]:
    """Every crop point inside the CONUS, with the crops that cannot be read named."""
    usable: list[tuple[CropGeography, object]] = []
    outside: list[str] = []
    pointless: list[str] = []
    for crop in geography:
        if not crop.points:
            if crop.states:
                pointless.append(crop.name)
            continue
        for point in crop.points:
            if (CONUS_LAT[0] <= point.lat <= CONUS_LAT[1]
                    and CONUS_LON[0] <= point.lon <= CONUS_LON[1]):
                usable.append((crop, point))
            else:
                outside.append(f"{crop.name}/{point.name}")
    if outside:
        print(f"  skipping {len(outside)} point(s) outside the CONUS forecast domain: "
              f"{', '.join(outside)} -- outside every contour for a different reason "
              f"than an unremarkable forecast, and must not read as normal odds")
    if pointless:
        print(f"  note: {', '.join(pointless)} declare US states but no points, so this "
              f"point-in-polygon source cannot be read for them")
    return usable


def _issued(record) -> date | None:
    stamp = record.params.get("issued")
    if stamp:
        return date.fromisoformat(str(stamp))
    tail = record.key.split("/")[-1]
    try:
        return date.fromisoformat(tail)
    except ValueError:
        return None
