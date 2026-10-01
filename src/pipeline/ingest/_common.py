"""Helpers every ingest module needs, so six of them do not answer the same
question six ways. NOT a source module -- nothing here knows about any one API.

Four things live here because they are decided by the CLI contract rather than by
a source: how a dry run reports a fetch it did not make, how a scope argument
becomes a list of years, how a free-text place name becomes a store key, and how
long to wait between requests to a keyless public service.
"""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ..storage import RawRecord, RawStore

# Keyless academic use of public services: slow enough to be invisible in
# someone else's logs, fast enough that a full archive pull finishes over coffee.
POLITE_DELAY_SECONDS = 1.0

# A dry-run record has no bytes, so it cannot have a digest. Dashes rather than
# zeros, because zeros read like a real (and alarming) hash in the CLI's output.
UNFETCHED_DIGEST = "-" * 64


def pause(seconds: float = POLITE_DELAY_SECONDS) -> None:
    time.sleep(seconds)


def planned(raw: RawStore, source: str, key: str, url: str, *,
            params: Mapping[str, Any] | None = None,
            version_stamp: str | None = None) -> RawRecord:
    """A RawRecord describing a fetch that did NOT happen, for --dry-run.

    `fetch()` returns records either way, because the CLI prints the same table
    for both. Nothing is written and no manifest line is appended; the digest is
    dashes and the size is zero so a dry run cannot be mistaken for a real one in
    the output. No publication date is guessed -- for most sources it is only
    knowable once the response is in hand.
    """
    return RawRecord(
        source=source,
        key=key,
        url=url,
        path=raw.raw_dir / source / key,
        sha256=UNFETCHED_DIGEST,
        bytes=0,
        fetched_at=datetime.now(timezone.utc),
        params=dict(params or {}),
        publication_date=None,
        version_stamp=version_stamp,
    )


def resolve_years(requested: Iterable[int], *, first: int, last: int,
                  default_start: int) -> tuple[int, ...]:
    """The years to fetch: what was asked for, clipped to what the source has.

    An empty `requested` means the source's own default scope (the CLI documents
    it that way), which is `default_start` through `last` -- not the whole
    archive, because every source here reaches back further than this project
    looks. Years outside the record are dropped rather than fetched and turned
    into a 404 or a 422.
    """
    years = tuple(requested) or tuple(range(default_start, last + 1))
    return tuple(year for year in sorted(dict.fromkeys(years)) if first <= year <= last)


def slug(text: str) -> str:
    """A store-safe key segment from a free-text name.

    Place names carry diacritics and apostrophes that storage.RawStore rejects as
    path components, so "Cote d'Ivoire" becomes "cote-divoire" and stays stable
    across runs. The readable original travels in the manifest's params.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", folded).strip("-").lower()
    if not cleaned:
        raise ValueError(f"{text!r} has no characters usable in a store key")
    return cleaned
