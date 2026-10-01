"""Raw archive and processed tables, with a fetch history that cannot be rewritten.

The project's argument is about what was knowable on a given date, so the store
is built so that no fetch can quietly replace an earlier one. Two mechanisms do
that work:

  * Content addressing. A body lands at <root>/raw/<source>/<key>/<sha8><ext>, so
    a revised nClimGrid month writes a NEW file beside the old numbers instead of
    overwriting them. Nothing here ever opens an existing file for writing.
  * An append-only manifest. Every save appends one JSON line to
    <root>/manifest.jsonl, including a re-observation of identical bytes, because
    "the server still served this today" is itself point-in-time information --
    NOAA's prelim files change daily and are then deleted, so an unarchived
    observation is gone for good.

Hence the two readers: `versions()` is the list of distinct contents ever seen for
a key, while `history()` is every fetch. `latest()` is the newest fetch, which is
what the source served most recently.

`root` is the DATA directory, not the repository root: RawStore(Path("data"))
gives exactly data/raw/... and data/manifest.jsonl, and ProcessedStore(Path("data"))
gives data/processed/<name>.parquet.

    store = RawStore(default_data_root())
    record = store.save("usdm", "IA/2012", body, url=url, publication_date=released)
    if store.latest("usdm", "IA/2012").sha256 == sha256_hex(body): ...  # unchanged
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import pandas as pd

from .http import redact_url

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_NAME = "manifest.jsonl"
RAW_DIRNAME = "raw"
PROCESSED_DIRNAME = "processed"
DIGEST_WIDTHS = (8, 12, 16, 64)
# Keys become directory names: word characters plus the punctuation real symbols
# use (Yahoo's ZC=F), and "/" so a source can nest (usdm/IA/2012). No "..".
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._=+-]*")
_KEY = re.compile(rf"{_NAME.pattern}(?:/{_NAME.pattern})*")


def default_data_root() -> Path:
    """The repository's data/ directory, which is gitignored and re-derivable."""
    return ROOT / "data"


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RawRecord:
    """One fetch of one body: what it was, where it came from, when, and when published.

    `fetched_at` is always timezone-aware UTC and is ours. `publication_date` is
    the source's, and is None when the source does not define one -- the two are
    separate fields precisely so neither can be mistaken for the other.
    `version_stamp` is a short source-defined vintage token (an ETag, a GHCN build
    id, "prelim through 2026-09-12") that makes a silent re-issue detectable even
    when the hash comparison is not available.
    """

    source: str
    key: str
    url: str
    path: Path
    sha256: str
    bytes: int
    fetched_at: datetime
    params: Mapping[str, Any] = field(default_factory=dict)
    publication_date: date | None = None
    version_stamp: str | None = None


