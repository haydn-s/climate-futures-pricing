"""Helpers every clean module needs. NOT a source module.

A clean step always starts the same way -- walk the archive for one source and
read the newest body per key -- and the two text quirks below turn up in more
than one source, so they are answered once here.
"""

from __future__ import annotations

import unicodedata
from typing import Iterator

from ..storage import RawRecord, RawStore


def archived(raw: RawStore, source: str, *, prefix: str = "") -> Iterator[tuple[RawRecord, bytes]]:
    """The newest archived body for every key of a source, with its record.

    `latest()` rather than `versions()` on purpose: a clean run rebuilds the
    table as the source stands NOW, and the older versions stay in the archive
    for the separate question of what changed. `prefix` selects one family of
    keys (a variable, a crop) for sources that archive more than one shape.

    Skips a key whose file has gone missing rather than failing the whole run,
    since data/ is gitignored and can be partially re-derived; the caller counts
    what it got and says so.
    """
    for key in raw.keys(source):
        if prefix and not key.startswith(prefix):
            continue
        record = raw.latest(source, key)
        if record is None or not record.path.exists():
            continue
        yield record, record.path.read_bytes()


def fold(text: str) -> str:
    """Casefolded and stripped of diacritics, for matching a place name across sources.

    "Cote d'Ivoire" and "Côte d'Ivoire" are the same country, and OWID spells it
    both ways across datasets, so a join on the raw string silently drops rows.
    """
    stripped = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return stripped.casefold().strip()


def matches_any(value: str, candidates: Iterator[str] | tuple[str, ...]) -> bool:
    """Whether `value` starts with any candidate, compared folded.

    Prefix rather than equality because entity names gain qualifiers -- FAO
    publishes "Cote d'Ivoire" in one table and OWID has regional aggregates like
    "Africa (FAO)" in the same column -- and the existing analysis matches the
    same way, so the pipeline agrees with the numbers already published.
    """
    folded = fold(value)
    return any(folded.startswith(fold(candidate)) for candidate in candidates)
