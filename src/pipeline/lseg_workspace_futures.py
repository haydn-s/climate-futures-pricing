"""Download individual CBOT corn and wheat contracts through LSEG Workspace.

The connector uses the signed-in desktop session.  It neither accepts nor
stores credentials, and ``lseg-data`` remains an optional dependency so the
public-data pipeline and its tests do not require licensed software.

LSEG reuses live futures RICs.  Expired contracts therefore need the archived
RIC form (for example ``CZ24^2``), while the canonical table uses stable CME-
style identifiers such as ``ZCZ24``.  Keeping both identifiers in the vendor
export makes that translation auditable.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import pandas as pd
import yaml


SOURCE_NAME = "lseg_workspace_data_library"
SOURCE_URL = "https://developers.lseg.com/en/api-catalog/lseg-data-platform/lseg-data-library-for-python"
LICENSE_NOTE = (
    "Licensed LSEG Workspace data; do not commit or redistribute raw or derived "
    "row-level data."
)
LISTED_MONTHS = ("H", "K", "N", "U", "Z")
TWO_DIGIT_RIC_START_YEAR = 2024
MONTH_NUMBERS = {"H": 3, "K": 5, "N": 7, "U": 9, "Z": 12}
RIC_ROOTS = {"ZC": "C", "ZW": "W"}
HISTORY_FIELDS = (
    "OPEN_PRC",
    "HIGH_1",
    "LOW_1",
    "SETTLE",
    "ACVOL_UNS",
    "OPINT_1",
    "EXPIR_DATE",
)
EXPORT_COLUMNS = (
    "date",
    "root",
    "contract",
    "ric",
    "last_trade_date",
    "open",
    "high",
    "low",
    "settlement",
    "volume",
    "open_interest",
)


class WorkspaceFuturesError(ValueError):
    """The Workspace session, request, or response is unsuitable."""


@dataclass(frozen=True)
class ContractRequest:
    root: str
    delivery_year: int
    month_code: str
    contract: str
    ric: str


@dataclass(frozen=True)
class ContractResult:
    root: str
    contract: str
    ric: str
    rows_received: int
    rows_retained: int
    dropped_incomplete_ohlc: int
    dropped_invalid_ohlc: int
    first_date: str | None
    last_date: str | None
    status: str
    error: str | None = None


@dataclass(frozen=True)
class WorkspaceDownload:
    frame: pd.DataFrame
    contracts: tuple[ContractResult, ...]
    start: date
    end: date
    delivery_years: tuple[int, ...]
    roots: tuple[str, ...]
    downloaded_at_utc: str

    @property
    def requested_contracts(self) -> int:
        return len(self.contracts)

    @property
    def successful_contracts(self) -> int:
        return sum(result.status == "ok" for result in self.contracts)


def _archive_decade(year: int) -> int:
    return (year // 10) % 10


def _is_expired(year: int, month_code: str, as_of: date) -> bool:
    """Use the 15th as a safe boundary; actual expiry comes from LSEG history."""

    return date(year, MONTH_NUMBERS[month_code], 15) < as_of


def lseg_ric(root: str, month_code: str, year: int, *, as_of: date) -> str:
    """Return LSEG's live or archived RIC for one standard grain contract."""

    if root not in RIC_ROOTS:
        raise WorkspaceFuturesError(f"unsupported futures root {root!r}")
    if month_code not in LISTED_MONTHS:
        raise WorkspaceFuturesError(f"unsupported delivery month {month_code!r}")
    if not 2000 <= year <= 2099:
        raise WorkspaceFuturesError("delivery years must be between 2000 and 2099")
    # LSEG's CBOT RIC convention changed for the 2024 delivery year. Archived
    # 2020-2023 contracts are CH0^2..CH3^2, while 2024 is CH24^2.
    year_part = (
        f"{year % 100:02d}"
        if year >= TWO_DIGIT_RIC_START_YEAR
        else str(year % 10)
    )
    base = f"{RIC_ROOTS[root]}{month_code}{year_part}"
    if _is_expired(year, month_code, as_of):
        return f"{base}^{_archive_decade(year)}"
    return base


