"""Archive Our World in Data crop-yield tables, one slug per key.

    PYTHONPATH=src python -m pipeline fetch --source owid_yields

One file per grapher slug, and the slugs come from config/sources.yaml's
params.slugs (crop -> slug), so adding a crop's yield series is a config change.
The whole table is fetched every run: it is a few tens of kilobytes, FAO revises
history, and a restatement is only visible if it is archived beside the previous
numbers.

publication_lag_days is null for this source, and this module records NO
publication date at all. config/sources.yaml used to expect the CSV's HTTP
Last-Modified to be the one available signal; a probe on 2026-09-27 showed it is
a CDN artefact -- last-modified was 14 seconds before the response Date, with
`age: 14` and `cf-cache-status: HIT`, i.e. when the edge cached the body, not
when anyone published anything. It is kept in params as evidence and is not
promoted to a date. The consequence is deliberate: pipeline.calendar.as_of drops
rows with no publication date, so this table cannot be used as a point-in-time
input, which is correct -- it validates the weather index against realised
harvests and was never a trading signal.
"""

from __future__ import annotations

from ..cli import FetchRequest
from ..clients import owid_yields as client
from ..http import parse_last_modified
from ..storage import RawRecord
from ._common import pause, planned


def fetch(request: FetchRequest) -> list[RawRecord]:
    slugs = {str(crop): str(slug) for crop, slug in request.spec.params["slugs"].items()}
    configured = set(request.geography.names())
    unknown = sorted(set(slugs) - configured)
    if unknown:
        print(f"  note: params.slugs names {', '.join(unknown)}, which "
              f"config/geography.yaml does not describe; fetching anyway")
    if request.years or request.months or request.states:
        print("  note: this source has no scope filters; the table is annual and global")

    records: list[RawRecord] = []
    for crop, slug in slugs.items():
        if request.dry_run:
            records.append(planned(request.raw, request.spec.name, slug,
                                   client.url(request.spec, slug)))
            continue
        body, headers, target = client.table(request.spec, slug)
        rows, column = client.parse(body, url=target)
        years = sorted({int(row["year"]) for row in rows if (row.get("year") or "").strip()})
        stamp = parse_last_modified(headers)
        records.append(request.raw.save(
            request.spec.name, slug, body,
            url=target,
            params={"crop": crop, "slug": slug, "value_column": column, "rows": len(rows),
                    "entities": len({row["entity"] for row in rows}),
                    "first_year": years[0] if years else None,
                    "last_year": years[-1] if years else None,
                    "etag": headers.get("etag"),
                    # Evidence, not a vintage: this tracks the CDN, not a release.
                    "http_last_modified": stamp.isoformat() if stamp else None,
                    "http_age": headers.get("age")},
            publication_date=None,
            version_stamp=f"{column} rows={len(rows)} "
                          f"years={years[0] if years else '?'}..{years[-1] if years else '?'}",
        ))
        pause()
    return records
