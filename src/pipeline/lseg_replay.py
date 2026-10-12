"""Replay frozen corn/wheat signals against dated LSEG futures contracts.

This is an effectiveness study, not a margin backtest.  It answers two narrower
questions without changing the fitted model or its threshold:

* does the original Yahoo continuous-series target agree with a dated-contract
  settlement target on the same decision and outcome dates; and
* what would the frozen action earn from the next shared session open through
  the configured holding horizon, after explicit commission and slippage?

Execution returns use average leg notional as the denominator.  That keeps a
dollar-neutral long/short return comparable with ``corn return - wheat return``
without pretending that the missing historical margin series is known.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from .futures_execution import (
    ContractSpec,
    ExecutionConfig,
    ExecutionError,
    load_execution_config,
    select_contract,
)


REQUIRED_PREDICTION_COLUMNS = frozenset({
    "date", "target_end_date", "year", "actual", "signal", "fold", "sample",
})
REQUIRED_CONTRACT_COLUMNS = frozenset({
    "date", "root", "contract", "last_trade_date", "open_cents",
    "settlement_cents",
})


class ReplayError(ValueError):
    """A frozen prediction or contract panel cannot support the replay."""


@dataclass(frozen=True)
class PriceOnlySize:
    corn_contracts: int
    wheat_contracts: int
    corn_notional_usd: float
    wheat_notional_usd: float
    imbalance_fraction: float

    @property
    def average_leg_notional_usd(self) -> float:
        return (self.corn_notional_usd + self.wheat_notional_usd) / 2.0


def size_price_only_spread(
    corn_price_cents: float,
    wheat_price_cents: float,
    *,
    specs: Mapping[str, ContractSpec],
    max_contracts_per_leg: int,
) -> PriceOnlySize:
    """Choose the least-imbalanced small-integer pair without using margin."""

    if not all(np.isfinite(value) and value > 0
               for value in (corn_price_cents, wheat_price_cents)):
        raise ReplayError("entry prices must be finite and positive")
    if max_contracts_per_leg < 1:
        raise ReplayError("max_contracts_per_leg must be at least 1")
    candidates: list[tuple[float, int, int, int, float, float]] = []
    for corn_qty in range(1, max_contracts_per_leg + 1):
        for wheat_qty in range(1, max_contracts_per_leg + 1):
            corn_notional = specs["ZC"].notional_usd(corn_price_cents, corn_qty)
            wheat_notional = specs["ZW"].notional_usd(wheat_price_cents, wheat_qty)
            gross = corn_notional + wheat_notional
            imbalance = abs(corn_notional - wheat_notional) / gross
            candidates.append((
                imbalance, corn_qty + wheat_qty, corn_qty, wheat_qty,
                corn_notional, wheat_notional,
            ))
    imbalance, _, corn_qty, wheat_qty, corn_notional, wheat_notional = min(candidates)
    return PriceOnlySize(
        corn_contracts=corn_qty,
        wheat_contracts=wheat_qty,
        corn_notional_usd=corn_notional,
        wheat_notional_usd=wheat_notional,
        imbalance_fraction=imbalance,
    )


def prepare_contracts(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize the price fields required by a margin-free LSEG replay."""
    missing = sorted(REQUIRED_CONTRACT_COLUMNS - set(frame.columns))
    if missing:
        raise ReplayError(f"contract panel is missing: {', '.join(missing)}")
    clean = frame.copy()
    for column in ("date", "last_trade_date"):
        clean[column] = pd.to_datetime(clean[column], errors="coerce").dt.normalize()
    for column in ("open_cents", "settlement_cents"):
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    required = ["date", "last_trade_date", "open_cents", "settlement_cents"]
    if clean[required].isna().any().any():
        raise ReplayError("contract dates, opens, and settlements must be present")
    if (clean[["open_cents", "settlement_cents"]] <= 0).any().any():
        raise ReplayError("contract prices must be positive")
    if clean.duplicated(["date", "root", "contract"]).any():
        raise ReplayError("contract panel has duplicate date/root/contract rows")
    if set(clean["root"]) != {"ZC", "ZW"}:
        raise ReplayError("contract panel must contain exactly ZC and ZW")
    if (clean["date"] > clean["last_trade_date"]).any():
        raise ReplayError("contract observations cannot follow last_trade_date")
    return clean.sort_values(
        ["date", "root", "last_trade_date", "contract"]
    ).reset_index(drop=True)


