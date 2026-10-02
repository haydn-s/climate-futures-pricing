"""Archive CPC 6-10 and 8-14 day outlooks, one issuance per key.

    PYTHONPATH=src python -m pipeline fetch --source cpc_outlook --years 2012
    PYTHONPATH=src python -m pipeline fetch --source cpc_outlook --dry-run

THE PLAN COMES FROM THE ARCHIVE'S OWN LISTING, not from a calendar. Issuances are
near-daily but skip weekends and holidays unevenly, so generating dates would make
a 404 out of most of them. One listing request resolves what exists, and the plan
is printed with its size before anything is downloaded.

THE ERA RULE, which is the one thing to get right here. The schema changed in
September 2012 and the two naming conventions OVERLAP from 2012-09-11 to
2012-10-09, so both exist for that month and taking both counts it twice. The
rule is a strict cut at `unified_from`: before it, the early pair (abv and bel,
two files per issuance, category in the filename); from it onwards, the single
unified file. Dropping the early era instead would lose 189 issuances including
half of 2012, the year every result in this project turns on.

Publication is exact rather than estimated: a forecast's issuance date is in its
filename and in its attributes, and the two are checked against each other.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

from ..cli import FetchRequest
from ..clients import cpc_outlook as client
from ..config import Geography
from ..http import FetchError
from ..storage import RawRecord
from ._common import pause, planned, resolve_years

FIRST_YEAR = 2011
DEFAULT_START_YEAR = 2011
# Observed mean size of one issuance, for the plan's estimate (~110 KB unified,
# ~9 KB early); only used to warn before a large pull.
APPROX_BYTES = 110_000


def fetch(request: FetchRequest) -> list[RawRecord]:
    params = request.spec.params
    unified_from = date.fromisoformat(str(params["unified_from"]))
    years = resolve_years(request.years, first=FIRST_YEAR, last=request.today.year,
                          default_start=DEFAULT_START_YEAR)
    months = tuple(request.months) or _months(request.geography)
    if request.states:
        print("  note: --states does not narrow this source; every outlook covers the whole CONUS")

    available = client.index(request.spec)
    plan: list[tuple[str, str, date]] = []
    for (product, suffix), dates in sorted(available.items()):
        for issued in dates:
            if issued.year not in years or issued.month not in months:
                continue
            # The strict cut that keeps the overlapping month from being counted
            # once per era.
            if suffix and issued >= unified_from:
                continue
            if not suffix and issued < unified_from:
                continue
            plan.append((product, suffix, issued))
    plan.sort(key=lambda item: (item[2], item[0], item[1]))

    _announce(plan, request.dry_run)

    records: list[RawRecord] = []
    for product, suffix, issued in plan:
        key = _key(product, suffix, issued)
        target = client.url(request.spec, product, issued, suffix=suffix)
        if request.dry_run:
            records.append(planned(request.raw, request.spec.name, key, target))
            continue
        if not request.force and request.raw.has(request.spec.name, key):
            continue
        try:
            body, _headers, target = client.download(
                request.spec, product, issued, suffix=suffix)
        except FetchError as exc:
            if exc.status == 404:
                # The listing said it was there; a 404 now means the archive moved
                # under us. Say which one and keep going rather than losing the run.
                print(f"  {key}: listed but 404 on fetch, skipping")
                continue
            raise
        category = client.EARLY_CATEGORY.get(suffix) if suffix else None
        bundle = client.read(body, product=product, issued=issued, category=category)
        _check(bundle, product, suffix, issued, request.spec)
        records.append(request.raw.save(
            request.spec.name, key, body,
            url=target,
            params={"product": product, "era": bundle.era,
                    "category": category, "suffix": suffix or None,
                    "issued": bundle.issued.isoformat(),
                    "valid_start": bundle.valid_start.isoformat(),
                    "valid_end": bundle.valid_end.isoformat(),
                    "lead_days": bundle.lead_days,
                    "contours": len(bundle.contours)},
            # Exact, not estimated: a forecast is public on the day it is issued.
            publication_date=bundle.issued,
        ))
        pause()
    return records


def _key(product: str, suffix: str, issued: date) -> str:
    """One key per product-era-issuance. The suffix stays in the key so the early
    era's two files do not collide on the same date."""
    family = f"{product}{suffix}" if suffix else product
    return f"{family}/{issued.isoformat()}"


def _months(geography: Geography) -> tuple[int, ...]:
    """The sensitive months of every crop with US states.

    This source covers the CONUS only, so a crop described by points abroad must
    not widen a fetch it cannot be read for.
    """
    months: set[int] = set()
    for crop in geography:
        if crop.states:
            months.update(crop.sensitive_months.months())
    return tuple(sorted(months)) or tuple(range(1, 13))


def _check(bundle: client.Bundle, product: str, suffix: str, issued: date,
           spec) -> None:
    """Two consistency checks the file can fail without erroring.

    The filename date and the attribute date must agree -- if they ever diverge,
    one of them is not the issuance and the publication date is wrong, which
    silently breaks every point-in-time claim built on it. And the lead time must
    match the product: a 610 file whose valid period starts eight days out is an
    814 under the wrong name.
    """
    if bundle.issued != issued:
        raise ValueError(
            f"{product}{suffix} {issued}: the filename says {issued} but Fcst_Date says "
            f"{bundle.issued}; one of them is not the issuance date")
    leads = spec.params.get("leads", {})
    expected = leads.get(product[:3])
    if expected:
        low, high = int(expected[0]), int(expected[1])
        actual_start = (bundle.valid_start - bundle.issued).days
        actual_end = (bundle.valid_end - bundle.issued).days
        if (actual_start, actual_end) != (low, high):
            raise ValueError(
                f"{product}{suffix} {issued}: expected a lead of {low}-{high} days, "
                f"found {actual_start}-{actual_end}")
    if not bundle.contours:
        raise ValueError(f"{product}{suffix} {issued}: no contours in the file")


def _announce(plan: Iterable[tuple[str, str, date]], dry_run: bool) -> None:
    items = list(plan)
    if not items:
        print("cpc_outlook: nothing in scope")
        return
    products = sorted({product for product, _, _ in items})
    early = sum(1 for _, suffix, _ in items if suffix)
    span = f"{items[0][2]} .. {items[-1][2]}"
    size = len(items) * APPROX_BYTES / 1_048_576
    verb = "would fetch" if dry_run else "fetching"
    print(f"  {verb} {len(items)} issuance(s) of {', '.join(products)}, {span}, "
          f"about {size:.0f} MB ({early} from the pre-2012-09 schema)")
