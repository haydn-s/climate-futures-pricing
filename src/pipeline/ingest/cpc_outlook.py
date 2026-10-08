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
# Mean size of one issuance, for the plan's estimate. MEASURED over a complete
# backfill, not sampled: an early guess of 110 KB from two files predicted
# 1,317 MB for a pull that turned out to be 4.4 GB, because contour counts and
# vertex density vary enormously with how active the forecast is. Only used to
# warn before a large pull, but a warning off by 3.3x is not much of one.
APPROX_BYTES = 360_000

# Statuses that mean "the listing offers it but the server will not serve it".
# 403 belongs here because this archive has per-file permission faults, not
# because a permission error is ever worth retrying -- it is deliberately NOT in
# pipeline.http.RETRY_STATUSES.
UNAVAILABLE_STATUSES = frozenset({403, 404})

# Consecutive refusals that stop the run rather than being skipped. Scattered
# refusals are an upstream fact; a run of them is this project being blocked, and
# the difference matters because quietly skipping thousands would produce an
# archive whose holes look like data.
BLOCKED_AFTER = 25


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
    unavailable = 0
    consecutive = 0
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
            if exc.status in UNAVAILABLE_STATUSES:
                # A file the listing offers but the server will not serve. 404
                # means it moved under us; 403 means its permissions are wrong
                # upstream, which happens per FILE and not per date -- verified
                # 2022-10-31, where 610temp, 814temp and 814prcp are all 403
                # while 610prcp the same day is 200 and the neighbouring days
                # are fine.
                unavailable += 1
                consecutive += 1
                print(f"  {key}: listed but HTTP {exc.status}, skipping")
                # SCATTERED permission errors are an upstream fact; a RUN of them
                # is this project being blocked, and skipping thousands of files
                # quietly would hand back an archive with holes that look like
                # data. Tell the two apart by how they arrive.
                if consecutive >= BLOCKED_AFTER:
                    raise SystemExit(
                        f"cpc_outlook: {consecutive} consecutive files refused "
                        f"(last {key}, HTTP {exc.status}). Scattered refusals are "
                        f"normal for this archive, a run of them is not -- this "
                        f"looks like being blocked rather than bad permissions. "
                        f"Stopping with {len(records)} archived this run; re-run "
                        f"later to resume.") from exc
                continue
            raise
        consecutive = 0
        category = client.EARLY_CATEGORY.get(suffix) if suffix else None
        leads = request.spec.params.get("leads", {}).get(product[:3])
        bundle = client.read(body, product=product, issued=issued, category=category,
                             leads=tuple(leads) if leads else None)
        problems = _check(bundle, product, suffix, issued, request.spec)
        records.append(request.raw.save(
            request.spec.name, key, body,
            url=target,
            params={"product": product, "era": bundle.era,
                    "category": category, "suffix": suffix or None,
                    "issued": bundle.issued.isoformat(),
                    "valid_start": bundle.valid_start.isoformat(),
                    "valid_end": bundle.valid_end.isoformat(),
                    "lead_days": bundle.lead_days,
                    "contours": len(bundle.contours),
                    # Self-describing: reading the archive by key shows the
                    # problem rather than handing back a plausible wrong product.
                    "anomalies": problems or None},
            # Exact, not estimated: a forecast is public on the day it is issued.
            publication_date=bundle.issued,
        ))
        pause()
    if unavailable:
        print(f"  {unavailable} file(s) listed but not served (403/404); "
              f"the archive is incomplete by that many and says so here")
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
           spec) -> list[str]:
    """Problems with a file, REPORTED rather than raised.

    These are upstream errors, not bugs here, and a single bad file must not kill
    a run of twelve thousand. Verified example: on 2018-07-30 CPC served the
    8-14 day precipitation map under the 6-10 day filename -- 610prcp carried a
    valid period of Aug 7-13 while 610temp the same day correctly carried Aug
    5-9, and 610prcp was right on the days either side. Raising on it stopped a
    backfill 5,444 files in.

    So the bytes are archived with whatever is wrong recorded alongside them, and
    the clean step refuses to build a row from a file whose lead time contradicts
    its own filename. Two layers, both loud: the archive keeps the evidence and
    the table stays honest. Treating an 8-14 day forecast as a 6-10 day one would
    silently corrupt the lead-time comparison, which is the reason both horizons
    are fetched at all.

    The two checks:

    * The filename date and Fcst_Date must agree. If they diverge, one of them is
      not the issuance, and the publication date is the only thing making this
      source point-in-time.
    * The lead must match the product. A 610 file whose valid period starts eight
      days out is an 814 under the wrong name.
    """
    problems: list[str] = []
    if bundle.issued != issued:
        problems.append(f"filename says {issued} but Fcst_Date says {bundle.issued}")
    expected = spec.params.get("leads", {}).get(product[:3])
    if expected and bundle.contours:
        low, high = int(expected[0]), int(expected[1])
        actual = ((bundle.valid_start - bundle.issued).days,
                  (bundle.valid_end - bundle.issued).days)
        if actual != (low, high):
            problems.append(
                f"expected a lead of {low}-{high} days, found {actual[0]}-{actual[1]}")
    # Not a problem: no polygons is CPC forecasting no significant departure
    # anywhere. Its dates were synthesised from the filename and the configured
    # leads, which is also why the lead check is skipped for it as tautological.
    if not bundle.contours:
        print(f"  {product}{suffix}/{issued}: no contours -- climatological odds "
              f"everywhere, recorded as a forecast")
    if problems:
        print(f"  {product}{suffix}/{issued}: UPSTREAM ANOMALY -- "
              f"{'; '.join(problems)}; archived but excluded from the table")
    return problems


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
