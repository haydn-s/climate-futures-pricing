"""A config-driven ingestion and cleaning pipeline for public climate and price data.

The core answers four questions so that the per-source modules do not each answer
them differently:

    config    what are the sources and the crop geography?  (config/*.yaml)
    http      how do we talk to the network?                (stdlib urllib, one agent)
    storage   where do bytes and tables live, and how are revisions kept?
    calendar  when did a dated observation become public?

Sources are described in config/sources.yaml and crops in config/geography.yaml,
so adding either needs no Python change. Per-source code lives in three packages
whose modules are named after the source key: clients/ (talk to one API),
ingest/ (archive raw bytes) and clean/ (raw bytes to a processed table). The CLI
dispatches on the key -- see pipeline.cli for the contract they implement.

Run it from the repository root with src/ on the path:

    PYTHONPATH=src python -m pipeline check-config
"""

from __future__ import annotations

from .calendar import (
    PUBLICATION_COLUMN,
    as_of,
    expected_nclimgrid_status,
    nclimgrid_available_after,
    usdm_release_date,
)
from .config import (
    ConfigError,
    CropGeography,
    Geography,
    MonthWindow,
    SourceSpec,
    load_dotenv,
    load_geography,
    load_sources,
)
from .http import USER_AGENT, FetchError, fetch, fetch_with_headers, parse_last_modified, redact_url
from .storage import ProcessedStore, RawRecord, RawStore, default_data_root, sha256_hex

__version__ = "0.1.0"

__all__ = [
    "ConfigError",
    "CropGeography",
    "FetchError",
    "Geography",
    "MonthWindow",
    "PUBLICATION_COLUMN",
    "ProcessedStore",
    "RawRecord",
    "RawStore",
    "SourceSpec",
    "USER_AGENT",
    "__version__",
    "as_of",
    "default_data_root",
    "expected_nclimgrid_status",
    "fetch",
    "fetch_with_headers",
    "load_dotenv",
    "load_geography",
    "load_sources",
    "nclimgrid_available_after",
    "parse_last_modified",
    "redact_url",
    "sha256_hex",
    "usdm_release_date",
]
