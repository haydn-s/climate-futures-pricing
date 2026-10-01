"""Archive USDA NASS county production, one state-year per key.

    export NASS_API_KEY=...          # free, from https://quickstats.nass.usda.gov/api
    PYTHONPATH=src python -m pipeline fetch --source nass_production --years 2024

THIS SOURCE NEEDS A KEY and it is the only one that does. The CLI resolves it
before dispatch, so an unset NASS_API_KEY fails with a message naming where to get
one rather than part-way through a run. The resolved URL is a secret -- the key
travels in the query string -- and nothing here logs or stores it unredacted.

It is also the only UNVERIFIED source in the pipeline: see
pipeline.clients.nass_production for the checklist of what to confirm on the first
real run. Nothing else depends on this source, so the rest of the pipeline works
with it unconfigured.

What it is for: the county production weights that config/geography.yaml holds an
empty `counties` list for. Until they exist, every state is weighted equally,
which is what the current analysis does and says.
"""

from __future__ import annotations

from ..cli import FetchRequest
from ..clients import nass_production as client
from ..config import Geography
from ..http import redact_url
from ..storage import RawRecord
from ._common import pause, planned, resolve_years

# Quick Stats county survey data does not reach back to 2000 for every state, but
# the API answers with zero records rather than an error, so the floor here is
# deliberately loose and a missing state-year is reported, not fatal.
FIRST_YEAR = 1997


def fetch(request: FetchRequest) -> list[RawRecord]:
    if not request.api_key:
        # Belt and braces: the CLI resolves the key before dispatch, so reaching
        # here without one would mean the contract changed.
        raise SystemExit(
            f"nass_production needs a key. Request a free one at "
            f"{request.spec.api_key_signup_url}, then export {request.spec.api_key_env}."
        )
    states = _states(request.geography, request.states)
    if not states:
        raise SystemExit("nass_production: no US states in scope; this source is county-level US only")
    years = resolve_years(request.years, first=FIRST_YEAR, last=request.today.year,
                          default_start=request.today.year - 5)
    if request.months:
        print("  note: --months does not narrow this source; production is annual")

    records: list[RawRecord] = []
    for postal in states:
        for year in years:
            key = f"{postal}/{year}"
            if request.dry_run:
                # The planned URL would carry the key, so it is redacted for display.
                records.append(planned(request.raw, request.spec.name, key, redact_url(
                    client.url(request.spec, api_key=request.api_key, state_alpha=postal, year=year)
                )))
                continue
            if request.raw.has(request.spec.name, key) and not request.force:
                continue

            try:
                rows, body, safe_url = client.records(
                    request.spec, api_key=request.api_key, state_alpha=postal, year=year,
                )
            except client.NassResponseError as exc:
                # A state-year with no county survey is normal and must not stop a run
                # over the other 24; anything else is re-raised.
                if "zero records" in str(exc):
                    print(f"  {postal} {year}: no county records, skipping")
                    continue
                raise
            counties = [row for row in rows if row.is_county]
            suppressed = [row for row in counties if row.quantity is None]
            load_times = [row.load_time for row in rows if row.load_time]
            records.append(request.raw.save(
                request.spec.name, key, body,
                url=safe_url,  # already redacted
                params={"state_alpha": postal, "year": year, "records": len(rows),
                        "counties": len(counties), "suppressed": len(suppressed),
                        "aggregate_rows": len(rows) - len(counties),
                        "unit": counties[0].unit if counties else None,
                        "load_time": max(load_times).isoformat() if load_times else None},
                # load_time is the only release signal the API is documented to
                # return. publication_lag_days is null for this source precisely
                # because the real lag is report-specific -- monthly in season, the
                # Annual Summary in January -- so nothing is computed here.
                publication_date=max(load_times) if load_times else None,
                version_stamp=f"{postal} {year} records={len(rows)} counties={len(counties)} "
                              f"suppressed={len(suppressed)}",
            ))
            pause()
    return records


def _states(geography: Geography, requested) -> list[str]:
    wanted = {code.upper() for code in requested}
    codes: dict[str, None] = {}
    for crop in geography:
        for state in crop.states:
            if not wanted or state.postal in wanted:
                codes.setdefault(state.postal, None)
    return list(codes)