def contract_requests(
    delivery_years: Iterable[int],
    roots: Iterable[str] = ("ZC", "ZW"),
    *,
    as_of: date,
) -> tuple[ContractRequest, ...]:
    years = tuple(dict.fromkeys(int(year) for year in delivery_years))
    selected_roots = tuple(dict.fromkeys(str(root).upper() for root in roots))
    if not years:
        raise WorkspaceFuturesError("at least one delivery year is required")
    unknown = sorted(set(selected_roots) - set(RIC_ROOTS))
    if unknown:
        raise WorkspaceFuturesError(f"unsupported futures roots: {', '.join(unknown)}")
    requests = []
    for year in years:
        for root in selected_roots:
            for month_code in LISTED_MONTHS:
                requests.append(ContractRequest(
                    root=root,
                    delivery_year=year,
                    month_code=month_code,
                    contract=f"{root}{month_code}{year % 100:02d}",
                    ric=lseg_ric(root, month_code, year, as_of=as_of),
                ))
    return tuple(requests)


def _history_date(values: pd.Series) -> pd.Series:
    """Parse LSEG's numeric YYYYMMDD expiry field without float punctuation."""

    text = values.astype("string").str.replace(r"\.0$", "", regex=True)
    return pd.to_datetime(text, format="%Y%m%d", errors="coerce").dt.normalize()


def normalize_history(
    history: pd.DataFrame,
    request: ContractRequest,
) -> tuple[pd.DataFrame, int, int]:
    """Convert one LSEG history response to the vendor-export schema."""

    missing = sorted(set(HISTORY_FIELDS) - set(history.columns))
    if missing:
        raise WorkspaceFuturesError(
            f"{request.ric}: LSEG response is missing fields: {', '.join(missing)}"
        )
    frame = history.reset_index()
    date_column = "Date" if "Date" in frame.columns else frame.columns[0]
    normalized = pd.DataFrame({
        "date": pd.to_datetime(frame[date_column], errors="coerce").dt.normalize(),
        "root": request.root,
        "contract": request.contract,
        "ric": request.ric,
        "last_trade_date": _history_date(frame["EXPIR_DATE"]),
        "open": pd.to_numeric(frame["OPEN_PRC"], errors="coerce"),
        "high": pd.to_numeric(frame["HIGH_1"], errors="coerce"),
        "low": pd.to_numeric(frame["LOW_1"], errors="coerce"),
        "settlement": pd.to_numeric(frame["SETTLE"], errors="coerce"),
        "volume": pd.to_numeric(frame["ACVOL_UNS"], errors="coerce"),
        "open_interest": pd.to_numeric(frame["OPINT_1"], errors="coerce"),
    })
    required = ["date", "last_trade_date", "open", "high", "low", "settlement"]
    incomplete = normalized[required].isna().any(axis=1)
    invalid_ohlc = ~incomplete & (
        (normalized["high"] < normalized["open"])
        | (normalized["low"] > normalized["open"])
        | (normalized["low"] > normalized["high"])
    )
    dropped_incomplete = int(incomplete.sum())
    dropped_invalid = int(invalid_ohlc.sum())
    normalized = normalized.loc[~incomplete & ~invalid_ohlc, list(EXPORT_COLUMNS)]
    return (
        normalized.sort_values("date").reset_index(drop=True),
        dropped_incomplete,
        dropped_invalid,
    )


def _load_lseg() -> Any:
    try:
        import lseg.data as ld
    except ImportError as exc:
        raise WorkspaceFuturesError(
            "lseg-data is not installed; run `pip install -r requirements-lseg.txt`"
        ) from exc
    return ld


