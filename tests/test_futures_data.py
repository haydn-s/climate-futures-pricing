"""Vendor-neutral contract imports, provenance, and quality diagnostics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from pipeline.futures_data import (
    FuturesDataError,
    ImportSpec,
    audit_contract_panel,
    compare_continuous_returns,
    import_contract_export,
    normalize_export,
    reconstruct_front_month,
    render_audit,
    save_import,
)
from pipeline.storage import ProcessedStore


def vendor_frame(include_margin: bool = True) -> pd.DataFrame:
    rows = []
    for day, corn, wheat in [
        ("2026-01-02", 400.00, 500.00),
        ("2026-01-05", 410.00, 495.00),
        ("2026-01-06", 415.00, 490.00),
    ]:
        rows.extend([
            {
                "trade_day": day, "commodity": "C", "ticker": "ZCH26",
                "expiry": "2026-03-13", "px_open": corn,
                "px_high": corn + 2, "px_low": corn - 2, "px_settle": corn + 1,
                "vol": 1000, "oi": 5000, "margin": 1000,
            },
            {
                "trade_day": day, "commodity": "W", "ticker": "ZWH26",
                "expiry": "2026-03-13", "px_open": wheat,
                "px_high": wheat + 2, "px_low": wheat - 2, "px_settle": wheat + 1,
                "vol": 800, "oi": 4000, "margin": 2000,
            },
        ])
    frame = pd.DataFrame(rows)
    return frame if include_margin else frame.drop(columns="margin")


def import_spec(include_margin: bool = True) -> ImportSpec:
    columns = {
        "date": "trade_day",
        "root": "commodity",
        "contract": "ticker",
        "last_trade_date": "expiry",
        "open": "px_open",
        "high": "px_high",
        "low": "px_low",
        "settlement": "px_settle",
        "volume": "vol",
        "open_interest": "oi",
    }
    if include_margin:
        columns["maintenance_margin_usd"] = "margin"
    return ImportSpec(
        source_name="synthetic_vendor",
        source_url="https://example.test",
        license_note="synthetic test data",
        query_reference="test query",
        columns=columns,
        root_values={"C": "ZC", "W": "ZW"},
        price_multiplier_to_cents=1.0,
    )


def write_mapping(path: Path, include_margin: bool = True) -> Path:
    margin = "  maintenance_margin_usd: margin\n" if include_margin else ""
    path.write_text(
        "schema_version: 1\n"
        "source_name: synthetic_vendor\n"
        "source_url: https://example.test\n"
        "license_note: synthetic test data\n"
        "query_reference: test query\n"
        "columns:\n"
        "  date: trade_day\n"
        "  root: commodity\n"
        "  contract: ticker\n"
        "  last_trade_date: expiry\n"
        "  open: px_open\n"
        "  high: px_high\n"
        "  low: px_low\n"
        "  settlement: px_settle\n"
        "  volume: vol\n"
        "  open_interest: oi\n"
        f"{margin}"
        "root_values:\n"
        "  C: ZC\n"
        "  W: ZW\n"
        "price_multiplier_to_cents: 1.0\n",
        encoding="utf-8",
    )
    return path


def test_normalize_export_maps_columns_roots_dates_and_prices() -> None:
    normalized = normalize_export(vendor_frame(), import_spec())
    assert normalized.columns.tolist() == [
        "date", "root", "contract", "last_trade_date", "open_cents",
        "high_cents", "low_cents", "settlement_cents", "volume",
        "open_interest", "maintenance_margin_usd",
    ]
    assert set(normalized["root"]) == {"ZC", "ZW"}
    assert pd.api.types.is_datetime64_any_dtype(normalized["date"])
    assert normalized.loc[normalized["root"] == "ZC", "open_cents"].iloc[0] == 400


def test_price_multiplier_converts_dollars_to_cents() -> None:
    frame = vendor_frame()
    for column in ("px_open", "px_high", "px_low", "px_settle"):
        frame[column] /= 100
    spec = import_spec()
    spec = ImportSpec(**{**spec.__dict__, "price_multiplier_to_cents": 100.0})
    normalized = normalize_export(frame, spec)
    assert normalized.iloc[0]["open_cents"] == 400


def test_unmapped_vendor_root_is_refused() -> None:
    frame = vendor_frame()
    frame.loc[0, "commodity"] = "SOY"
    with pytest.raises(FuturesDataError, match="SOY"):
        normalize_export(frame, import_spec())


def test_cocoa_only_panel_is_audited_without_requiring_grain_roots() -> None:
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
        "root": ["CC", "CC"],
        "contract": ["CCH26", "CCH26"],
        "last_trade_date": pd.to_datetime(["2026-03-13", "2026-03-13"]),
        "open_cents": [500_000.0, 501_000.0],
        "high_cents": [501_000.0, 502_000.0],
        "low_cents": [499_000.0, 500_000.0],
        "settlement_cents": [500_000.0, 501_000.0],
        "volume": [1000, 1100],
        "open_interest": [5000, 4900],
    })
    report = audit_contract_panel(frame)
    assert report.pricing_valid
    assert report.roots == ("CC",)
    assert not any(issue.code in {"UNKNOWN_ROOTS", "MISSING_ROOTS"} for issue in report.issues)
    assert any(issue.code == "MISSING_MARGIN" for issue in report.issues)


def test_coffee_only_panel_uses_five_cent_tick_audit() -> None:
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
        "root": ["KC", "KC"],
        "contract": ["KCH26", "KCH26"],
        "last_trade_date": pd.to_datetime(["2026-03-18", "2026-03-18"]),
        "open_cents": [300.00, 301.05],
        "high_cents": [302.00, 303.05],
        "low_cents": [299.00, 300.05],
        "settlement_cents": [301.00, 302.05],
        "volume": [1000, 1100],
        "open_interest": [5000, 4900],
    })
    report = audit_contract_panel(frame)
    assert report.pricing_valid
    assert report.roots == ("KC",)
    assert not any(issue.code == "OFF_TICK_PRICE" for issue in report.issues)
    assert any(issue.code == "MISSING_MARGIN" for issue in report.issues)


def test_import_manifest_hashes_inputs_and_preserves_license_note(tmp_path: Path) -> None:
    source = tmp_path / "contracts.csv"
    vendor_frame().to_csv(source, index=False)
    mapping = write_mapping(tmp_path / "mapping.yaml")
    imported = import_contract_export(
        source, mapping, imported_at=datetime(2026, 10, 12, tzinfo=timezone.utc)
    )
    assert len(imported.manifest["input_sha256"]) == 64
    assert len(imported.manifest["mapping_sha256"]) == 64
    assert imported.manifest["license_note"] == "synthetic test data"
    assert imported.manifest["audit"]["ready_for_execution"] is True


def test_saved_import_writes_parquet_and_sidecar_manifest(tmp_path: Path) -> None:
    source = tmp_path / "contracts.csv"
    vendor_frame().to_csv(source, index=False)
    result = import_contract_export(source, write_mapping(tmp_path / "mapping.yaml"))
    table, manifest = save_import(result, ProcessedStore(tmp_path), name="contracts_test")
    assert table.exists() and manifest.exists()
    assert pd.read_parquet(table).shape == result.frame.shape
    assert json.loads(manifest.read_text())["processed_table"] == "contracts_test.parquet"


def test_audit_distinguishes_valid_prices_from_missing_margin() -> None:
    normalized = normalize_export(vendor_frame(False), import_spec(False))
    report = audit_contract_panel(normalized)
    assert report.pricing_valid is True
    assert report.ready_for_execution is False
    assert any(issue.code == "MISSING_MARGIN" for issue in report.issues)
    assert "execution ready: NO" in render_audit(report)
    assert report.rows_by_root == {"ZC": 3, "ZW": 3}
    assert report.years_by_root == {"ZC": (2026,), "ZW": (2026,)}
    assert report.delivery_month_codes_by_root == {"ZC": ("H",), "ZW": ("H",)}


def test_import_audit_requires_every_root_declared_by_mapping(tmp_path: Path) -> None:
    source = tmp_path / "contracts.csv"
    vendor_frame().loc[lambda rows: rows["commodity"] == "C"].to_csv(source, index=False)
    imported = import_contract_export(source, write_mapping(tmp_path / "mapping.yaml"))
    assert not imported.manifest["audit"]["pricing_valid"]
    assert any(
        issue["code"] == "MISSING_ROOTS" for issue in imported.manifest["audit"]["issues"]
    )


def test_audit_detects_duplicate_and_impossible_ohlc() -> None:
    normalized = normalize_export(vendor_frame(), import_spec())
    normalized.loc[0, "high_cents"] = normalized.loc[0, "low_cents"] - 1
    dirty = pd.concat([normalized, normalized.iloc[[0]]], ignore_index=True)
    report = audit_contract_panel(dirty)
    codes = {issue.code for issue in report.issues}
    assert {"DUPLICATE_ROWS", "INVALID_OHLC"}.issubset(codes)
    assert not report.pricing_valid


def test_settlement_outside_session_range_is_disclosed_but_not_invalid() -> None:
    normalized = normalize_export(vendor_frame(), import_spec())
    normalized.loc[0, "settlement_cents"] = normalized.loc[0, "high_cents"] + 1
    corn = normalized["contract"] == "ZCH26"
    normalized.loc[corn, "last_trade_date"] = normalized.loc[corn, "date"].max()
    report = audit_contract_panel(normalized)
    assert report.pricing_valid
    assert any(issue.code == "SETTLEMENT_OUTSIDE_RANGE" for issue in report.issues)
    assert not any(issue.code == "AFTER_LAST_TRADE" for issue in report.issues)


def test_audit_detects_bad_contract_month_and_reports_session_gaps() -> None:
    normalized = normalize_export(vendor_frame(), import_spec())
    normalized.loc[0, "contract"] = "ZCF26"  # January is not listed for corn
    # Remove Monday from wheat, leaving a weekday gap from Friday to Tuesday.
    normalized = normalized.drop(
        normalized[(normalized["root"] == "ZW")
                   & (normalized["date"] == pd.Timestamp("2026-01-05"))].index
    )
    report = audit_contract_panel(normalized)
    codes = {issue.code for issue in report.issues}
    assert "INVALID_CONTRACT_CODE" in codes
    assert "POTENTIAL_SESSION_GAPS" in codes


def test_front_month_reconstruction_skips_near_expiry_contract() -> None:
    normalized = normalize_export(vendor_frame(), import_spec())
    near = normalized[normalized["root"] == "ZC"].copy()
    near["contract"] = "ZCZ25"
    near["last_trade_date"] = pd.Timestamp("2026-01-08")
    combined = pd.concat([near, normalized], ignore_index=True)
    front = reconstruct_front_month(combined, roll_business_days=4)
    corn = front[front["root"] == "ZC"]
    assert set(corn["contract"]) == {"ZCH26"}


def test_continuous_comparison_uses_returns_not_adjusted_price_levels() -> None:
    normalized = normalize_export(vendor_frame(), import_spec())
    front = reconstruct_front_month(normalized, roll_business_days=0)
    reference = front[["date", "root", "settlement_cents"]].rename(
        columns={"settlement_cents": "close_cents"}
    )
    reference["close_cents"] *= 2  # level differs, returns do not
    comparison = compare_continuous_returns(front, reference)
    assert comparison["return_correlation"].tolist() == pytest.approx([1.0, 1.0])
    assert comparison["median_absolute_return_difference"].max() == pytest.approx(0)
