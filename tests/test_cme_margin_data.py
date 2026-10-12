"""CME margin extraction and roll-position attachment."""

from __future__ import annotations

import pandas as pd
import pytest

from pipeline.cme_margin_data import (
    MarginDataError,
    attach_margin_history,
    parse_margin_text,
)


def margin_text() -> str:
    return """Minimum Performance Bond Requirements
Business Date
Description
Exchange
Margin
Roll Product Code
2026-01-02
CORN FUTURES
CBT
1200
C-01
2026-01-02
CORN FUTURES
CBT
900
C-02
"""


def test_parse_margin_text_recovers_business_date_rate_and_roll() -> None:
    frame = parse_margin_text(margin_text(), root="ZC")

    assert frame[[
        "margin_business_date", "root", "margin_roll_index",
        "maintenance_margin_usd", "margin_roll_product_code",
    ]].to_dict("records") == [
        {
            "margin_business_date": "2026-01-02", "root": "ZC",
            "margin_roll_index": 1, "maintenance_margin_usd": 1200.0,
            "margin_roll_product_code": "C-01",
        },
        {
            "margin_business_date": "2026-01-02", "root": "ZC",
            "margin_roll_index": 2, "maintenance_margin_usd": 900.0,
            "margin_roll_product_code": "C-02",
        },
    ]


def test_parse_margin_text_rejects_wrong_product() -> None:
    with pytest.raises(MarginDataError, match="no CME margin rows"):
        parse_margin_text(margin_text(), root="ZW")


def test_attach_margin_history_maps_expiry_order_without_forward_filling() -> None:
    contracts = pd.DataFrame([
        {"date": "2026-01-02", "root": "ZC", "contract": "ZCK26",
         "last_trade_date": "2026-05-14", "open_cents": 410.0},
        {"date": "2026-01-02", "root": "ZC", "contract": "ZCH26",
         "last_trade_date": "2026-03-13", "open_cents": 400.0},
        {"date": "2026-01-05", "root": "ZC", "contract": "ZCH26",
         "last_trade_date": "2026-03-13", "open_cents": 405.0},
    ])
    margins = parse_margin_text(margin_text(), root="ZC")

    result = attach_margin_history(contracts, margins)

    assert result.frame["contract"].tolist() == ["ZCH26", "ZCK26"]
    assert result.frame["maintenance_margin_usd"].tolist() == [1200.0, 900.0]
    assert result.frame["margin_roll_index"].tolist() == [1, 2]
    assert result.within_source_dates == 2
    assert result.matched_rows == 2
    assert result.frame["date"].max() == pd.Timestamp("2026-01-02")


def test_attach_margin_history_refuses_to_overwrite_existing_rates() -> None:
    contracts = pd.DataFrame([{
        "date": "2026-01-02", "root": "ZC", "contract": "ZCH26",
        "last_trade_date": "2026-03-13", "maintenance_margin_usd": 1.0,
    }])
    with pytest.raises(MarginDataError, match="refusing to overwrite"):
        attach_margin_history(contracts, parse_margin_text(margin_text(), root="ZC"))
