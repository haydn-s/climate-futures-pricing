"""Make src/ importable, and give the per-source tests a store to work against.

The package lives under src/ and nothing is pip-installed, so pytest needs the
path. Everything else here exists so a source's test can say "archive these
fixture bytes, then clean them" in two lines, because that round trip -- bytes in,
table out, no network -- is what most of these tests are.

Every test in this suite is OFFLINE. Fixtures under tests/fixtures/ are real
captured responses (except tests/fixtures/nass/, which says so loudly in its
PROVENANCE.md), and no test may reach the network: a suite that needs a working
internet connection stops telling you whether your parser is correct.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline.cli import CleanRequest, FetchRequest  # noqa: E402
from pipeline.config import Geography, SourceSpec, load_geography, load_sources  # noqa: E402
from pipeline.storage import ProcessedStore, RawRecord, RawStore  # noqa: E402


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return ROOT / "tests" / "fixtures"


@pytest.fixture(scope="session")
def sources() -> dict[str, SourceSpec]:
    """The real config/sources.yaml. Tests read the shipped config, not a copy of it."""
    return load_sources(ROOT / "config" / "sources.yaml")


@pytest.fixture(scope="session")
def geography() -> Geography:
    return load_geography(ROOT / "config" / "geography.yaml")


@dataclass
class Harness:
    """A throwaway data root plus the requests the CLI would have built.

        harness.seed("usdm", "IA/2012", body, publication_date=date(2012, 12, 27))
        harness.clean("usdm")          # -> CleanRequest
        harness.fetch("usdm", years=(2012,), dry_run=True)
    """

    root: Path
    raw: RawStore
    processed: ProcessedStore
    sources: dict[str, SourceSpec]
    geography: Geography

    def seed(self, source: str, key: str, body: bytes, *, url: str | None = None,
             params: Mapping[str, Any] | None = None,
             publication_date: date | None = None,
             version_stamp: str | None = None) -> RawRecord:
        return self.raw.save(
            source, key, body,
            url=url or f"https://example.test/{source}/{key}",
            params=params, publication_date=publication_date, version_stamp=version_stamp,
        )

    def clean(self, source: str, *, dry_run: bool = False) -> CleanRequest:
        return CleanRequest(spec=self.sources[source], geography=self.geography,
                            raw=self.raw, processed=self.processed, dry_run=dry_run)

    def fetch(self, source: str, **overrides: Any) -> FetchRequest:
        fields: dict[str, Any] = {
            "spec": self.sources[source], "geography": self.geography,
            "raw": self.raw, "processed": self.processed,
        }
        fields.update(overrides)
        return FetchRequest(**fields)


@pytest.fixture
def harness(tmp_path: Path, sources: dict[str, SourceSpec], geography: Geography) -> Harness:
    root = tmp_path / "data"
    return Harness(root=root, raw=RawStore(root), processed=ProcessedStore(root),
                   sources=sources, geography=geography)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any accidental network call a loud failure rather than a slow test.

    Applied by the tests that exercise an ingest module's dry-run and skip paths,
    which must decide what to do WITHOUT fetching. If one of them ever starts
    reaching for the network, this is the assertion that says so.
    """
    import urllib.request

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("this test must not touch the network")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)


class FakeResponse:
    """The little of urllib's response object that pipeline.http actually uses."""

    def __init__(self, body: bytes = b"", status: int = 200,
                 headers: Mapping[str, str] | None = None) -> None:
        self.body = body
        self.status = status
        self.headers = dict(headers or {})

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)