def download_workspace_history(
    *,
    start: date,
    end: date,
    delivery_years: Iterable[int],
    roots: Iterable[str] = ("ZC", "ZW"),
    lseg: Any | None = None,
    downloaded_at: datetime | None = None,
    as_of: date | None = None,
    progress: Callable[[ContractResult], None] | None = None,
) -> WorkspaceDownload:
    """Download each contract separately through the local desktop session.

    A contract-level error is recorded and the run continues.  The caller can
    therefore inspect partial coverage, but saving refuses a wholly empty run.
    """

    if start > end:
        raise WorkspaceFuturesError("start date must not be after end date")
    requests = contract_requests(
        delivery_years,
        roots,
        as_of=as_of or datetime.now(timezone.utc).date(),
    )
    client = lseg or _load_lseg()
    frames: list[pd.DataFrame] = []
    results: list[ContractResult] = []
    try:
        client.open_session()
    except Exception as exc:
        raise WorkspaceFuturesError(
            "could not open the LSEG Workspace desktop session; start Workspace and sign in"
        ) from exc
    try:
        for request in requests:
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore", category=FutureWarning, module=r"lseg\.data\..*"
                    )
                    history = client.get_history(
                        request.ric,
                        fields=list(HISTORY_FIELDS),
                        interval="1D",
                        start=start.isoformat(),
                        end=end.isoformat(),
                    )
                if history is None or history.empty:
                    raise WorkspaceFuturesError("LSEG returned no observations")
                normalized, dropped_incomplete, dropped_invalid = normalize_history(
                    history, request
                )
                if normalized.empty:
                    raise WorkspaceFuturesError(
                        "LSEG returned no rows with complete date, expiry, and OHLC"
                    )
                frames.append(normalized)
                result = ContractResult(
                    root=request.root,
                    contract=request.contract,
                    ric=request.ric,
                    rows_received=len(history),
                    rows_retained=len(normalized),
                    dropped_incomplete_ohlc=dropped_incomplete,
                    dropped_invalid_ohlc=dropped_invalid,
                    first_date=normalized["date"].min().date().isoformat(),
                    last_date=normalized["date"].max().date().isoformat(),
                    status="ok",
                )
            except Exception as exc:
                result = ContractResult(
                    root=request.root,
                    contract=request.contract,
                    ric=request.ric,
                    rows_received=0,
                    rows_retained=0,
                    dropped_incomplete_ohlc=0,
                    dropped_invalid_ohlc=0,
                    first_date=None,
                    last_date=None,
                    status="error",
                    error=str(exc),
                )
            results.append(result)
            if progress:
                progress(result)
    finally:
        try:
            client.close_session()
        except Exception:
            pass

    if not frames:
        details = "; ".join(
            f"{result.ric}: {result.error}" for result in results[:3]
        )
        raise WorkspaceFuturesError(f"no futures history was downloaded ({details})")
    frame = pd.concat(frames, ignore_index=True).sort_values(
        ["date", "root", "last_trade_date", "contract"]
    ).reset_index(drop=True)
    timestamp = downloaded_at or datetime.now(timezone.utc)
    return WorkspaceDownload(
        frame=frame,
        contracts=tuple(results),
        start=start,
        end=end,
        delivery_years=tuple(dict.fromkeys(int(year) for year in delivery_years)),
        roots=tuple(dict.fromkeys(str(root).upper() for root in roots)),
        downloaded_at_utc=timestamp.astimezone(timezone.utc).isoformat(),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mapping(query_reference: str) -> Mapping[str, Any]:
    return {
        "schema_version": 1,
        "source_name": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "license_note": LICENSE_NOTE,
        "query_reference": query_reference,
        "columns": {
            "date": "date",
            "root": "root",
            "contract": "contract",
            "last_trade_date": "last_trade_date",
            "open": "open",
            "high": "high",
            "low": "low",
            "settlement": "settlement",
            "volume": "volume",
            "open_interest": "open_interest",
        },
        "root_values": {"ZC": "ZC", "ZW": "ZW"},
        "price_multiplier_to_cents": 1.0,
    }


def save_workspace_download(
    download: WorkspaceDownload,
    output_dir: str | Path,
    *,
    stem: str = "zc_zw_daily",
) -> tuple[Path, Path, Path]:
    """Write an ignored vendor export, query manifest, and importer mapping."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    export_path = directory / f"{stem}.csv"
    manifest_path = directory / f"{stem}.query.json"
    mapping_path = directory / f"{stem}.mapping.yaml"
    download.frame.to_csv(export_path, index=False, date_format="%Y-%m-%d")

    mapping = _mapping(manifest_path.name)
    mapping_path.write_text(
        yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8"
    )
    manifest = {
        "schema_version": 1,
        "source_name": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "license_note": LICENSE_NOTE,
        "session_type": "desktop.workspace",
        "downloaded_at_utc": download.downloaded_at_utc,
        "request": {
            "start": download.start.isoformat(),
            "end": download.end.isoformat(),
            "delivery_years": list(download.delivery_years),
            "roots": list(download.roots),
            "month_codes": list(LISTED_MONTHS),
            "interval": "1D",
            "fields": list(HISTORY_FIELDS),
        },
        "transformations": {
            "prices": "LSEG values retained in cents per bushel",
            "expired_rics": "resolved with LSEG archive-decade suffixes",
            "dropped_rows": (
                "rows missing date, expiry, or any OHLC field, plus rows whose "
                "high/low does not contain the session open"
            ),
        },
        "export_file": export_path.name,
        "export_sha256": _sha256(export_path),
        "mapping_file": mapping_path.name,
        "rows": len(download.frame),
        "requested_contracts": download.requested_contracts,
        "successful_contracts": download.successful_contracts,
        "contracts": [asdict(result) for result in download.contracts],
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return export_path, manifest_path, mapping_path
