"""Workspace futures adapter tests; no live LSEG session is used here."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline.lseg_workspace_futures import (
    ContractRequest,
    contract_requests,
    download_workspace_history,
    lseg_ric,
    normalize_history,
    save_workspace_download,
)


def history_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "OPEN_PRC": [400.0, pd.NA, 402.0],
            "HIGH_1": [405.0, pd.NA, 406.0],
            "LOW_1": [398.0, pd.NA, 399.0],
            "SETTLE": [403.0, 404.0, 405.0],
            "ACVOL_UNS": [1000, 25, 900],
            "OPINT_1": [5000, 40, 4900],
            "EXPIR_DATE": [20241213, 20241213, 20241213],
        },
        index=pd.DatetimeIndex(
            ["2024-11-04", "2024-11-05", "2024-11-06"], name="Date"
        ),
    )


def test_lseg_ric_uses_archive_decade_and_live_forms() -> None:
    as_of = date(2026, 10, 12)
    assert lseg_ric("ZC", "Z", 2012, as_of=as_of) == "CZ2^1"
    assert lseg_ric("ZW", "Z", 2018, as_of=as_of) == "WZ8^1"
    assert lseg_ric("ZC", "H", 2020, as_of=as_of) == "CH0^2"
    assert lseg_ric("ZW", "Z", 2023, as_of=as_of) == "WZ3^2"
    assert lseg_ric("ZC", "Z", 2024, as_of=as_of) == "CZ24^2"
    assert lseg_ric("ZW", "Z", 2026, as_of=as_of) == "WZ26"


def test_contract_requests_have_stable_canonical_symbols() -> None:
    requests = contract_requests([2024], ["ZC", "ZW"], as_of=date(2026, 1, 1))
    assert len(requests) == 10
    assert requests[0] == ContractRequest("ZC", 2024, "H", "ZCH24", "CH24^2")
    assert requests[-1] == ContractRequest("ZW", 2024, "Z", "ZWZ24", "WZ24^2")


def test_normalize_history_parses_expiry_and_records_incomplete_rows() -> None:
    request = ContractRequest("ZC", 2024, "Z", "ZCZ24", "CZ24^2")
    normalized, dropped, invalid = normalize_history(history_frame(), request)
    assert dropped == 1
    assert invalid == 0
    assert len(normalized) == 2
    assert normalized["contract"].unique().tolist() == ["ZCZ24"]
    assert normalized["ric"].unique().tolist() == ["CZ24^2"]
    assert normalized["last_trade_date"].dt.date.unique().tolist() == [date(2024, 12, 13)]
    assert normalized["open"].tolist() == [400.0, 402.0]


def test_normalize_history_excludes_impossible_open_range() -> None:
    history = history_frame()
    history.loc[pd.Timestamp("2024-11-04"), "HIGH_1"] = 399.0
    request = ContractRequest("ZC", 2024, "Z", "ZCZ24", "CZ24^2")
    normalized, incomplete, invalid = normalize_history(history, request)
    assert incomplete == 1
    assert invalid == 1
    assert normalized["date"].dt.date.tolist() == [date(2024, 11, 6)]


class FakeLseg:
    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0
        self.calls: list[dict] = []

    def open_session(self) -> None:
        self.opened += 1

    def close_session(self) -> None:
        self.closed += 1

    def get_history(self, ric: str, **kwargs) -> pd.DataFrame:
        self.calls.append({"ric": ric, **kwargs})
        return history_frame()


def test_download_uses_one_desktop_session_and_preserves_query_details(tmp_path: Path) -> None:
    fake = FakeLseg()
    download = download_workspace_history(
        start=date(2024, 11, 1),
        end=date(2024, 11, 8),
        delivery_years=[2024],
        roots=["ZC"],
        lseg=fake,
        as_of=date(2026, 10, 12),
        downloaded_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
    )
    assert fake.opened == fake.closed == 1
    assert len(fake.calls) == 5
    assert fake.calls[0]["ric"] == "CH24^2"
    assert fake.calls[0]["fields"][-1] == "EXPIR_DATE"
    assert len(download.frame) == 10
    assert download.successful_contracts == 5

    export, manifest, mapping = save_workspace_download(download, tmp_path)
    assert export.exists() and manifest.exists() and mapping.exists()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["session_type"] == "desktop.workspace"
    assert payload["successful_contracts"] == 5
    assert len(payload["export_sha256"]) == 64
    assert "maintenance_margin_usd" not in mapping.read_text(encoding="utf-8")
