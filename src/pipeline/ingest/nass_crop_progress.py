"""Archive original USDA ESMIS Crop Progress text releases."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from ..cli import FetchRequest
from ..clients import nass_crop_progress as client
from ..storage import RawRecord
from ._common import planned, resolve_years

FIRST_YEAR = 1995
DEFAULT_START_YEAR = 2012
WORKERS = 4


def fetch(request: FetchRequest) -> list[RawRecord]:
    years = resolve_years(
        request.years, first=FIRST_YEAR, last=request.today.year,
        default_start=DEFAULT_START_YEAR,
    )
    if not years:
        raise SystemExit("nass_crop_progress: no requested years are in source scope")
    if request.dry_run:
        key = f"{years[0]}-{years[-1]}"
        return [planned(
            request.raw, request.spec.name, key,
            request.spec.format_url(page=0),
            params={"years": list(years), "discovery": "catalog crawl"},
            version_stamp="plan only; release URLs require catalog discovery",
        )]

    releases = _discover(request, set(years))
    wanted = [release for release in releases
              if release.release_date.year in years
              and (request.force or not request.raw.has(
                  request.spec.name, release.release_date.isoformat()))]
    records = []
    failures = []
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(
                client.report, release.url, expected_release=release.release_date
            ): release
            for release in wanted
        }
        for future in as_completed(futures):
            release = futures[future]
            try:
                body, observations = future.result()
            except Exception as exc:  # preserve successful files for a resumable retry
                failures.append(f"{release.release_date}: {exc}")
                continue
            rows = len(observations)
            document_type = client.document_type(body, observations)
            records.append(request.raw.save(
                request.spec.name,
                release.release_date.isoformat(),
                body,
                url=release.url,
                params={
                    "release_date": release.release_date.isoformat(),
                    "corn_observations": rows,
                    "document_type": document_type,
                },
                publication_date=release.release_date,
                version_stamp=(
                    f"released {release.release_date.isoformat()} "
                    f"type={document_type} rows={rows}"
                ),
            ))
    records.sort(key=lambda record: record.key)
    if failures:
        raise client.CropProgressError(
            f"{len(failures)} release(s) failed after {len(records)} were archived; "
            f"rerun to resume. First failure: {failures[0]}"
        )
    return records


def _discover(request: FetchRequest, years: set[int]) -> list[client.Release]:
    first = client.catalog(request.spec.format_url(page=0))
    releases = list(first.releases)
    for page in range(1, first.last_page + 1):
        found = client.catalog(request.spec.format_url(page=page))
        releases.extend(found.releases)
        if found.releases and min(item.release_date.year for item in found.releases) < min(years):
            break
    unique: dict[date, client.Release] = {}
    for release in releases:
        existing = unique.get(release.release_date)
        if existing is not None and existing.url != release.url:
            raise client.CropProgressError(
                f"catalog lists two text files for {release.release_date}"
            )
        unique[release.release_date] = release
    selected = sorted(
        (release for release in unique.values() if release.release_date.year in years),
        key=lambda item: item.release_date,
    )
    missing = sorted(year for year in years if not any(
        release.release_date.year == year for release in selected
    ))
    if missing:
        raise client.CropProgressError(
            "catalog has no Crop Progress release for year(s): "
            + ", ".join(map(str, missing))
        )
    return selected
