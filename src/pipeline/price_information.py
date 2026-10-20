"""Why the weather signal does not trade: is it already priced, and could we tell?

Two questions that the return backtests in this repository cannot answer on their
own, because a null return R-squared is consistent with several different worlds.

ONE: IS THE INFORMATION ALREADY IN THE PRICE? If the futures market has already
impounded the summer's weather, then a weather-based forecast of the harvest adds
nothing to what the price was already saying, and no amount of modelling will
recover an edge from it. That is a testable claim, and it is tested here on the
PHYSICAL target rather than on returns. At each fixed issue date, the realised
yield deviation is regressed on the published nowcast, on the contemporaneous
new-crop futures price change, and on both together. If the price subsumes the
nowcast, the weather information was public knowledge the market had used. If the
nowcast survives alongside the price, the information was not fully impounded and
the trading failure has to be explained by cost, horizon, or noise instead.

Running the race on the yield rather than on returns is deliberate. Regressing
returns on a signal asks whether the signal predicts the price, which is the
question every backtest here already answered no to. Regressing the harvest on
both asks who knew what, which is the question that distinguishes an efficient
market from a useless model.

TWO: COULD THIS SAMPLE HAVE SEEN A PROFITABLE SIGNAL? A strategy needs only a
small edge to beat costs, and a small edge needs a large sample to distinguish
from zero. Those two thresholds are computed separately here and compared. Where
the break-even edge is smaller than the smallest edge the sample can detect,
there is a band of signal strengths that would be profitable and invisible, and
a null result cannot rule them out. That is a statement about the experiment's
resolution, not an excuse for its result: the backtests in this repository did
not merely fail to find skill, several of them lost money decisively, and a tiny
positive edge does not produce a 52% loss. Both facts belong in the conclusion.

THE BREAK-EVEN ARITHMETIC ASSUMES A SIGN RULE ON JOINTLY NORMAL DRAWS. For a
position that takes the sign of the forecast, expected gross profit per trade is
sd * sqrt(2/pi) * correlation, so break-even against a round-trip cost c is a
correlation of c / (sd * sqrt(2/pi)). Real strategies hold sometimes, which
trades breadth for fewer costs and moves both sides of the comparison; the
reported numbers are the every-period case and should be read as an order of
magnitude rather than a threshold to defend.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
from statistics import NormalDist

# The new-crop contract a northern-hemisphere corn harvest is priced in.
DECEMBER_CODE = "Z"
# Pre-season baseline: planting is under way and the crop is not yet made.
BASELINE_MONTH = 4
BASELINE_DAY = 1
# Round-trip costs to evaluate, in basis points of notional. 10 is the bridge
# the signal benchmark used; 24 is roughly one tick plus commission on each of
# four contract sides, which is what the execution attribution actually charged.
COST_BPS = (10.0, 24.0, 50.0)
SEASON_MONTHS = (4, 5, 6, 7, 8, 9, 10)
HORIZONS = (1, 5, 10)
BOOTSTRAP_DRAWS = 2000
# Conventional two-sided significance and power for the detection threshold.
ALPHA = 0.05
POWER = 0.80


def december_contract_signal(contracts: pd.DataFrame, *, root: str = "ZC",
                             issue_months: Sequence[int] = (8, 9, 10),
                             ) -> pd.DataFrame:
    """Log change in the new-crop December contract from April 1 to each issue.

    The December contract of harvest year t is the cleanest public statement of
    what the market expects that harvest to be worth, and using the dated
    contract avoids the roll artefacts a continuous series carries. A positive
    change means the market marked new-crop corn up over the growing season.
    """
    frame = contracts.loc[contracts["root"] == root].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    pattern = rf"^{root}{DECEMBER_CODE}\d{{2}}$"
    frame = frame.loc[frame["contract"].str.match(pattern)]
    if frame.empty:
        raise ValueError(f"no {root} December contracts in the panel")
    frame["year"] = 2000 + frame["contract"].str[-2:].astype(int)
    rows = []
    for year, group in frame.groupby("year"):
        group = group.sort_values("date")
        baseline_date = pd.Timestamp(year, BASELINE_MONTH, BASELINE_DAY)
        before = group.loc[group["date"] <= baseline_date]
        if before.empty:
            continue
        baseline = float(before.iloc[-1]["settlement_cents"])
        for month in issue_months:
            issue = pd.Timestamp(year, month, 1)
            known = group.loc[group["date"] < issue]
            if known.empty or baseline <= 0:
                continue
            latest = float(known.iloc[-1]["settlement_cents"])
            if latest <= 0:
                continue
            rows.append({
                "year": int(year), "issue_month": int(month),
                "price_source": "december_contract",
                "baseline_date": before.iloc[-1]["date"],
                "signal_date": known.iloc[-1]["date"],
                "price_change": float(np.log(latest / baseline)),
            })
    return pd.DataFrame(rows)


def continuous_signal(prices: pd.DataFrame, *, symbol: str = "ZC=F",
                      issue_months: Sequence[int] = (8, 9, 10),
                      ) -> pd.DataFrame:
    """The same signal from the continuous series, to extend the sample.

    This reaches back further than the dated panel and so covers more harvests,
    at the cost of contract-roll contamination inside the window. Roll effects
    add noise to a four-month price change rather than pushing it in a known
    direction, but the two sources are reported separately rather than pooled.
    """
    frame = prices.loc[prices["symbol"] == symbol].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date")
    if frame.empty:
        raise ValueError(f"no {symbol} prices")
    rows = []
    for year in sorted(frame["date"].dt.year.unique()):
        baseline_date = pd.Timestamp(year, BASELINE_MONTH, BASELINE_DAY)
        before = frame.loc[frame["date"] <= baseline_date]
        if before.empty:
            continue
        baseline = float(before.iloc[-1]["close"])
        for month in issue_months:
            issue = pd.Timestamp(year, month, 1)
            known = frame.loc[(frame["date"] < issue)
                              & (frame["date"] >= baseline_date)]
            if known.empty or baseline <= 0:
                continue
            latest = float(known.iloc[-1]["close"])
            if latest <= 0:
                continue
            rows.append({
                "year": int(year), "issue_month": int(month),
                "price_source": "continuous_front",
                "baseline_date": before.iloc[-1]["date"],
                "signal_date": known.iloc[-1]["date"],
                "price_change": float(np.log(latest / baseline)),
            })
    return pd.DataFrame(rows)


def _regress(target: np.ndarray, *columns: np.ndarray) -> dict:
    """Ordinary least squares with an intercept, reporting adjusted R-squared."""
    design = np.column_stack([np.ones(len(target)), *columns])
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    fitted = design @ coefficients
    residual = float(np.sum((target - fitted) ** 2))
    total = float(np.sum((target - target.mean()) ** 2))
    r2 = 1.0 - residual / total if total > 1e-20 else float("nan")
    predictors = design.shape[1] - 1
    degrees = len(target) - predictors - 1
    adjusted = (1.0 - (1.0 - r2) * (len(target) - 1) / degrees
                if degrees > 0 else float("nan"))
    return {"coefficients": coefficients, "r2": r2, "adjusted_r2": adjusted,
            "observations": len(target), "predictors": predictors}


def horse_race(predictions: pd.DataFrame, signals: pd.DataFrame, *,
               draws: int = BOOTSTRAP_DRAWS, seed: int = 20261020,
               ) -> pd.DataFrame:
    """Does the nowcast still explain the harvest once the price is in the model?

    `predictions` is the committed nowcast prediction file, so the forecasts
    raced here are the published ones rather than a refit. For each sample,
    issue date, feature set, and price source, three regressions are run on the
    realised yield deviation and the whole-harvest bootstrap is applied to the
    joint coefficient on the nowcast. `incremental_r2` is what the nowcast adds
    on top of the price, which is the quantity the efficiency question turns on.
    """
    merged = predictions.merge(signals, on=["year", "issue_month"], how="inner")
    keys = ["sample", "issue_month", "feature_set", "price_source"]
    rows = []
    for values, frame in merged.groupby(keys, sort=False):
        frame = frame.dropna(subset=["predicted_anomaly", "actual_anomaly",
                                     "price_change"])
        if len(frame) < 8:
            continue
        target = frame["actual_anomaly"].to_numpy(dtype=float)
        nowcast = frame["predicted_anomaly"].to_numpy(dtype=float)
        price = frame["price_change"].to_numpy(dtype=float)
        alone = _regress(target, nowcast)
        priced = _regress(target, price)
        joint = _regress(target, nowcast, price)
        generator = np.random.default_rng(seed)
        indices = generator.integers(0, len(frame), size=(draws, len(frame)))
        samples = []
        for draw in indices:
            if len(np.unique(draw)) < 4:
                continue
            try:
                samples.append(_regress(target[draw], nowcast[draw],
                                        price[draw])["coefficients"][1])
            except np.linalg.LinAlgError:  # pragma: no cover - degenerate draw
                continue
        samples = np.asarray(samples)
        # Leave one harvest out of the score, as the decomposition report does:
        # an incremental R-squared carried by a single season is a description
        # of that season. This never refits a model, only rescores.
        incremental = []
        for position in range(len(frame)):
            keep = np.arange(len(frame)) != position
            incremental.append(
                _regress(target[keep], nowcast[keep], price[keep])["r2"]
                - _regress(target[keep], price[keep])["r2"])
        rows.append({
            **dict(zip(keys, values if isinstance(values, tuple) else (values,))),
            "harvests": len(frame),
            "first_year": int(frame["year"].min()),
            "last_year": int(frame["year"].max()),
            "nowcast_r2": alone["r2"],
            "price_r2": priced["r2"],
            "joint_r2": joint["r2"],
            "joint_adjusted_r2": joint["adjusted_r2"],
            "incremental_r2": joint["r2"] - priced["r2"],
            "incremental_r2_min_drop_one": float(np.min(incremental)),
            "incremental_r2_max_drop_one": float(np.max(incremental)),
            "most_load_bearing_year": int(
                frame["year"].to_numpy()[int(np.argmin(incremental))]),
            "nowcast_beta_alone": float(alone["coefficients"][1]),
            "nowcast_beta_joint": float(joint["coefficients"][1]),
            "price_beta_alone": float(priced["coefficients"][1]),
            "price_beta_joint": float(joint["coefficients"][2]),
            "nowcast_beta_retained": (float(joint["coefficients"][1]
                                            / alone["coefficients"][1])
                                      if abs(alone["coefficients"][1]) > 1e-12
                                      else np.nan),
            "bootstrap_draws": len(samples),
            "nowcast_beta_p025": (float(np.quantile(samples, 0.025))
                                  if len(samples) else np.nan),
            "nowcast_beta_p975": (float(np.quantile(samples, 0.975))
                                  if len(samples) else np.nan),
            "nowcast_beta_excludes_zero": (
                bool(np.quantile(samples, 0.025) > 0
                     or np.quantile(samples, 0.975) < 0)
                if len(samples) else False),
        })
    return pd.DataFrame(rows)


def return_moments(prices: pd.DataFrame, *, symbol: str = "ZC=F",
                   horizons: Sequence[int] = HORIZONS,
                   season_months: Sequence[int] = SEASON_MONTHS,
                   ) -> pd.DataFrame:
    """Growing-season return dispersion and how many independent draws exist.

    Overlapping horizon returns are not independent observations, so the
    non-overlapping count is what any power statement has to use. Reporting both
    makes the gap between them visible.
    """
    frame = prices.loc[prices["symbol"] == symbol].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    series = frame.sort_values("date").set_index("date")["close"]
    if series.empty:
        raise ValueError(f"no {symbol} prices")
    logged = np.log(series.where(series > 0))
    rows = []
    for horizon in horizons:
        returns = logged.diff(horizon).dropna()
        in_season = returns[returns.index.month.isin(list(season_months))]
        if len(in_season) < horizon * 2:
            continue
        rows.append({
            "symbol": symbol, "horizon_days": horizon,
            "return_sd": float(in_season.std(ddof=1)),
            "overlapping_observations": len(in_season),
            "independent_observations": len(in_season) // horizon,
            "first_date": in_season.index.min().date().isoformat(),
            "last_date": in_season.index.max().date().isoformat(),
        })
    return pd.DataFrame(rows)


def break_even_correlation(return_sd: float, cost_bps: float) -> float:
    """Signal/return correlation at which a sign rule exactly pays its costs."""
    if return_sd <= 0:
        raise ValueError("return dispersion must be positive")
    if cost_bps < 0:
        raise ValueError("cost cannot be negative")
    return (cost_bps / 10_000.0) / (return_sd * np.sqrt(2.0 / np.pi))


def detectable_correlation(observations: int, *, alpha: float = ALPHA,
                           power: float = POWER) -> float:
    """Smallest correlation a sample of this size can distinguish from zero."""
    if observations < 4:
        raise ValueError("a power statement needs at least four observations")
    z = (NormalDist().inv_cdf(1.0 - alpha / 2.0)
         + NormalDist().inv_cdf(power))
    return z / np.sqrt(observations)


def required_observations(correlation: float, *, alpha: float = ALPHA,
                          power: float = POWER) -> float:
    """Observations needed to detect this correlation at the stated power."""
    if not 0 < abs(correlation) < 1:
        raise ValueError("correlation must be non-zero and below one")
    z = (NormalDist().inv_cdf(1.0 - alpha / 2.0)
         + NormalDist().inv_cdf(power))
    return float(np.ceil((z / correlation) ** 2))


def power_budget(moments: pd.DataFrame, *,
                 cost_bps: Sequence[float] = COST_BPS,
                 alpha: float = ALPHA, power: float = POWER) -> pd.DataFrame:
    """Compare what pays for itself with what this sample could have detected.

    `detectable_r2` is the floor on measurable skill; `break_even_r2` is the
    floor on profitable skill. Where the break-even floor sits below the
    detectable floor, a null result cannot distinguish "no edge" from "an edge
    too small for this sample", and `blind_to_profitable_signal` says so.
    """
    rows = []
    for moment in moments.itertuples(index=False):
        detectable = detectable_correlation(moment.independent_observations,
                                            alpha=alpha, power=power)
        for cost in cost_bps:
            break_even = break_even_correlation(moment.return_sd, cost)
            rows.append({
                "symbol": moment.symbol, "horizon_days": moment.horizon_days,
                "return_sd": moment.return_sd,
                "independent_observations": moment.independent_observations,
                "cost_bps": cost,
                "break_even_correlation": break_even,
                "break_even_r2": break_even ** 2,
                "detectable_correlation": detectable,
                "detectable_r2": detectable ** 2,
                "observations_needed": required_observations(
                    break_even, alpha=alpha, power=power),
                "observation_shortfall": (
                    required_observations(break_even, alpha=alpha, power=power)
                    / moment.independent_observations),
                "blind_to_profitable_signal": bool(break_even < detectable),
            })
    return pd.DataFrame(rows)
