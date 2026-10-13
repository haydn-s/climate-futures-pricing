"""Align scheduled USDA Crop Progress releases to dated corn execution labels."""

from __future__ import annotations

from dataclasses import replace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .features.crop_progress import STAGES
from .futures_execution import load_execution_config
from .gefs_model import MARKET_FEATURES, zc_market_features
from .lseg_replay import prepare_contracts, replay_frozen_signals

EASTERN = ZoneInfo("America/New_York")
CONDITION_FEATURES = (
    "condition_good_excellent",
    "condition_good_excellent_change_1w",
    "condition_good_excellent_vs_prior_year",
    "condition_poor_very_poor",
    "condition_poor_very_poor_change_1w",
    "condition_poor_very_poor_vs_prior_year",
    "condition_balance",
    "condition_balance_change_1w",
    "condition_balance_vs_prior_year",
)
PROGRESS_FEATURES = tuple(
    f"progress_{stage}{suffix}"
    for stage in STAGES
    for suffix in ("", "_change_1w", "_vs_prior_year", "_vs_five_year")
)


class CropProgressModelError(ValueError):
    """Release features and dated contracts cannot form an honest model row."""


def build_execution_frame(features: pd.DataFrame, contracts: pd.DataFrame, *,
                          holding_sessions: int = 5,
                          roll_business_days: int = 10) -> pd.DataFrame:
    required = {
        "release_date", "reference_date", "available_at", "source_sha256",
        *CONDITION_FEATURES, *PROGRESS_FEATURES,
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise CropProgressModelError(
            "Crop Progress feature table is missing: " + ", ".join(missing)
        )
    if holding_sessions < 1:
        raise CropProgressModelError("holding_sessions must be positive")
    releases = features.copy()
    releases["release_date"] = pd.to_datetime(
        releases["release_date"], errors="coerce"
    ).dt.normalize()
    releases["reference_date"] = pd.to_datetime(
        releases["reference_date"], errors="coerce"
    ).dt.normalize()
    releases["available_at"] = pd.to_datetime(
        releases["available_at"], errors="coerce", utc=True
    )
    if releases[["release_date", "reference_date", "available_at"]].isna().any().any():
        raise CropProgressModelError("release timestamps must be present")
    local = releases["available_at"].dt.tz_convert(EASTERN)
    if not ((local.dt.hour == 16) & (local.dt.minute == 0)).all():
        raise CropProgressModelError("every report must become available at 4:00 p.m. Eastern")
    if not local.dt.tz_localize(None).dt.normalize().eq(releases["release_date"]).all():
        raise CropProgressModelError("release date and availability timestamp disagree")
    if (releases["reference_date"] >= releases["release_date"]).any():
        raise CropProgressModelError("reference week must precede its release")
    if releases.duplicated("release_date").any():
        raise CropProgressModelError("feature table has duplicate release dates")

    panel = prepare_contracts(contracts)
    roots = {
        root: set(panel.loc[panel["root"] == root, "date"])
        for root in ("ZC", "ZW")
    }
    sessions = sorted(roots["ZC"] & roots["ZW"])
    decisions = []
    for release in releases.itertuples(index=False):
        later = [session for session in sessions if session > release.release_date]
        if len(later) <= holding_sessions:
            continue
        decisions.append({
            "date": release.release_date,
            "target_end_date": later[holding_sessions],
            "year": release.release_date.year,
            "actual": 0.0,
            "signal": 0,
            "fold": 0,
            "sample": "unassigned",
        })
    if not decisions:
        raise CropProgressModelError("dated contracts have no complete post-release window")

    config = load_execution_config()
    config = replace(config, assumptions=replace(
        config.assumptions,
        holding_sessions=holding_sessions,
        roll_business_days=roll_business_days,
    ))
    outcomes = replay_frozen_signals(pd.DataFrame(decisions), panel, config=config)
    outcome_columns = [
        "date", "entry_date", "exit_date", "corn_contract", "corn_rolled_forward",
        "execution_unsigned_corn_return", "potential_corn_cost_return",
    ]
    releases = releases.rename(columns={"release_date": "date"}).merge(
        outcomes[outcome_columns], on="date", how="inner", validate="one_to_one"
    )
    if releases.duplicated("entry_date").any():
        raise CropProgressModelError("more than one USDA release maps to an entry session")

    market = zc_market_features(panel, roll_business_days=roll_business_days)
    releases = pd.merge_asof(
        releases.sort_values("date"),
        market.sort_values("market_data_through"),
        left_on="date", right_on="market_data_through",
        direction="backward", allow_exact_matches=True,
    )
    angle = 2.0 * np.pi * (releases["date"].dt.month - 1) / 12.0
    releases["month_sin"] = np.sin(angle)
    releases["month_cos"] = np.cos(angle)
    releases["target_corn_gross_return"] = releases["execution_unsigned_corn_return"]
    releases["target_corn_cost_return"] = releases["potential_corn_cost_return"]
    releases["target_corn_net_long_return"] = (
        releases["target_corn_gross_return"] - releases["target_corn_cost_return"]
    )
    releases["year"] = releases["date"].dt.year.astype("int64")
    usable = releases.dropna(subset=[
        "entry_date", "exit_date", "target_corn_gross_return",
        "target_corn_cost_return", *MARKET_FEATURES,
    ])
    if usable.empty:
        raise CropProgressModelError(
            "no USDA release has complete controls and execution labels"
        )
    if not (pd.to_datetime(usable["market_data_through"]) <= usable["date"]).all():
        raise CropProgressModelError("market controls occur after release")
    if not (pd.to_datetime(usable["entry_date"]) > usable["date"]).all():
        raise CropProgressModelError("entry must be after the 4 p.m. release date")
    return usable.sort_values("date", ignore_index=True)
