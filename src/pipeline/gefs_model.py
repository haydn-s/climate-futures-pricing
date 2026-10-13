"""Point-in-time GEFS revisions aligned to executable dated corn returns."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import numpy as np
import pandas as pd

from .lseg_replay import prepare_contracts, replay_frozen_signals
from .futures_execution import load_execution_config

MARKET_FEATURES = (
    "zc_mom5", "zc_mom20", "zc_vol20", "zc_curve_slope", "zc_curve_change5",
    "month_sin", "month_cos",
)
ABSOLUTE_FEATURES = tuple(
    f"d{window}_{measure}"
    for window in ("1_3", "4_7", "8_10")
    for measure in ("tmax_c_mean", "tmin_c_mean", "precip_mm_sum", "heat_dd29_sum")
)
REVISION_FEATURES = tuple(f"rev_{name}" for name in ABSOLUTE_FEATURES)


class GefsModelError(ValueError):
    """GEFS features and dated contracts cannot form an honest model row."""


def zc_market_features(contracts: pd.DataFrame, *, roll_business_days: int = 10
                       ) -> pd.DataFrame:
    """Build dated ZC controls from settlements, one row per completed session."""
    panel = prepare_contracts(contracts)
    corn = panel.loc[panel["root"] == "ZC"].copy()
    corn["days_to_last_trade"] = [
        int(np.busday_count(date.date(), expiry.date()))
        for date, expiry in zip(corn["date"], corn["last_trade_date"])
    ]
    corn = corn[corn["days_to_last_trade"] > roll_business_days]
    rows: list[dict[str, object]] = []
    for session, group in corn.groupby("date", sort=True):
        ordered = group.sort_values(["last_trade_date", "contract"])
        if len(ordered) < 2:
            continue
        front, deferred = ordered.iloc[0], ordered.iloc[1]
        rows.append({
            "market_data_through": pd.Timestamp(session),
            "zc_front_contract": str(front["contract"]),
            "zc_deferred_contract": str(deferred["contract"]),
            "zc_front_settlement_cents": float(front["settlement_cents"]),
            "zc_curve_slope": (
                float(front["settlement_cents"])
                / float(deferred["settlement_cents"]) - 1.0
            ),
        })
    result = pd.DataFrame(rows)
    if result.empty:
        raise GefsModelError("dated contracts have no sessions with two eligible ZC contracts")
    # Measure momentum and volatility within the selected contract. A naïve
    # front-price pct_change mistakes the price gap at a roll for a market move.
    histories = {
        contract: group.sort_values("date").set_index("date")["settlement_cents"]
        for contract, group in corn.groupby("contract")
    }
    momenta5: list[float] = []
    momenta20: list[float] = []
    volatilities: list[float] = []
    for row in result.itertuples(index=False):
        history = histories[row.zc_front_contract]
        history = history.loc[history.index <= row.market_data_through]
        momenta5.append(
            float(history.iloc[-1] / history.iloc[-6] - 1.0)
            if len(history) >= 6 else np.nan
        )
        momenta20.append(
            float(history.iloc[-1] / history.iloc[-21] - 1.0)
            if len(history) >= 21 else np.nan
        )
        returns = history.pct_change(fill_method=None).dropna().iloc[-20:]
        volatilities.append(
            float(returns.std(ddof=1) * np.sqrt(252))
            if len(returns) >= 15 else np.nan
        )
    result["zc_mom5"] = momenta5
    result["zc_mom20"] = momenta20
    result["zc_vol20"] = volatilities
    result["zc_curve_change5"] = result["zc_curve_slope"].diff(5)
    return result


def build_execution_frame(revisions: pd.DataFrame, contracts: pd.DataFrame, *,
                          holding_sessions: int = 5,
                          roll_business_days: int = 10) -> pd.DataFrame:
    """Attach prior-session controls and next-open ZC outcomes to GEFS revisions.

    LSEG rows provide dates but not an intraday timestamp for the reported daily
    open. Because the 00 UTC model is available at 07:00 UTC, same-date opens are
    never used: entry is the first common ZC/ZW session strictly after the model
    date. Market controls use settlements strictly before the model date.
    """
    required = {
        "initialized_at", "model_available_at", "statistic",
        "has_prior_day_revision", *ABSOLUTE_FEATURES, *REVISION_FEATURES,
    }
    missing = sorted(required - set(revisions.columns))
    if missing:
        raise GefsModelError(f"GEFS revision table is missing: {', '.join(missing)}")
    if holding_sessions < 1:
        raise GefsModelError("holding_sessions must be positive")

    features = revisions.copy()
    features["initialized_at"] = pd.to_datetime(features["initialized_at"], utc=True)
    features["model_available_at"] = pd.to_datetime(
        features["model_available_at"], utc=True
    )
    if features[["initialized_at", "model_available_at"]].isna().any().any():
        raise GefsModelError("GEFS run and availability timestamps must be present")
    expected = features["initialized_at"] + timedelta(hours=7)
    if not features["model_available_at"].eq(expected).all():
        raise GefsModelError("GEFS model availability must equal initialization + 7 hours")
    features = features[
        (features["statistic"] == "geavg")
        & features["has_prior_day_revision"].astype(bool)
    ].copy()
    features["date"] = (features["initialized_at"].dt.tz_localize(None).dt.normalize()
                        .astype("datetime64[ns]"))
    if features.duplicated("date").any():
        raise GefsModelError("GEFS mean revision table has duplicate decision dates")
    if features.empty:
        raise GefsModelError("no GEFS mean run has an exact prior-day revision")

    panel = prepare_contracts(contracts)
    roots = {
        root: set(panel.loc[panel["root"] == root, "date"])
        for root in ("ZC", "ZW")
    }
    sessions = sorted(roots["ZC"] & roots["ZW"])
    decision_rows = []
    for run in features.itertuples(index=False):
        later = [session for session in sessions if session > run.date]
        if len(later) <= holding_sessions:
            continue
        decision_rows.append({
            "date": run.date,
            "target_end_date": later[holding_sessions],
            "year": run.date.year,
            "actual": 0.0,
            "signal": 0,
            "fold": 0,
            "sample": "unassigned",
        })
    if not decision_rows:
        raise GefsModelError("dated contracts have no complete next-open outcome window")

    config = load_execution_config()
    config = replace(config, assumptions=replace(
        config.assumptions,
        holding_sessions=holding_sessions,
        roll_business_days=roll_business_days,
    ))
    outcomes = replay_frozen_signals(pd.DataFrame(decision_rows), panel, config=config)
    outcome_columns = [
        "date", "entry_date", "exit_date", "corn_contract", "corn_rolled_forward",
        "execution_unsigned_corn_return", "potential_corn_cost_return",
    ]
    features = features.merge(
        outcomes[outcome_columns], on="date", how="inner", validate="one_to_one"
    )
    # Weekend and holiday runs can point to the same next session. Keeping all
    # of them would duplicate one realized target in training. The last model
    # available before that open is the actual decision information set.
    features = (features.sort_values(["entry_date", "model_available_at"])
                .drop_duplicates("entry_date", keep="last"))
    market = zc_market_features(panel, roll_business_days=roll_business_days)
    features = pd.merge_asof(
        features.sort_values("date"),
        market.sort_values("market_data_through"),
        left_on="date", right_on="market_data_through",
        direction="backward", allow_exact_matches=False,
    )
    angle = 2.0 * np.pi * (features["date"].dt.month - 1) / 12.0
    features["month_sin"] = np.sin(angle)
    features["month_cos"] = np.cos(angle)
    features["target_corn_gross_return"] = features["execution_unsigned_corn_return"]
    features["target_corn_cost_return"] = features["potential_corn_cost_return"]
    features["target_corn_net_long_return"] = (
        features["target_corn_gross_return"] - features["target_corn_cost_return"]
    )
    features["year"] = features["date"].dt.year
    usable = features.dropna(subset=[
        "entry_date", "exit_date", "target_corn_gross_return",
        "target_corn_cost_return", *MARKET_FEATURES,
    ])
    if usable.empty:
        raise GefsModelError("no GEFS decisions have complete controls and execution labels")
    if not (pd.to_datetime(usable["market_data_through"]) < usable["date"]).all():
        raise GefsModelError("market controls are not strictly prior to the model date")
    if not (pd.to_datetime(usable["entry_date"]) > usable["date"]).all():
        raise GefsModelError("entry must be strictly after the model date")
    return usable.sort_values("date", ignore_index=True)