def _prepare_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_PREDICTION_COLUMNS - set(frame.columns))
    if missing:
        raise ReplayError(f"prediction frame is missing: {', '.join(missing)}")
    clean = frame.copy()
    for column in ("date", "target_end_date"):
        clean[column] = pd.to_datetime(clean[column], errors="coerce").dt.normalize()
    clean["signal"] = pd.to_numeric(clean["signal"], errors="coerce")
    if clean[["date", "target_end_date", "signal"]].isna().any().any():
        raise ReplayError("prediction dates and signals must be present")
    if not clean["signal"].isin([-1, 0, 1]).all():
        raise ReplayError("signals must be -1, 0, or 1")
    if clean.duplicated("date").any():
        raise ReplayError("prediction frame contains duplicate decision dates")
    return clean.sort_values("date").reset_index(drop=True)


def _selected_rows(
    panel: pd.DataFrame,
    *,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    price_column: str,
    roll_business_days: int,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    entries: dict[str, pd.Series] = {}
    exits: dict[str, pd.Series] = {}
    for root in ("ZC", "ZW"):
        entries[root] = select_contract(
            panel,
            root=root,
            entry_date=entry_date,
            exit_date=exit_date,
            roll_business_days=roll_business_days,
        )
        symbol = entries[root]["contract"]
        match = panel[
            (panel["date"] == exit_date)
            & (panel["root"] == root)
            & (panel["contract"] == symbol)
        ]
        if match.empty:
            raise ExecutionError(
                f"{symbol} has no {price_column} on exit date {exit_date.date()}"
            )
        exits[root] = match.iloc[0]
    return entries, exits


def _leg_pnl(
    entry: float,
    exit: float,
    *,
    quantity: int,
    side: int,
    spec: ContractSpec,
) -> float:
    return side * quantity * (exit - entry) * spec.contract_size_bushels / 100.0


def replay_frozen_signals(
    predictions: pd.DataFrame,
    contracts: pd.DataFrame,
    *,
    config: ExecutionConfig | None = None,
    roll_business_days: int | None = None,
    commission_usd_per_contract_side: float | None = None,
    slippage_ticks_per_contract_side: float | None = None,
) -> pd.DataFrame:
    """Attach LSEG close targets and next-open outcomes to frozen predictions."""

    config = config or load_execution_config()
    assumptions = config.assumptions
    roll_days = (
        assumptions.roll_business_days
        if roll_business_days is None else int(roll_business_days)
    )
    commission = (
        assumptions.commission_usd_per_contract_side
        if commission_usd_per_contract_side is None
        else float(commission_usd_per_contract_side)
    )
    slippage_ticks = (
        assumptions.slippage_ticks_per_contract_side
        if slippage_ticks_per_contract_side is None
        else float(slippage_ticks_per_contract_side)
    )
    if roll_days < 0 or commission < 0 or slippage_ticks < 0:
        raise ReplayError("roll, commission, and slippage assumptions cannot be negative")

    panel = prepare_contracts(contracts)
    decisions = _prepare_predictions(predictions)
    root_dates = {
        root: set(panel.loc[panel["root"] == root, "date"])
        for root in ("ZC", "ZW")
    }
    common_sessions = sorted(root_dates["ZC"] & root_dates["ZW"])
    rows: list[dict[str, object]] = []
    previous_trade_exit: pd.Timestamp | None = None

    for decision in decisions.itertuples(index=False):
        base = decision._asdict()
        base.update({
            "roll_business_days": roll_days,
            "commission_usd_per_contract_side": commission,
            "slippage_ticks_per_contract_side": slippage_ticks,
        })
        row: dict[str, object] = dict(base)
        try:
            close_entries, close_exits = _selected_rows(
                panel,
                entry_date=decision.date,
                exit_date=decision.target_end_date,
                price_column="settlement_cents",
                roll_business_days=roll_days,
            )
            corn_close_return = (
                float(close_exits["ZC"]["settlement_cents"])
                / float(close_entries["ZC"]["settlement_cents"]) - 1.0
            )
            wheat_close_return = (
                float(close_exits["ZW"]["settlement_cents"])
                / float(close_entries["ZW"]["settlement_cents"]) - 1.0
            )
            row.update({
                "close_status": "OK",
                "close_corn_contract": close_entries["ZC"]["contract"],
                "close_wheat_contract": close_entries["ZW"]["contract"],
                "lseg_close_corn_return": corn_close_return,
                "lseg_close_wheat_return": wheat_close_return,
                "lseg_close_spread_return": corn_close_return - wheat_close_return,
            })
        except (ExecutionError, IndexError) as exc:
            row.update({"close_status": "UNAVAILABLE", "close_error": str(exc)})

        later = [session for session in common_sessions if session > decision.date]
        if len(later) <= assumptions.holding_sessions:
            row.update({
                "execution_status": "SKIPPED_NO_EXIT",
                "execution_error": "too few common sessions after decision",
            })
            rows.append(row)
            continue
        entry_date = later[0]
        exit_date = later[assumptions.holding_sessions]
        try:
            entries, exits = _selected_rows(
                panel,
                entry_date=entry_date,
                exit_date=exit_date,
                price_column=assumptions.fill_price_column,
                roll_business_days=roll_days,
            )
            size = size_price_only_spread(
                float(entries["ZC"][assumptions.fill_price_column]),
                float(entries["ZW"][assumptions.fill_price_column]),
                specs=config.contracts,
                max_contracts_per_leg=assumptions.max_contracts_per_leg,
            )
            quantities = {"ZC": size.corn_contracts, "ZW": size.wheat_contracts}
            nearest = {
                root: panel[
                    (panel["date"] == entry_date) & (panel["root"] == root)
                ].sort_values(["last_trade_date", "contract"]).iloc[0]
                for root in ("ZC", "ZW")
            }
            unsigned_pnl = sum([
                _leg_pnl(
                    float(entries["ZC"][assumptions.fill_price_column]),
                    float(exits["ZC"][assumptions.fill_price_column]),
                    quantity=quantities["ZC"], side=1,
                    spec=config.contracts["ZC"],
                ),
                _leg_pnl(
                    float(entries["ZW"][assumptions.fill_price_column]),
                    float(exits["ZW"][assumptions.fill_price_column]),
                    quantity=quantities["ZW"], side=-1,
                    spec=config.contracts["ZW"],
                ),
            ])
            total_contracts = size.corn_contracts + size.wheat_contracts
            commission_usd = 2 * total_contracts * commission
            slippage_usd = 2 * sum(
                quantities[root] * slippage_ticks * config.contracts[root].tick_value_usd
                for root in ("ZC", "ZW")
            )
            cost_usd = commission_usd + slippage_usd
            denominator = size.average_leg_notional_usd
            unsigned_return = unsigned_pnl / denominator
            signal = int(decision.signal)
            overlap = bool(
                signal != 0
                and previous_trade_exit is not None
                and entry_date <= previous_trade_exit
            )
            skipped_overlap = overlap and not assumptions.allow_overlapping_positions
            execution_signal = 0 if skipped_overlap else signal
            if signal == 0:
                execution_status = "HOLD"
            elif skipped_overlap:
                execution_status = "SKIPPED_OVERLAP"
            else:
                execution_status = "EXECUTED"
            row.update({
                "execution_status": execution_status,
                "execution_signal": execution_signal,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "corn_contract": entries["ZC"]["contract"],
                "wheat_contract": entries["ZW"]["contract"],
                "corn_nearest_contract": nearest["ZC"]["contract"],
                "wheat_nearest_contract": nearest["ZW"]["contract"],
                "corn_rolled_forward": (
                    entries["ZC"]["contract"] != nearest["ZC"]["contract"]
                ),
                "wheat_rolled_forward": (
                    entries["ZW"]["contract"] != nearest["ZW"]["contract"]
                ),
                "corn_business_days_to_last_trade_at_exit": int(np.busday_count(
                    exit_date.date(), entries["ZC"]["last_trade_date"].date()
                )),
                "wheat_business_days_to_last_trade_at_exit": int(np.busday_count(
                    exit_date.date(), entries["ZW"]["last_trade_date"].date()
                )),
                "corn_contracts": size.corn_contracts,
                "wheat_contracts": size.wheat_contracts,
                "corn_entry_cents": float(entries["ZC"][assumptions.fill_price_column]),
                "corn_exit_cents": float(exits["ZC"][assumptions.fill_price_column]),
                "wheat_entry_cents": float(entries["ZW"][assumptions.fill_price_column]),
                "wheat_exit_cents": float(exits["ZW"][assumptions.fill_price_column]),
                "corn_notional_usd": size.corn_notional_usd,
                "wheat_notional_usd": size.wheat_notional_usd,
                "average_leg_notional_usd": denominator,
                "notional_imbalance_fraction": size.imbalance_fraction,
                "execution_unsigned_gross_return": unsigned_return,
                "execution_gross_pnl_usd": execution_signal * unsigned_pnl,
                "execution_commission_usd": abs(execution_signal) * commission_usd,
                "execution_slippage_usd": abs(execution_signal) * slippage_usd,
                "execution_cost_return": abs(execution_signal) * cost_usd / denominator,
                "execution_gross_return": execution_signal * unsigned_return,
                "execution_net_return": (
                    execution_signal * unsigned_return
                    - abs(execution_signal) * cost_usd / denominator
                ),
                "overlaps_previous_trade": overlap,
            })
            if execution_signal != 0:
                previous_trade_exit = exit_date
        except (ExecutionError, ReplayError, IndexError) as exc:
            row.update({
                "execution_status": "SKIPPED_NO_CONTRACT",
                "entry_date": entry_date,
                "exit_date": exit_date,
                "execution_error": str(exc),
            })
        rows.append(row)
    return pd.DataFrame(rows)
