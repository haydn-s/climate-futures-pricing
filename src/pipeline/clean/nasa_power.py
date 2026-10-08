"""Archived NASA POWER responses to one daily point table, offline.

    PYTHONPATH=src python -m pipeline clean --source nasa_power  ->  nasa_power_point_daily

One row per point per day, with the fill value already NaN and units in the
column names (config/sources.yaml maps tmax_c -> T2M_MAX and precip_mm ->
PRECTOTCORR, so renaming a column is a config change, not a code change).

The publication date is computed per row as the day plus the source's lag, which
is the one place this pipeline asserts something POWER does not tell it: there is
no Last-Modified, no ETag and no version document on the endpoint, so the lag in
config/sources.yaml -- an observed 3-5 days, recorded as 5 -- is the assumption
carrying every as-of filter over this table. The api_version and source_model
columns are kept so a row can be traced to the reanalysis that produced it.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from ..calendar import PUBLICATION_COLUMN
from ..cli import CleanRequest
from ..clients import nasa_power as client
from ._common import archived

TABLE = "nasa_power_point_daily"


def clean(request: CleanRequest) -> list:
    # column name -> POWER parameter, e.g. {"tmax_c": "T2M_MAX"}
    variables = {str(name): str(parameter)
                 for name, parameter in request.spec.params["variables"].items()}
    lag = request.spec.publication_lag_days or 0

    rows: list[dict] = []
    files = 0
    for record, body in archived(request.raw, request.spec.name):
        answer = client.parse(body, url=str(record.path))
        files += 1
        missing = [name for name, parameter in variables.items() if parameter not in answer.series]
        if missing:
            raise SystemExit(
                f"nasa_power: {record.key} has no series for "
                f"{', '.join(variables[name] for name in missing)}; config/sources.yaml maps "
                f"{variables} and the file holds {sorted(answer.series)}. Re-fetch with --force "
                f"after changing params.variables."
            )
        for day in sorted(next(iter(answer.series.values()))):
            rows.append({
                "crop": record.params.get("crop", ""),
                "point": record.params.get("point", record.key.split("/")[1]),
                "country": record.params.get("country", ""),
                "lat": answer.latitude,
                "lon": answer.longitude,
                "date": day,
                **{name: answer.series[parameter][day] for name, parameter in variables.items()},
                PUBLICATION_COLUMN: day + timedelta(days=lag),
                "api_version": answer.api_version,
                "source_model": "+".join(answer.sources),
            })

    if not files:
        raise SystemExit(
            "nasa_power: nothing archived yet -- run "
            "`python -m pipeline fetch --source nasa_power --years 2012` first"
        )

    table = pd.DataFrame(rows)
    table["date"] = pd.to_datetime(table["date"])
    table[PUBLICATION_COLUMN] = pd.to_datetime(table[PUBLICATION_COLUMN])
    # A point-year is one key, so duplicates only appear when the same day is
    # covered twice; keep the last read, which is the most recently fetched file.
    before = len(table)
    table = (table.drop_duplicates(subset=["crop", "point", "date"], keep="last")
             .sort_values(["crop", "point", "date"]).reset_index(drop=True))
    duplicates = before - len(table)

    # Drop the padded tail, as pipeline.clean.nclimgrid drops a short month's
    # unused day slots: a day with no value in any variable is not an observation,
    # and keeping it would put rows with a FUTURE publication date in the table.
    unobserved = table[list(variables)].isna().all(axis=1)
    padded = int(unobserved.sum())
    table = table[~unobserved].reset_index(drop=True)

    print(f"  {len(table)} point-days from {files} file(s), "
          f"{table['point'].nunique()} point(s) in {table['crop'].nunique()} crop(s), "
          f"{table['date'].min().date()}..{table['date'].max().date()}, "
          f"publication = day + {lag}d")
    if padded:
        print(f"    {padded} day(s) dropped as entirely unobserved -- POWER pads the tail of "
              f"a window with {int(request.spec.params['fill_value'])} rather than shortening it")
    if duplicates:
        print(f"    {duplicates} duplicated point-day(s) de-duplicated")
    if request.dry_run:
        print(f"  would write {request.processed.path(TABLE)}")
        return []
    return [request.processed.write(TABLE, table)]
