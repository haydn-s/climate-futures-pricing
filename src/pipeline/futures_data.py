"""Import and audit licensed individual-contract futures data.

Vendor files are kept outside Git.  This module converts a CSV or Parquet export
through an explicit column mapping, then records hashes and provenance beside the
derived canonical table.  The mapping is deliberately vendor-neutral: we will
fill in the exact WRDS/LSEG fields only after the account exposes its data
dictionary, rather than guessing them now.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml

from .futures_execution import ContractSpec, ExecutionError, load_execution_config
from .storage import ProcessedStore

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMPORT_MAPPING = "config/futures_import_template.yaml"

REQUIRED_IMPORT_FIELDS = (
    "date", "root", "contract", "last_trade_date",
    "open", "high", "low", "settlement",
)
OPTIONAL_IMPORT_FIELDS = ("volume", "open_interest", "maintenance_margin_usd")
PRICE_FIELDS = ("open", "high", "low", "settlement")
CANONICAL_PRICE_FIELDS = tuple(f"{field}_cents" for field in PRICE_FIELDS)


class FuturesDataError(ValueError):
    """A vendor export, import mapping, or canonical panel is invalid."""


@dataclass(frozen=True)
class ImportSpec:
    source_name: str
    source_url: str
    license_note: str
    query_reference: str
    columns: Mapping[str, str]
    root_values: Mapping[str, str]
    price_multiplier_to_cents: float = 1.0

    def __post_init__(self) -> None:
        missing = sorted(set(REQUIRED_IMPORT_FIELDS) - set(self.columns))
        if missing:
            raise FuturesDataError(
                f"import mapping is missing canonical fields: {', '.join(missing)}"
            )
        unknown = sorted(
            set(self.columns) - set(REQUIRED_IMPORT_FIELDS) - set(OPTIONAL_IMPORT_FIELDS)
        )
        if unknown:
            raise FuturesDataError(
                f"import mapping has unknown canonical fields: {', '.join(unknown)}"
            )
        if len(set(self.columns.values())) != len(self.columns):
            raise FuturesDataError("each vendor column may map to only one canonical field")
        if not self.source_name.strip() or not self.license_note.strip():
            raise FuturesDataError("source_name and license_note must be non-empty")
        if not np.isfinite(self.price_multiplier_to_cents) or self.price_multiplier_to_cents <= 0:
            raise FuturesDataError("price_multiplier_to_cents must be positive")
        if not self.root_values or set(self.root_values.values()) - {"ZC", "ZW", "CC"}:
            raise FuturesDataError("root_values must map vendor values only to ZC, ZW, or CC")


@dataclass(frozen=True)
class AuditIssue:
    severity: str
    code: str
    count: int
    message: str
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditContractSpec:
    """The subset of exchange mechanics needed for price-data auditing."""

    tick_size_cents: float
    listed_month_codes: tuple[str, ...]


@dataclass(frozen=True)
class AuditReport:
    rows: int
    roots: tuple[str, ...]
    contracts: int
    start_date: str | None
    end_date: str | None
    common_sessions: int
    rows_by_root: Mapping[str, int]
    years_by_root: Mapping[str, tuple[int, ...]]
    delivery_month_codes_by_root: Mapping[str, tuple[str, ...]]
    issues: tuple[AuditIssue, ...]

    @property
    def pricing_valid(self) -> bool:
        return not any(issue.severity == "ERROR" for issue in self.issues)

    @property
    def ready_for_execution(self) -> bool:
        return self.pricing_valid and not any(
            issue.severity == "BLOCKER" for issue in self.issues
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "roots": list(self.roots),
            "contracts": self.contracts,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "common_sessions": self.common_sessions,
            "rows_by_root": dict(self.rows_by_root),
            "years_by_root": {
                root: list(years) for root, years in self.years_by_root.items()
            },
            "delivery_month_codes_by_root": {
                root: list(codes)
                for root, codes in self.delivery_month_codes_by_root.items()
            },
            "pricing_valid": self.pricing_valid,
            "ready_for_execution": self.ready_for_execution,
            "issues": [asdict(issue) for issue in self.issues],
        }


@dataclass(frozen=True)
class ImportResult:
    frame: pd.DataFrame
    manifest: Mapping[str, Any]


def _resolve(path: str | Path) -> Path:
    resolved = Path(path)
    return resolved if resolved.is_absolute() else ROOT / resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_import_spec(path: str | Path = DEFAULT_IMPORT_MAPPING) -> ImportSpec:
    mapping_path = _resolve(path)
    try:
        raw = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise FuturesDataError(f"cannot load import mapping {mapping_path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise FuturesDataError("import mapping must be a mapping with schema_version: 1")
    expected = {
        "schema_version", "source_name", "source_url", "license_note",
        "query_reference", "columns", "root_values", "price_multiplier_to_cents",
    }
    if set(raw) != expected:
        missing = sorted(expected - set(raw))
        extra = sorted(set(raw) - expected)
        raise FuturesDataError(
            f"import mapping keys differ; missing={missing or 'none'}, extra={extra or 'none'}"
        )
    try:
        return ImportSpec(
            source_name=str(raw["source_name"]),
            source_url=str(raw["source_url"]),
            license_note=str(raw["license_note"]),
            query_reference=str(raw["query_reference"]),
            columns={str(k): str(v) for k, v in raw["columns"].items()},
            root_values={str(k): str(v) for k, v in raw["root_values"].items()},
            price_multiplier_to_cents=float(raw["price_multiplier_to_cents"]),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise FuturesDataError(f"invalid import mapping {mapping_path}: {exc}") from exc


def read_export(path: str | Path) -> pd.DataFrame:
    source = Path(path)
    if not source.exists():
        raise FuturesDataError(f"vendor export does not exist: {source}")
    suffix = source.suffix.lower()
    try:
        if suffix == ".csv":
            return pd.read_csv(source)
        if suffix in {".parquet", ".pq"}:
            return pd.read_parquet(source, engine="pyarrow")
    except Exception as exc:  # pandas wraps parser and Arrow failures differently
        raise FuturesDataError(f"cannot read vendor export {source}: {exc}") from exc
    raise FuturesDataError("vendor export must be .csv, .parquet, or .pq")


def normalize_export(frame: pd.DataFrame, spec: ImportSpec) -> pd.DataFrame:
    """Map a vendor export into a stable contract-price schema."""

    missing_source = sorted(set(spec.columns.values()) - set(frame.columns))
    if missing_source:
        raise FuturesDataError(
            f"vendor export is missing mapped columns: {', '.join(missing_source)}"
        )
    selected = frame[list(spec.columns.values())].rename(
        columns={vendor: canonical for canonical, vendor in spec.columns.items()}
    )
    raw_roots = selected["root"].astype(str).str.strip()
    selected["root"] = raw_roots.map(spec.root_values)
    unknown = sorted(raw_roots[selected["root"].isna()].unique())
    if unknown:
        raise FuturesDataError(
            f"root_values does not map vendor roots: {', '.join(unknown[:10])}"
        )
    selected["contract"] = selected["contract"].astype(str).str.strip().str.upper()
    if (selected["contract"] == "").any():
        raise FuturesDataError("contract identifiers cannot be empty")
    for column in ("date", "last_trade_date"):
        parsed = pd.to_datetime(selected[column], errors="coerce").dt.normalize()
        if parsed.isna().any():
            raise FuturesDataError(
                f"{column} has {int(parsed.isna().sum())} missing or unparseable values"
            )
        selected[column] = parsed

    for column in PRICE_FIELDS:
        numeric = pd.to_numeric(selected.pop(column), errors="coerce")
        selected[f"{column}_cents"] = numeric * spec.price_multiplier_to_cents
    for column in OPTIONAL_IMPORT_FIELDS:
        if column in selected:
            selected[column] = pd.to_numeric(selected[column], errors="coerce")

    order = [
        "date", "root", "contract", "last_trade_date", *CANONICAL_PRICE_FIELDS,
        *[field for field in OPTIONAL_IMPORT_FIELDS if field in selected],
    ]
    return selected[order].sort_values(
        ["date", "root", "last_trade_date", "contract"]
    ).reset_index(drop=True)


def import_contract_export(
    input_path: str | Path,
    mapping_path: str | Path = DEFAULT_IMPORT_MAPPING,
    *,
    imported_at: datetime | None = None,
) -> ImportResult:
    input_file = Path(input_path)
    mapping_file = _resolve(mapping_path)
    spec = load_import_spec(mapping_file)
    frame = normalize_export(read_export(input_file), spec)
    report = audit_contract_panel(
        frame, required_roots=set(spec.root_values.values())
    )
    timestamp = imported_at or datetime.now(timezone.utc)
    manifest = {
        "schema_version": 1,
        "source_name": spec.source_name,
        "source_url": spec.source_url,
        "license_note": spec.license_note,
        "query_reference": spec.query_reference,
        "input_file": input_file.name,
        "input_sha256": _sha256_file(input_file),
        "mapping_file": mapping_file.name,
        "mapping_sha256": _sha256_file(mapping_file),
        "imported_at_utc": timestamp.astimezone(timezone.utc).isoformat(),
        "price_multiplier_to_cents": spec.price_multiplier_to_cents,
        "audit": report.as_dict(),
    }
    return ImportResult(frame=frame, manifest=manifest)


def save_import(
    result: ImportResult,
    processed: ProcessedStore,
    *,
    name: str = "futures_contracts",
) -> tuple[Path, Path]:
    table_path = processed.write(name, result.frame)
    manifest_path = table_path.with_suffix(".manifest.json")
    payload = {**result.manifest, "processed_table": table_path.name}
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return table_path, manifest_path


def _examples(frame: pd.DataFrame, mask: pd.Series, columns: list[str]) -> tuple[str, ...]:
    values = frame.loc[mask, columns].head(3).astype(str).agg(" | ".join, axis=1)
    return tuple(values)


def audit_contract_panel(
    frame: pd.DataFrame,
    specs: Mapping[str, ContractSpec | AuditContractSpec] | None = None,
    *,
    required_roots: set[str] | None = None,
) -> AuditReport:
    """Report data defects without failing at the first one."""

    if specs is None:
        specs = dict(load_execution_config().contracts)
        # Cocoa is not part of the corn/wheat spread executor, but its exchange
        # mechanics are sufficient for unit, tick, and contract-code auditing.
        specs["CC"] = AuditContractSpec(
            tick_size_cents=100.0,
            listed_month_codes=("H", "K", "N", "U", "Z"),
        )
    issues: list[AuditIssue] = []
    required = {
        "date", "root", "contract", "last_trade_date", *CANONICAL_PRICE_FIELDS,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        issues.append(AuditIssue(
            "ERROR", "MISSING_COLUMNS", len(missing),
            f"canonical panel is missing: {', '.join(missing)}", tuple(missing),
        ))
        return AuditReport(
            len(frame), (), 0, None, None, 0, {}, {}, {}, tuple(issues)
        )

    clean = frame.copy()
    for column in ("date", "last_trade_date"):
        clean[column] = pd.to_datetime(clean[column], errors="coerce").dt.normalize()
        bad = clean[column].isna()
        if bad.any():
            issues.append(AuditIssue(
                "ERROR", f"INVALID_{column.upper()}", int(bad.sum()),
                f"{column} contains missing or unparseable values",
                _examples(clean, bad, ["root", "contract"]),
            ))
    for column in (*CANONICAL_PRICE_FIELDS, "volume", "open_interest",
                   "maintenance_margin_usd"):
        if column in clean:
            clean[column] = pd.to_numeric(clean[column], errors="coerce")

    duplicates = clean.duplicated(["date", "root", "contract"], keep=False)
    if duplicates.any():
        issues.append(AuditIssue(
            "ERROR", "DUPLICATE_ROWS", int(duplicates.sum()),
            "date/root/contract must identify exactly one observation",
            _examples(clean, duplicates, ["date", "root", "contract"]),
        ))
    roots = tuple(sorted(str(value) for value in clean["root"].dropna().unique()))
    unknown_roots = sorted(set(roots) - set(specs))
    if unknown_roots:
        issues.append(AuditIssue(
            "ERROR", "UNKNOWN_ROOTS", len(unknown_roots),
            f"unsupported roots: {', '.join(unknown_roots)}", tuple(unknown_roots),
        ))
    missing_roots = sorted((required_roots or set()) - set(roots))
    if missing_roots:
        issues.append(AuditIssue(
            "ERROR", "MISSING_ROOTS", len(missing_roots),
            f"required roots absent: {', '.join(missing_roots)}", tuple(missing_roots),
        ))
    for column in CANONICAL_PRICE_FIELDS:
        bad = clean[column].isna() | (clean[column] <= 0)
        if bad.any():
            issues.append(AuditIssue(
                "ERROR", f"INVALID_{column.upper()}", int(bad.sum()),
                f"{column} must be present and positive",
                _examples(clean, bad, ["date", "root", "contract"]),
            ))
    usable_prices = clean[list(CANONICAL_PRICE_FIELDS)].notna().all(axis=1)
    bad_ohlc = usable_prices & (
        (clean["high_cents"] < clean["open_cents"])
        | (clean["low_cents"] > clean["open_cents"])
        | (clean["low_cents"] > clean["high_cents"])
    )
    if bad_ohlc.any():
        issues.append(AuditIssue(
            "ERROR", "INVALID_OHLC", int(bad_ohlc.sum()),
            "high/low does not contain the session open",
            _examples(clean, bad_ohlc, ["date", "root", "contract"]),
        ))
    settlement_outside = usable_prices & ~bad_ohlc & (
        (clean["settlement_cents"] > clean["high_cents"])
        | (clean["settlement_cents"] < clean["low_cents"])
    )
    if settlement_outside.any():
        issues.append(AuditIssue(
            "INFO", "SETTLEMENT_OUTSIDE_RANGE", int(settlement_outside.sum()),
            "settlement lies outside the reported intraday high/low; verify vendor semantics",
            _examples(clean, settlement_outside, ["date", "root", "contract"]),
        ))

    valid_dates = clean["date"].notna() & clean["last_trade_date"].notna()
    after_expiry = valid_dates & (clean["date"] > clean["last_trade_date"])
    if after_expiry.any():
        issues.append(AuditIssue(
            "ERROR", "AFTER_LAST_TRADE", int(after_expiry.sum()),
            "observations cannot follow last_trade_date",
            _examples(clean, after_expiry, ["date", "contract", "last_trade_date"]),
        ))
    inconsistent_expiry = clean.groupby("contract", dropna=False)[
        "last_trade_date"
    ].nunique(dropna=False) > 1
    if inconsistent_expiry.any():
        examples = tuple(str(value) for value in inconsistent_expiry[inconsistent_expiry].index[:3])
        issues.append(AuditIssue(
            "ERROR", "INCONSISTENT_LAST_TRADE_DATE", int(inconsistent_expiry.sum()),
            "a contract has more than one last_trade_date", examples,
        ))

    for root, spec in specs.items():
        root_rows = clean[clean["root"] == root]
        if root_rows.empty:
            continue
        symbols = root_rows["contract"].astype(str)
        suffixes = symbols.str[len(root):]
        invalid_symbol = (
            ~symbols.str.startswith(root)
            | (suffixes.str.len() < 2)
            | ~suffixes.str[0].isin(spec.listed_month_codes)
        )
        if invalid_symbol.any():
            issues.append(AuditIssue(
                "ERROR", "INVALID_CONTRACT_CODE", int(invalid_symbol.sum()),
                f"{root} contract identifiers must use a listed delivery-month code",
                tuple(symbols[invalid_symbol].drop_duplicates().head(3)),
            ))
        prices = root_rows[list(CANONICAL_PRICE_FIELDS)].to_numpy(dtype=float)
        finite = prices[np.isfinite(prices)]
        if finite.size and float(np.median(finite)) < 50:
            issues.append(AuditIssue(
                "WARNING", "SUSPECT_PRICE_UNIT", len(root_rows),
                f"{root} median price is below 50 cents; verify dollars-to-cents scaling",
            ))
        tick = spec.tick_size_cents
        off_tick = root_rows[list(CANONICAL_PRICE_FIELDS)].apply(
            lambda column: ~np.isclose(column / tick, np.round(column / tick)), axis=0
        ).any(axis=1)
        if off_tick.any():
            issues.append(AuditIssue(
                "WARNING", "OFF_TICK_PRICE", int(off_tick.sum()),
                f"{root} has prices not aligned to its {tick:g}-cent tick",
                _examples(root_rows, off_tick, ["date", "contract"]),
            ))

    for column in ("volume", "open_interest"):
        if column not in clean:
            issues.append(AuditIssue(
                "WARNING", f"MISSING_{column.upper()}", len(clean),
                f"{column} was not supplied; liquidity-based roll checks are unavailable",
            ))
        else:
            bad = clean[column].isna() | (clean[column] < 0)
            if bad.any():
                issues.append(AuditIssue(
                    "WARNING", f"INVALID_{column.upper()}", int(bad.sum()),
                    f"{column} contains missing or negative values",
                ))
    if "maintenance_margin_usd" not in clean:
        issues.append(AuditIssue(
            "BLOCKER", "MISSING_MARGIN", len(clean),
            "dated maintenance margin is required before execution simulation",
        ))
    else:
        bad_margin = clean["maintenance_margin_usd"].isna() | (
            clean["maintenance_margin_usd"] <= 0
        )
        if bad_margin.any():
            issues.append(AuditIssue(
                "BLOCKER", "INVALID_MARGIN", int(bad_margin.sum()),
                "dated maintenance margin is missing or non-positive",
            ))

    gap_examples: list[str] = []
    gap_count = 0
    dated = clean.dropna(subset=["date"])
    for (root, contract), group in dated.groupby(["root", "contract"]):
        dates_for_contract = sorted(group["date"].drop_duplicates())
        for previous, current in zip(dates_for_contract, dates_for_contract[1:]):
            missing_weekdays = int(np.busday_count(previous.date(), current.date())) - 1
            if missing_weekdays > 0:
                gap_count += missing_weekdays
                if len(gap_examples) < 3:
                    gap_examples.append(
                        f"{root} {contract}: {previous.date()}..{current.date()}"
                    )
    if gap_count:
        issues.append(AuditIssue(
            "INFO", "POTENTIAL_SESSION_GAPS", gap_count,
            "weekday gaps need comparison with the exchange holiday calendar",
            tuple(gap_examples),
        ))

    contracts = clean["contract"].nunique(dropna=True)
    dates = clean["date"].dropna()
    root_dates = {
        root: set(clean.loc[clean["root"] == root, "date"].dropna()) for root in roots
    }
    common = set.intersection(*root_dates.values()) if root_dates and all(root_dates.values()) else set()
    rows_by_root = {
        root: int((clean["root"] == root).sum()) for root in roots
    }
    years_by_root = {
        root: tuple(sorted(
            int(year) for year in clean.loc[clean["root"] == root, "date"].dropna().dt.year.unique()
        ))
        for root in roots
    }
    delivery_month_codes_by_root: dict[str, tuple[str, ...]] = {}
    for root in roots:
        symbols = clean.loc[clean["root"] == root, "contract"].dropna().astype(str)
        codes = {
            symbol[len(root):len(root) + 1]
            for symbol in symbols
            if symbol.startswith(root) and len(symbol) > len(root)
        }
        delivery_month_codes_by_root[root] = tuple(sorted(codes))
    return AuditReport(
        rows=len(clean),
        roots=roots,
        contracts=int(contracts),
        start_date=dates.min().date().isoformat() if not dates.empty else None,
        end_date=dates.max().date().isoformat() if not dates.empty else None,
        common_sessions=len(common),
        rows_by_root=rows_by_root,
        years_by_root=years_by_root,
        delivery_month_codes_by_root=delivery_month_codes_by_root,
        issues=tuple(issues),
    )


def render_audit(report: AuditReport) -> str:
    lines = [
        f"contract panel: {report.rows:,} rows, {report.contracts:,} contracts, "
        f"{report.start_date or 'n/a'}..{report.end_date or 'n/a'}",
        f"  roots: {', '.join(report.roots) or 'none'}; "
        f"common sessions: {report.common_sessions:,}",
        f"  pricing valid: {'yes' if report.pricing_valid else 'NO'}; "
        f"execution ready: {'yes' if report.ready_for_execution else 'NO'}",
    ]
    for root in report.roots:
        years = report.years_by_root.get(root, ())
        year_span = f"{min(years)}-{max(years)}" if years else "n/a"
        codes = ",".join(report.delivery_month_codes_by_root.get(root, ())) or "none"
        lines.append(
            f"  {root}: {report.rows_by_root.get(root, 0):,} rows, "
            f"years {year_span}, delivery codes {codes}"
        )
    if not report.issues:
        lines.append("  no issues")
    for issue in report.issues:
        examples = f"; examples: {', '.join(issue.examples)}" if issue.examples else ""
        lines.append(
            f"  {issue.severity:7s} {issue.code}: {issue.count} — {issue.message}{examples}"
        )
    return "\n".join(lines)


def reconstruct_front_month(
    frame: pd.DataFrame,
    *,
    roll_business_days: int = 10,
) -> pd.DataFrame:
    """Create an unadjusted, explicit front-month series for reconciliation."""

    required = {"date", "root", "contract", "last_trade_date", "settlement_cents"}
    missing = sorted(required - set(frame))
    if missing:
        raise FuturesDataError(f"cannot reconstruct front month; missing {', '.join(missing)}")
    clean = frame.copy()
    clean["date"] = pd.to_datetime(clean["date"]).dt.normalize()
    clean["last_trade_date"] = pd.to_datetime(clean["last_trade_date"]).dt.normalize()
    clean["business_days_to_last_trade"] = clean.apply(
        lambda row: int(np.busday_count(row["date"].date(), row["last_trade_date"].date())),
        axis=1,
    )
    eligible = clean[clean["business_days_to_last_trade"] > roll_business_days]
    selected = eligible.sort_values(
        ["date", "root", "last_trade_date", "contract"]
    ).drop_duplicates(["date", "root"], keep="first")
    return selected[
        ["date", "root", "contract", "last_trade_date", "settlement_cents"]
    ].reset_index(drop=True)


def compare_continuous_returns(
    front_month: pd.DataFrame,
    reference: pd.DataFrame,
) -> pd.DataFrame:
    """Compare daily returns, which remain meaningful across level adjustments.

    ``reference`` must contain date, root, and close_cents.  The result is a
    diagnostic, not a pass/fail gate: different roll rules create local gaps.
    """

    required = {"date", "root", "close_cents"}
    missing = sorted(required - set(reference))
    if missing:
        raise FuturesDataError(f"continuous reference is missing {', '.join(missing)}")
    left = front_month[["date", "root", "settlement_cents"]].copy()
    right = reference[["date", "root", "close_cents"]].copy()
    left["date"] = pd.to_datetime(left["date"]).dt.normalize()
    right["date"] = pd.to_datetime(right["date"]).dt.normalize()
    merged = left.merge(right, on=["date", "root"], how="inner").sort_values(
        ["root", "date"]
    )
    merged["contract_return"] = merged.groupby("root")["settlement_cents"].pct_change()
    merged["reference_return"] = merged.groupby("root")["close_cents"].pct_change()
    rows = []
    for root, group in merged.dropna().groupby("root"):
        difference = (group["contract_return"] - group["reference_return"]).abs()
        rows.append({
            "root": root,
            "observations": len(group),
            "return_correlation": group["contract_return"].corr(group["reference_return"]),
            "median_absolute_return_difference": difference.median(),
            "max_absolute_return_difference": difference.max(),
        })
    return pd.DataFrame(rows)