class RawStore:
    """The append-only raw archive under <root>/raw with its manifest at <root>/manifest.jsonl."""

    def __init__(self, root: str | Path, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self.root = Path(root)
        self.raw_dir = self.root / RAW_DIRNAME
        self.manifest_path = self.root / MANIFEST_NAME
        self._clock = clock
        self._cache: list[RawRecord] | None = None
        self._cache_size = -1

    # ---------------------------------------------------------------- writing

    def save(self, source: str, key: str, body: bytes, *, url: str,
             params: Mapping[str, Any] | None = None,
             publication_date: date | None = None,
             version_stamp: str | None = None) -> RawRecord:
        """Archive one fetched body and append its manifest line.

        Identical bytes for the same key resolve to the same path, which is left
        untouched; the manifest still records that the fetch happened. Changed
        bytes write a new file, so revisions accumulate.
        """
        source = _validate(source, "source", _NAME)
        key = _validate(key, "key", _KEY)
        digest = sha256_hex(body)
        directory = self.raw_dir / source / key
        path, needs_write = _content_path(directory, digest, _extension(url, body), len(body))
        if needs_write:
            directory.mkdir(parents=True, exist_ok=True)
            with open(path, "xb") as handle:  # never truncates an existing version
                handle.write(body)

        record = RawRecord(
            source=source,
            key=key,
            url=redact_url(url),
            path=path,
            sha256=digest,
            bytes=len(body),
            fetched_at=self._clock(),
            params=dict(params or {}),
            publication_date=publication_date,
            version_stamp=version_stamp,
        )
        self._append(record)
        return record

    def _append(self, record: RawRecord) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _as_json(record, self.root)
        line = json.dumps(payload, sort_keys=True, default=str)
        with open(self.manifest_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if self._cache is not None:
            # Extend the cache instead of dropping it, so a long fetch that checks
            # `latest()` before each download does not re-parse the whole manifest
            # once per file. Round-tripped, so a cached read equals a fresh one.
            self._cache.append(_from_json(payload, self.root))
            self._cache_size = self.manifest_path.stat().st_size

    # ---------------------------------------------------------------- reading

    def history(self, source: str | None = None, key: str | None = None) -> list[RawRecord]:
        """Every fetch recorded in the manifest, oldest first, optionally filtered."""
        return [
            record for record in self._records()
            if (source is None or record.source == source) and (key is None or record.key == key)
        ]

    def versions(self, source: str, key: str) -> list[RawRecord]:
        """Each distinct content ever seen for a key, oldest first observation first.

        A body re-observed later appears once, stamped with the first time it was
        seen; use history() when the full fetch log is what you want.
        """
        seen: dict[str, RawRecord] = {}
        for record in self.history(source, key):
            seen.setdefault(record.sha256, record)
        return sorted(seen.values(), key=lambda record: record.fetched_at)

    def latest(self, source: str, key: str) -> RawRecord | None:
        """The most recent fetch for a key -- what the source served last."""
        newest: RawRecord | None = None
        for record in self.history(source, key):
            if newest is None or record.fetched_at >= newest.fetched_at:
                newest = record
        return newest

    def has(self, source: str, key: str) -> bool:
        return self.latest(source, key) is not None

    def keys(self, source: str | None = None) -> list[str]:
        """Keys seen for a source, in first-fetch order -- what `status` reports on."""
        ordered: dict[str, None] = {}
        for record in self.history(source):
            ordered.setdefault(record.key, None)
        return list(ordered)

    def sources(self) -> list[str]:
        ordered: dict[str, None] = {}
        for record in self._records():
            ordered.setdefault(record.source, None)
        return list(ordered)

    def _records(self) -> list[RawRecord]:
        """The whole manifest, memoised on its byte size.

        The file is append-only, so its size is a sufficient cache key: it is
        stable while nothing is written and changes the moment anything is,
        including a line appended by another process.
        """
        size = self.manifest_path.stat().st_size if self.manifest_path.exists() else -1
        if self._cache is None or self._cache_size != size:
            self._cache = list(self._read_manifest())
            self._cache_size = size
        return self._cache

    def _read_manifest(self) -> Iterator[RawRecord]:
        if not self.manifest_path.exists():
            return
        with open(self.manifest_path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    # Corruption is worth stopping for: the manifest is the record
                    # of what was knowable when, and a skipped line is a lost fetch.
                    raise ValueError(f"{self.manifest_path}:{number}: malformed manifest line: {exc}") from exc
                yield _from_json(payload, self.root)


class ProcessedStore:
    """Cleaned tables as parquet under <root>/processed, one file per named table."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.processed_dir = self.root / PROCESSED_DIRNAME

    def path(self, name: str) -> Path:
        return self.processed_dir / f"{_validate(name, 'name', _KEY)}.parquet"

    def write(self, name: str, frame: pd.DataFrame) -> Path:
        """Write a cleaned table, replacing any previous copy.

        Processed tables are derived and re-derivable, so unlike the raw archive
        they are overwritten rather than versioned. Keep a `publication_date`
        column on anything dated so pipeline.calendar.as_of can reconstruct what
        was knowable on a past date.
        """
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target, engine="pyarrow")
        return target

    def read(self, name: str) -> pd.DataFrame:
        target = self.path(name)
        if not target.exists():
            raise FileNotFoundError(
                f"no processed table {name!r} at {target} (available: {', '.join(self.names()) or 'none'})"
            )
        return pd.read_parquet(target, engine="pyarrow")

    def names(self) -> list[str]:
        if not self.processed_dir.exists():
            return []
        return sorted(path.stem for path in self.processed_dir.glob("*.parquet"))


# --------------------------------------------------------------------- helpers


def _validate(value: str, label: str, pattern: re.Pattern[str]) -> str:
    """Reject anything that would escape the store or confuse a path."""
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(
            f"{label} {value!r} is not usable as a path: expected "
            f"{pattern.pattern!r} (letters, digits, . _ = + -"
            f"{', and / to nest' if pattern is _KEY else ''})"
        )
    return value


def _extension(url: str, body: bytes) -> str:
    """A cosmetic extension so archived files open in the right tool.

    Taken from the URL when it looks like a filename, otherwise sniffed as JSON,
    otherwise dropped. The sha is the identity; this is only for humans.
    """
    suffix = Path(urllib.parse.urlsplit(url).path).suffix
    if 1 < len(suffix) <= 8 and suffix[1:].isalnum():
        return suffix.lower()
    if body[:1] in (b"{", b"["):
        return ".json"
    return ".bin"


def _content_path(directory: Path, digest: str, extension: str, size: int) -> tuple[Path, bool]:
    """Where a body belongs, and whether it still needs writing.

    Widens the digest prefix on the astronomically unlikely chance that two
    different bodies share their first 8 hex characters, so "never overwrites"
    holds literally rather than approximately. The size is checked before the
    file is re-hashed, so the common idempotent path costs one stat.
    """
    for width in DIGEST_WIDTHS:
        candidate = directory / f"{digest[:width]}{extension}"
        if not candidate.exists():
            return candidate, True
        if candidate.stat().st_size == size and sha256_hex(candidate.read_bytes()) == digest:
            return candidate, False
    raise ValueError(f"{directory}: cannot place {digest} without overwriting an existing version")


def _as_json(record: RawRecord, root: Path) -> dict[str, Any]:
    """The manifest line for a record: relative path, ISO dates, redacted URL."""
    try:
        relative = record.path.relative_to(root)
    except ValueError:
        relative = record.path
    return {
        "source": record.source,
        "key": record.key,
        "url": redact_url(record.url),
        "path": relative.as_posix(),
        "sha256": record.sha256,
        "bytes": record.bytes,
        "fetched_at": record.fetched_at.isoformat(),
        "params": dict(record.params),
        "publication_date": record.publication_date.isoformat() if record.publication_date else None,
        "version_stamp": record.version_stamp,
    }


def _from_json(payload: Mapping[str, Any], root: Path) -> RawRecord:
    published = payload.get("publication_date")
    return RawRecord(
        source=payload["source"],
        key=payload["key"],
        url=payload["url"],
        path=root / payload["path"],
        sha256=payload["sha256"],
        bytes=payload["bytes"],
        fetched_at=datetime.fromisoformat(payload["fetched_at"]),
        params=payload.get("params") or {},
        publication_date=date.fromisoformat(published) if published else None,
        version_stamp=payload.get("version_stamp"),
    )
