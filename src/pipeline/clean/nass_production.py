"""Archived Quick Stats responses to one county production table, offline.

    PYTHONPATH=src python -m pipeline clean --source nass_production
        ->  nass_county_production

One row per county per year, plus the production share of its state-year, which is
what config/geography.yaml's empty `counties` list is waiting for. The run prints
the largest counties in a paste-ready shape so the weights can be moved into
config by hand rather than generated behind anyone's back -- a weighting scheme is
a research decision, not a side effect of a clean step.

Two things are excluded on purpose:

  * "other (combined) counties" rows, which are a remainder rather than a place.
    Weighted as a county, one would invent a large phantom producer.
  * Suppressed values -- (D) withheld for disclosure and friends -- which are kept
    as rows with a null quantity and a `suppression` flag, never as zero. A
    suppressed county is a county with production that NASS will not print, and
    reading it as zero moves its weight to its neighbours.

UNVERIFIED, like its client: no live response was available when this was written.
"""

from __future__ import annotations

import pandas as pd

from ..calendar import PUBLICATION_COLUMN
from ..cli import CleanRequest
from ..clients import nass_production as client
from ._common import archived

TABLE = "nass_county_production"


def clean(request: CleanRequest) -> list:
    rows: list[dict] = []
    files = 0
    for record, body in archived(request.raw, request.spec.name):
        for entry in client.parse(body, url=str(record.path)):
            rows.append({
                "fips": entry.fips, "state": entry.state_alpha, "county": entry.county_name,
                "year": entry.year, "production": entry.quantity, "unit": entry.unit,
                "suppression": entry.suppression, "is_county": entry.is_county,
                PUBLICATION_COLUMN: entry.load_time,
            })
        files += 1

    if not files:
        raise SystemExit(
            "nass_production: nothing archived yet -- set NASS_API_KEY and run "
            "`python -m pipeline fetch --source nass_production --years 2024` first"
        )

    table = pd.DataFrame(rows)
    table[PUBLICATION_COLUMN] = pd.to_datetime(table[PUBLICATION_COLUMN])
    aggregates = int((~table["is_county"]).sum())
    table = table[table["is_county"]].drop(columns="is_county")
    table = (table.drop_duplicates(subset=["fips", "year"], keep="last")
             .sort_values(["year", "fips"]).reset_index(drop=True))

    # Share of the state-year, which is what a weight is. Computed on the printed
    # figures only: a suppressed county cannot contribute to the denominator, so
    # the shares of a state with suppressions do not sum to the state's true total.
    totals = table.groupby(["state", "year"])["production"].transform("sum")
    table["state_share"] = table["production"] / totals

    suppressed = int(table["production"].isna().sum())
    print(f"  {len(table)} county-years from {files} file(s), {table['fips'].nunique()} counties "
          f"in {table['state'].nunique()} state(s), {int(table['year'].min())}.."
          f"{int(table['year'].max())}, unit {'/'.join(sorted(table['unit'].dropna().unique()))}")
    if aggregates:
        print(f"    {aggregates} 'other counties' aggregate row(s) excluded")
    if suppressed:
        print(f"    {suppressed} county-year(s) suppressed by NASS, kept as null (never zero)")
    _suggest_weights(table)
    if request.dry_run:
        print(f"  would write {request.processed.path(TABLE)}")
        return []
    return [request.processed.write(TABLE, table)]


def _suggest_weights(table: pd.DataFrame, top: int = 10) -> None:
    """Print the largest counties of the latest year in config/geography.yaml's shape.

    Printed, not written: which counties to carry, and whether to weight on one
    year or an average of several, changes what the index measures.
    """
    latest = table[table["year"] == table["year"].max()].dropna(subset=["production"])
    if latest.empty:
        return
    national = latest["production"].sum()
    print(f"\n  largest counties of {int(latest['year'].iloc[0])}, as config/geography.yaml "
          f"crops.corn.counties entries (weight = share of these {len(latest)} counties):")
    for _, row in latest.nlargest(top, "production").iterrows():
        weight = row["production"] / national
        print(f"    - {{fips: \"{row['fips']}\", name: \"{row['county'].title()} County, "
              f"{row['state']}\", weight: {weight:.4f}}}")
    print(f"  ({min(top, len(latest))} of {len(latest)} counties shown; weights are normalised "
          f"at use, so a "
          f"partial list is fine -- but weights are all-or-nothing within the list)")
