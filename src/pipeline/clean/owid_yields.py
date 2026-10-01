"""Archived OWID tables to one annual yield table, offline.

    PYTHONPATH=src python -m pipeline clean --source owid_yields  ->  owid_yields_annual

One row per crop per country per year. Two joins are made carefully:

  * The value column is taken by exclusion, never by name -- it is spelled
    differently in every dataset.
  * Entities are matched folded and accent-insensitive, because "Cote d'Ivoire"
    and "Cote d'Ivoire" with diacritics are the same country and OWID spells it
    both ways across datasets. The same prefix match the existing analysis uses,
    so the pipeline's numbers agree with the ones already published.

publication_date is EMPTY on every row, and that is the finding rather than a
gap: OWID serves no per-row release date, FAO revises history years later, and
the HTTP Last-Modified turns out to be a CDN cache timestamp (see
pipeline.ingest.owid_yields). Rows with no publication date are dropped by
pipeline.calendar.as_of, which is the correct outcome -- this source validates
the weather index against realised harvests and is not a point-in-time input.
The column is kept so the table has the same shape as every other one here.
"""

from __future__ import annotations

import pandas as pd

from ..calendar import PUBLICATION_COLUMN
from ..cli import CleanRequest
from ..clients import owid_yields as client
from ._common import archived, matches_any

TABLE = "owid_yields_annual"
VALUE = "yield_t_per_ha"


def clean(request: CleanRequest) -> list:
    slugs = {str(crop): str(slug) for crop, slug in request.spec.params["slugs"].items()}
    bodies = {record.key: (record, body) for record, body in archived(request.raw, request.spec.name)}
    if not bodies:
        raise SystemExit(
            "owid_yields: nothing archived yet -- run "
            "`python -m pipeline fetch --source owid_yields` first"
        )

    frames: list[pd.DataFrame] = []
    for crop_name, slug in slugs.items():
        if slug not in bodies:
            print(f"  note: {slug} not archived, skipping {crop_name}")
            continue
        record, body = bodies[slug]
        rows, column = client.parse(body, url=str(record.path))
        frame = pd.DataFrame(rows)
        wanted = _countries(request, crop_name)
        if wanted:
            keep = frame["entity"].map(lambda entity: matches_any(entity, wanted))
            dropped = int((~keep).sum())
            frame = frame[keep]
            if frame.empty:
                raise SystemExit(
                    f"owid_yields: none of {list(wanted)} matched an entity in {slug}. "
                    f"config/geography.yaml spells the countries for {crop_name}; the file "
                    f"offers e.g. {sorted(set(pd.DataFrame(rows)['entity']))[:6]}."
                )
            print(f"  {crop_name}: {len(frame)} rows for {', '.join(sorted(frame['entity'].unique()))} "
                  f"({dropped} other entities dropped)")
        frame = frame.rename(columns={column: VALUE})
        frame["crop"] = crop_name
        frame[PUBLICATION_COLUMN] = pd.to_datetime(record.publication_date)
        frames.append(frame)

    if not frames:
        raise SystemExit("owid_yields: no crop matched an archived slug")

    table = pd.concat(frames, ignore_index=True)
    table["year"] = pd.to_numeric(table["year"], errors="coerce").astype("Int64")
    table[VALUE] = pd.to_numeric(table[VALUE], errors="coerce")
    table = (table[["crop", "entity", "code", "year", VALUE, PUBLICATION_COLUMN]]
             .sort_values(["crop", "entity", "year"]).reset_index(drop=True))

    unpublished = int(table[PUBLICATION_COLUMN].isna().sum())
    print(f"  {len(table)} crop-country-years, {table['crop'].nunique()} crop(s), "
          f"{int(table['year'].min())}..{int(table['year'].max())}")
    if unpublished:
        print(f"    {unpublished} row(s) carry no publication date -- this source serves none, "
              f"so pipeline.calendar.as_of will drop them; use it for validation, not timing")
    if request.dry_run:
        print(f"  would write {request.processed.path(TABLE)}")
        return []
    return [request.processed.write(TABLE, table)]


def _countries(request: CleanRequest, crop_name: str) -> tuple[str, ...]:
    """The country names a crop declares, or () when it declares none.

    Falls back to no filter rather than to an empty result, so a crop described
    without countries still yields a table someone can look at.
    """
    if crop_name not in request.geography.names():
        return ()
    return request.geography.crop(crop_name).countries
