"""Contract-realistic execution for the corn/wheat paper-trading signal.

The research target is built from continuous price series.  Those series are
useful for modelling, but they are not orders: a trade needs a dated contract,
an integer quantity, a fill convention, transaction costs, and sufficient
margin.  This module makes those decisions explicit and leaves a row-level
ledger suitable for audit.

It deliberately does not synthesize individual contracts from a continuous
series.  Callers must provide actual contract observations and dated maintenance
margin.  Missing execution data is an error rather than an invitation to make a
backtest look smoother than the market was.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXECUTION_CONFIG = "config/futures_execution.yaml"

REQUIRED_CONTRACT_COLUMNS = frozenset({
    "date",
    "root",
    "contract",
    "last_trade_date",
    "open_cents",
    "maintenance_margin_usd",
})
VALID_ACTIONS = frozenset({"BUY", "SELL", "HOLD"})


class ExecutionError(ValueError):
    """Contract data or a requested simulation is not executable."""


@dataclass(frozen=True)
class ContractSpec:
    """Exchange-defined mechanics for one futures root."""

    root: str
    name: str
    contract_size_bushels: int
    tick_size_cents: float
    tick_value_usd: float
    listed_month_codes: tuple[str, ...]
    source_url: str

    def __post_init__(self) -> None:
        if self.contract_size_bushels <= 0:
            raise ExecutionError(f"{self.root}: contract size must be positive")
        if self.tick_size_cents <= 0 or self.tick_value_usd <= 0:
            raise ExecutionError(f"{self.root}: tick size and value must be positive")
        implied = self.contract_size_bushels * self.tick_size_cents / 100.0
        if not np.isclose(implied, self.tick_value_usd):
            raise ExecutionError(
                f"{self.root}: tick value ${self.tick_value_usd:g} disagrees with "
                f"{self.contract_size_bushels:,} bushels * "
                f"{self.tick_size_cents:g} cents = ${implied:g}"
            )
        if not self.listed_month_codes:
            raise ExecutionError(f"{self.root}: at least one listed month is required")

    def notional_usd(self, price_cents: float, quantity: int = 1) -> float:
        return price_cents * self.contract_size_bushels * quantity / 100.0


@dataclass(frozen=True)
class ExecutionAssumptions:
    """Research choices that are not exchange contract specifications."""

    fill_price_column: str = "open_cents"
    holding_sessions: int = 5
    roll_business_days: int = 10
    commission_usd_per_contract_side: float = 2.50
    slippage_ticks_per_contract_side: float = 1.0
    max_contracts_per_leg: int = 8
    max_margin_fraction: float = 1.0
    allow_overlapping_positions: bool = False

    def __post_init__(self) -> None:
        if self.holding_sessions < 1:
            raise ExecutionError("holding_sessions must be at least 1")
        if self.roll_business_days < 0:
            raise ExecutionError("roll_business_days cannot be negative")
        if self.commission_usd_per_contract_side < 0:
            raise ExecutionError("commission cannot be negative")
        if self.slippage_ticks_per_contract_side < 0:
            raise ExecutionError("slippage cannot be negative")
        if self.max_contracts_per_leg < 1:
            raise ExecutionError("max_contracts_per_leg must be at least 1")
        if not 0 < self.max_margin_fraction <= 1:
            raise ExecutionError("max_margin_fraction must be in (0, 1]")


@dataclass(frozen=True)
class SpreadSize:
    corn_contracts: int
    wheat_contracts: int
    corn_notional_usd: float
    wheat_notional_usd: float
    imbalance_fraction: float
    maintenance_margin_usd: float


@dataclass(frozen=True)
class ExecutionConfig:
    contracts: Mapping[str, ContractSpec]
    assumptions: ExecutionAssumptions


def load_execution_config(path: str | Path = DEFAULT_EXECUTION_CONFIG) -> ExecutionConfig:
    """Load exchange specifications and explicitly labelled research choices."""

    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    try:
        raw = yaml.safe_load(config_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ExecutionError(f"cannot load execution config {config_path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ExecutionError("execution config must be a mapping with schema_version: 1")
    if set(raw) != {"schema_version", "contracts", "execution"}:
        raise ExecutionError(
            "execution config keys must be schema_version, contracts, and execution"
        )
    try:
        contracts = {
            root: ContractSpec(
                root=root,
                name=values["name"],
                contract_size_bushels=int(values["contract_size_bushels"]),
                tick_size_cents=float(values["tick_size_cents"]),
                tick_value_usd=float(values["tick_value_usd"]),
                listed_month_codes=tuple(values["listed_month_codes"]),
                source_url=values["source_url"],
            )
            for root, values in raw["contracts"].items()
        }
        assumptions = ExecutionAssumptions(**raw["execution"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExecutionError(f"invalid execution config {config_path}: {exc}") from exc
    if set(contracts) != {"ZC", "ZW"}:
        raise ExecutionError("execution config must define exactly ZC and ZW")
    return ExecutionConfig(contracts=contracts, assumptions=assumptions)


def validate_contract_panel(
    frame: pd.DataFrame,
    specs: Mapping[str, ContractSpec],
    *,
    fill_price_column: str = "open_cents",
) -> pd.DataFrame:
    """Normalize and validate individual-contract observations.

    Prices are cents per bushel.  Maintenance margin is a dated dollar amount
    for one contract; it is intentionally data, not a constant in this module.
    """

    required = set(REQUIRED_CONTRACT_COLUMNS) | {fill_price_column}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ExecutionError(f"contract panel is missing columns: {', '.join(missing)}")

    clean = frame.copy()
    for column in ("date", "last_trade_date"):
        clean[column] = pd.to_datetime(clean[column], errors="coerce").dt.normalize()
    for column in (fill_price_column, "maintenance_margin_usd"):
        clean[column] = pd.to_numeric(clean[column], errors="coerce")

    if clean[
        ["date", "last_trade_date", fill_price_column, "maintenance_margin_usd"]
    ].isna().any().any():
        raise ExecutionError(
            "contract dates, fill prices, and dated maintenance margins must be present"
        )
    if (clean[fill_price_column] <= 0).any():
        raise ExecutionError("contract fill prices must be positive")
    if (clean["maintenance_margin_usd"] <= 0).any():
        raise ExecutionError("dated maintenance margins must be positive")
    if clean.duplicated(["date", "root", "contract"]).any():
        raise ExecutionError("contract panel has duplicate date/root/contract rows")
    unknown = sorted(set(clean["root"]) - set(specs))
    if unknown:
        raise ExecutionError(f"contract panel contains unknown roots: {', '.join(unknown)}")
    if (clean["last_trade_date"] < clean["date"]).any():
        raise ExecutionError("contract observations cannot follow last_trade_date")

    for row in clean[["root", "contract"]].drop_duplicates().itertuples(index=False):
        spec = specs[row.root]
        symbol = str(row.contract)
        suffix = symbol[len(row.root):]
        if not symbol.startswith(row.root) or len(suffix) < 2:
            raise ExecutionError(f"contract {symbol!r} does not match root {row.root}")
        if suffix[0] not in spec.listed_month_codes:
            raise ExecutionError(
                f"contract {symbol!r} uses unlisted month code {suffix[0]!r}"
            )
    return clean.sort_values(["date", "root", "last_trade_date", "contract"]).reset_index(drop=True)


def _business_days(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return int(np.busday_count(start.date(), end.date()))


def select_contract(
    panel: pd.DataFrame,
    *,
    root: str,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    roll_business_days: int,
) -> pd.Series:
    """Choose the nearest contract that remains outside its roll window at exit."""

    entry_date = pd.Timestamp(entry_date).normalize()
    exit_date = pd.Timestamp(exit_date).normalize()
    on_entry = panel[(panel["date"] == entry_date) & (panel["root"] == root)].copy()
    if on_entry.empty:
        raise ExecutionError(f"no {root} contracts on entry date {entry_date.date()}")
    on_entry["days_after_exit"] = on_entry["last_trade_date"].map(
        lambda value: _business_days(exit_date, value)
    )
    eligible = on_entry[on_entry["days_after_exit"] > roll_business_days]
    exit_symbols = set(
        panel.loc[(panel["date"] == exit_date) & (panel["root"] == root), "contract"]
    )
    eligible = eligible[eligible["contract"].isin(exit_symbols)]
    if eligible.empty:
        raise ExecutionError(
            f"no {root} contract on {entry_date.date()} remains more than "
            f"{roll_business_days} business days from last trade at the "
            f"{exit_date.date()} exit"
        )
    return eligible.sort_values(["last_trade_date", "contract"]).iloc[0]


def size_dollar_neutral_spread(
    corn_price_cents: float,
    wheat_price_cents: float,
    *,
    corn_spec: ContractSpec,
    wheat_spec: ContractSpec,
    corn_margin_usd: float,
    wheat_margin_usd: float,
    max_contracts_per_leg: int,
    margin_budget_usd: float | None = None,
) -> SpreadSize:
    """Find the small-integer pair with the lowest notional imbalance."""

    inputs = (corn_price_cents, wheat_price_cents, corn_margin_usd, wheat_margin_usd)
    if not all(np.isfinite(value) and value > 0 for value in inputs):
        raise ExecutionError("prices and dated maintenance margins must be positive")
    if max_contracts_per_leg < 1:
        raise ExecutionError("max_contracts_per_leg must be at least 1")

    candidates: list[tuple[float, int, int, int, float, float, float]] = []
    for corn_qty in range(1, max_contracts_per_leg + 1):
        for wheat_qty in range(1, max_contracts_per_leg + 1):
            corn_notional = corn_spec.notional_usd(corn_price_cents, corn_qty)
            wheat_notional = wheat_spec.notional_usd(wheat_price_cents, wheat_qty)
            gross = corn_notional + wheat_notional
            imbalance = abs(corn_notional - wheat_notional) / gross
            margin = corn_qty * corn_margin_usd + wheat_qty * wheat_margin_usd
            if margin_budget_usd is not None and margin > margin_budget_usd:
                continue
            candidates.append(
                (imbalance, corn_qty + wheat_qty, corn_qty, wheat_qty,
                 corn_notional, wheat_notional, margin)
            )
    if not candidates:
        raise ExecutionError("margin budget cannot fund one contract in each leg")
    imbalance, _, corn_qty, wheat_qty, corn_notional, wheat_notional, margin = min(candidates)
    return SpreadSize(
        corn_contracts=corn_qty,
        wheat_contracts=wheat_qty,
        corn_notional_usd=corn_notional,
        wheat_notional_usd=wheat_notional,
        imbalance_fraction=imbalance,
        maintenance_margin_usd=margin,
    )


def _leg_pnl(
    *,
    side: int,
    quantity: int,
    entry_price_cents: float,
    exit_price_cents: float,
    spec: ContractSpec,
) -> float:
    return side * quantity * (exit_price_cents - entry_price_cents) * (
        spec.contract_size_bushels / 100.0
    )


def simulate_spread_trades(
    signals: pd.DataFrame,
    contracts: pd.DataFrame,
    *,
    config: ExecutionConfig | None = None,
    capital_usd: float | None = None,
) -> pd.DataFrame:
    """Execute BUY/SELL/HOLD decisions as corn/wheat spread trades.

    A decision fills at the next common contract session's configured price and
    exits after ``holding_sessions`` further common sessions.  BUY means long
    corn/short wheat; SELL means short corn/long wheat.  Unless explicitly
    enabled, a new position is skipped while an earlier one remains open.
    """

    config = config or load_execution_config()
    assumptions = config.assumptions
    if capital_usd is not None and capital_usd <= 0:
        raise ExecutionError("capital_usd must be positive")
    if capital_usd is not None and assumptions.allow_overlapping_positions:
        raise ExecutionError(
            "capital-constrained simulation requires non-overlapping positions"
        )
    if not {"decision_date", "action"}.issubset(signals.columns):
        raise ExecutionError("signals must contain decision_date and action")

    decisions = signals[["decision_date", "action"]].copy()
    decisions["decision_date"] = pd.to_datetime(
        decisions["decision_date"], errors="coerce"
    ).dt.normalize()
    decisions["action"] = decisions["action"].astype(str).str.upper()
    if decisions["decision_date"].isna().any():
        raise ExecutionError("signal decision dates must be parseable")
    invalid_actions = sorted(set(decisions["action"]) - VALID_ACTIONS)
    if invalid_actions:
        raise ExecutionError(f"unknown signal actions: {', '.join(invalid_actions)}")
    if decisions.duplicated("decision_date").any():
        raise ExecutionError("signals contain duplicate decision dates")
    decisions = decisions.sort_values("decision_date")

    panel = validate_contract_panel(
        contracts, config.contracts, fill_price_column=assumptions.fill_price_column
    )
    root_dates = {
        root: set(panel.loc[panel["root"] == root, "date"])
        for root in ("ZC", "ZW")
    }
    common_sessions = sorted(root_dates["ZC"] & root_dates["ZW"])
    if len(common_sessions) <= assumptions.holding_sessions:
        raise ExecutionError("contract panel has too few common ZC/ZW sessions")

    rows: list[dict[str, object]] = []
    open_through: pd.Timestamp | None = None
    account_equity = float(capital_usd) if capital_usd is not None else None
    for decision in decisions.itertuples(index=False):
        base = {"decision_date": decision.decision_date, "action": decision.action}
        if account_equity is not None:
            base["capital_before_usd"] = account_equity
        if decision.action == "HOLD":
            rows.append({**base, "status": "HOLD"})
            continue
        later = [date for date in common_sessions if date > decision.decision_date]
        if len(later) <= assumptions.holding_sessions:
            rows.append({**base, "status": "SKIPPED_NO_EXIT"})
            continue
        entry_date = later[0]
        exit_date = later[assumptions.holding_sessions]
        if (
            not assumptions.allow_overlapping_positions
            and open_through is not None
            and entry_date <= open_through
        ):
            rows.append({
                **base,
                "status": "SKIPPED_OVERLAP",
                "entry_date": entry_date,
                "exit_date": exit_date,
            })
            continue

        selected: dict[str, pd.Series] = {}
        exits: dict[str, pd.Series] = {}
        try:
            for root in ("ZC", "ZW"):
                selected[root] = select_contract(
                    panel,
                    root=root,
                    entry_date=entry_date,
                    exit_date=exit_date,
                    roll_business_days=assumptions.roll_business_days,
                )
                symbol = selected[root]["contract"]
                exits[root] = panel[
                    (panel["date"] == exit_date)
                    & (panel["root"] == root)
                    & (panel["contract"] == symbol)
                ].iloc[0]
        except ExecutionError as exc:
            rows.append({
                **base,
                "status": "SKIPPED_NO_CONTRACT",
                "entry_date": entry_date,
                "exit_date": exit_date,
                "skip_reason": str(exc),
            })
            continue

        nearest = {
            root: panel[
                (panel["date"] == entry_date) & (panel["root"] == root)
            ].sort_values(["last_trade_date", "contract"]).iloc[0]["contract"]
            for root in ("ZC", "ZW")
        }

        margin_budget = None
        if account_equity is not None:
            margin_budget = max(account_equity, 0.0) * assumptions.max_margin_fraction
        try:
            size = size_dollar_neutral_spread(
                float(selected["ZC"][assumptions.fill_price_column]),
                float(selected["ZW"][assumptions.fill_price_column]),
                corn_spec=config.contracts["ZC"],
                wheat_spec=config.contracts["ZW"],
                corn_margin_usd=float(selected["ZC"]["maintenance_margin_usd"]),
                wheat_margin_usd=float(selected["ZW"]["maintenance_margin_usd"]),
                max_contracts_per_leg=assumptions.max_contracts_per_leg,
                margin_budget_usd=margin_budget,
            )
        except ExecutionError as exc:
            rows.append({
                **base,
                "status": "SKIPPED_MARGIN",
                "entry_date": entry_date,
                "exit_date": exit_date,
                "skip_reason": str(exc),
            })
            continue

        direction = 1 if decision.action == "BUY" else -1
        quantities = {"ZC": size.corn_contracts, "ZW": size.wheat_contracts}
        sides = {"ZC": direction, "ZW": -direction}
        gross_by_leg = {
            root: _leg_pnl(
                side=sides[root],
                quantity=quantities[root],
                entry_price_cents=float(selected[root][assumptions.fill_price_column]),
                exit_price_cents=float(exits[root][assumptions.fill_price_column]),
                spec=config.contracts[root],
            )
            for root in ("ZC", "ZW")
        }
        total_contracts = size.corn_contracts + size.wheat_contracts
        commission = 2 * total_contracts * assumptions.commission_usd_per_contract_side
        slippage = 2 * sum(
            quantities[root]
            * assumptions.slippage_ticks_per_contract_side
            * config.contracts[root].tick_value_usd
            for root in ("ZC", "ZW")
        )
        gross_pnl = sum(gross_by_leg.values())
        net_pnl = gross_pnl - commission - slippage
        capital_fields: dict[str, float] = {}
        if account_equity is not None:
            capital_after = account_equity + net_pnl
            capital_fields = {
                "margin_budget_usd": float(margin_budget),
                "margin_utilization": size.maintenance_margin_usd / account_equity,
                "return_on_capital": net_pnl / account_equity,
                "capital_after_usd": capital_after,
            }
        rows.append({
            **base,
            **capital_fields,
            "status": "EXECUTED",
            "entry_date": entry_date,
            "exit_date": exit_date,
            "corn_contract": selected["ZC"]["contract"],
            "wheat_contract": selected["ZW"]["contract"],
            "corn_nearest_contract": nearest["ZC"],
            "wheat_nearest_contract": nearest["ZW"],
            "corn_rolled_forward": selected["ZC"]["contract"] != nearest["ZC"],
            "wheat_rolled_forward": selected["ZW"]["contract"] != nearest["ZW"],
            "corn_last_trade_date": selected["ZC"]["last_trade_date"],
            "wheat_last_trade_date": selected["ZW"]["last_trade_date"],
            "corn_side": "LONG" if sides["ZC"] == 1 else "SHORT",
            "wheat_side": "LONG" if sides["ZW"] == 1 else "SHORT",
            "corn_contracts": size.corn_contracts,
            "wheat_contracts": size.wheat_contracts,
            "corn_entry_cents": float(selected["ZC"][assumptions.fill_price_column]),
            "corn_exit_cents": float(exits["ZC"][assumptions.fill_price_column]),
            "wheat_entry_cents": float(selected["ZW"][assumptions.fill_price_column]),
            "wheat_exit_cents": float(exits["ZW"][assumptions.fill_price_column]),
            "corn_gross_pnl_usd": gross_by_leg["ZC"],
            "wheat_gross_pnl_usd": gross_by_leg["ZW"],
            "gross_pnl_usd": gross_pnl,
            "commission_usd": commission,
            "slippage_usd": slippage,
            "net_pnl_usd": net_pnl,
            "maintenance_margin_usd": size.maintenance_margin_usd,
            "return_on_margin": net_pnl / size.maintenance_margin_usd,
            "corn_notional_usd": size.corn_notional_usd,
            "wheat_notional_usd": size.wheat_notional_usd,
            "notional_imbalance_fraction": size.imbalance_fraction,
            "fill_timing": f"next common session {assumptions.fill_price_column}",
            "roll_business_days": assumptions.roll_business_days,
        })
        if account_equity is not None:
            account_equity = capital_after
        open_through = exit_date
    return pd.DataFrame(rows)
