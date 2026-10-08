"""Talking to Our World in Data's grapher CSV endpoint.

One quirk shapes this module: the value column's name is different in every
dataset (`maize_yield` for maize, `cocoa_beans__00000661__yield__005412__
tonnes_per_hectare` for cocoa), so it can only be found BY EXCLUSION -- whatever
is not entity, code or year. Selecting it by name would work until the next slug.

    from pipeline.clients import owid_yields
    body, headers, url = owid_yields.table(spec, "maize-yields")
    rows, column = owid_yields.parse(body)
"""

from __future__ import annotations

import csv
import io

from ..config import SourceSpec
from ..http import fetch_with_headers

KEY_COLUMNS = ("entity", "code", "year")


class OwidResponseError(Exception):
    """The CSV was not the grapher table we asked for."""


def url(spec: SourceSpec, slug: str) -> str:
    return spec.format_url(slug=slug)


def table(spec: SourceSpec, slug: str) -> tuple[bytes, dict[str, str], str]:
    target = url(spec, slug)
    body, headers = fetch_with_headers(target, accept=spec.accept)
    parse(body, url=target)  # fail at the fetch, not three steps later
    return body, headers, target


def parse(body: bytes, *, url: str = "") -> tuple[list[dict[str, str]], str]:
    """(rows, value column name) from a grapher CSV. Offline."""
    text = body.decode("utf-8-sig", "replace")
    reader = csv.DictReader(io.StringIO(text))
    fields = [name.strip() for name in reader.fieldnames or []]
    missing = [name for name in KEY_COLUMNS if name not in fields]
    if missing:
        raise OwidResponseError(
            f"{url or 'CSV'}: missing column(s) {', '.join(missing)}; found {fields}. "
            f"useColumnShortNames=true is required for these names."
        )
    values = [name for name in fields if name not in KEY_COLUMNS]
    if len(values) != 1:
        # More than one leaves the choice ambiguous, and zero means the slug
        # returned a table with no measure in it.
        raise OwidResponseError(
            f"{url or 'CSV'}: expected exactly one value column besides "
            f"{', '.join(KEY_COLUMNS)}, found {values}"
        )
    rows = [row for row in reader if (row.get("entity") or "").strip()]
    if not rows:
        raise OwidResponseError(f"{url or 'CSV'}: no data rows")
    return rows, values[0]
