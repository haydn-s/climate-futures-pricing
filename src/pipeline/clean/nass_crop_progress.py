"""Normalize archived USDA Crop Progress text releases into long observations."""

from __future__ import annotations

from dataclasses import asdict

import pandas as pd

from ..cli import CleanRequest
from ..clients import nass_crop_progress as client
from ._common import archived

TABLE = "nass_crop_progress_weekly"


def clean(request: CleanRequest) -> list[str]:
    rows = []
    failures = []
    empty_documents: dict[str, int] = {}
    for record, body in archived(request.raw, request.spec.name):
        try:
            observations = client.parse_report(
                body, expected_release=record.publication_date, url=record.url
            )
        except client.CropProgressError as exc:
            failures.append(f"{record.key}: {exc}")
            continue
        if not observations:
            kind = str(record.params.get("document_type") or "no_corn_rows")
            empty_documents[kind] = empty_documents.get(kind, 0) + 1
            continue
        rows.extend({**asdict(item), "source_sha256": record.sha256}
                    for item in observations)
    if not rows:
        detail = f"; first error: {failures[0]}" if failures else ""
        raise SystemExit(
            "nass_crop_progress: nothing readable is archived -- run fetch first" + detail
        )
    table = pd.DataFrame(rows)
    identity = [
        "release_date", "measure", "stage", "region", "comparison", "category",
    ]
    duplicates = table.duplicated(identity, keep=False)
    if duplicates.any():
        sample = table.loc[duplicates, identity].iloc[0].to_dict()
        raise SystemExit(f"nass_crop_progress: duplicate observation identity: {sample}")
    table = table.sort_values(identity, ignore_index=True)
    if request.dry_run:
        print(f"  would write {len(table)} observations from "
              f"{table['release_date'].nunique()} release(s)")
        return []
    path = request.processed.write(TABLE, table)
    print(
        f"  {len(table)} corn observations from {table['release_date'].nunique()} "
        f"release(s), {table['release_date'].min()}..{table['release_date'].max()}"
    )
    print(
        f"    {table['region'].nunique()} region(s), "
        f"progress stages={sorted(table.loc[table['measure'] == 'progress', 'stage'].unique())}"
    )
    if failures:
        print(f"    {len(failures)} unreadable release(s); first: {failures[0]}")
    if empty_documents:
        print(f"    archived documents without corn rows: {empty_documents}")
    return [str(path)]
