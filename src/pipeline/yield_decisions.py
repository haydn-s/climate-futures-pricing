"""What a calibrated yield distribution is worth to someone holding the risk.

The project's two findings sit awkwardly together until you name who is using
the estimate. Public weather measures a corn supply shock, and the same
information does not produce a futures-return edge. Those are only in tension
for a speculator. A grain buyer, feed mill, or lender already holds the exposure
whether or not they forecast it, and their payoff from a better forecast is a
smaller chance of being caught short -- not a trading profit. That payoff
survives in a perfectly efficient market, which is why this module exists and
why it is built to be incapable of claiming anything else.

THE DECISION IS DELIBERATELY PRICE-FREE. A hedging demonstration that let its
user size a futures position from the model's view would be re-importing the
directional signal that failed every validation in this repository, and it would
report that failure as a profit. So no price enters anywhere here. The user
provisions a physical buffer against a shortfall in the crop they must handle,
their loss depends on the harvest alone, and the only question asked is whether
a conditional distribution beats the climatological one at setting that buffer.

THE LOSS IS A NEWSVENDOR LOSS, AND ITS ASYMMETRY IS AN ASSUMPTION. Provision
less than the shortfall and the penalty is `penalty_ratio` per unit short --
emergency origination, demurrage, missed contracts. Provision more and the cost
is one unit per unit carried. Under that loss the optimal buffer is the
`penalty_ratio / (penalty_ratio + 1)` quantile of the predictive shortfall
distribution, so a better distribution shows up as a better buffer. The ratio is
not estimated from anything; it is a stated preference, reported across several
values because no single value is defensible for every reader.

Costs are in units of the holding cost per tonne per hectare. Converting to
currency multiplies every strategy by the same constant and changes no ranking,
so no price is introduced to manufacture a dollar headline.

WHY THE STRATEGIES ARE SHAPED THIS WAY. `climatology` is what the user can do
with no weather data at all: last years' deviations from trend, which is a real
and surprisingly strong baseline. `model` adds the conditional conformal
distribution. `model_point_only` keeps the model's centre but borrows
climatology's spread, which separates knowing where the distribution sits from
knowing how wide it is. `no_buffer` is the floor for ignoring the risk. If the
model cannot beat climatology, the honest report is that the estimate is not yet
decision-grade, and the code below is arranged so that is the natural outcome
rather than something that has to be forced.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd

from .yield_intervals import FoldResiduals

# Stated cost asymmetries, not estimates. 4 means being caught one tonne short
# costs four times as much as carrying one tonne nobody needed.
PENALTY_RATIOS = (2.0, 4.0, 8.0)
STRATEGIES = ("no_buffer", "climatology", "climatology_scaled", "model",
              "model_point_only")
# Mean cost of the worst fifth of harvests: three or four seasons out of sixteen.
CVAR_LEVEL = 0.80
BOOTSTRAP_DRAWS = 2000
DECISION_COLUMNS = [
    "feature_set", "year", "penalty_ratio", "strategy", "trend_yield",
    "predicted_yield", "actual_yield", "actual_shortfall", "buffer",
    "shortfall_unprovisioned", "buffer_unused", "cost",
]


def _newsvendor_quantile(values: np.ndarray, level: float) -> float:
    """The ceil(level*n)-th smallest value: the buffer minimising empirical loss.

    DELIBERATELY NOT THE CONFORMAL CONVENTION. The interval code divides by
    n+1, which is the finite-sample correction that makes a coverage statement
    honest. Applied to a decision it is the wrong estimator: it rounds the buffer
    up past the loss-minimising order statistic and charges the user for carrying
    grain they did not need. Minimising the empirical newsvendor loss over this
    sample gives the ceil(level*n)-th order statistic instead, and that is what a
    buyer acting on the distribution should choose.

    Every strategy uses this same rule, so the comparison between them is
    unaffected by the choice; what changes is that none of them is handicapped by
    a convention borrowed from a different question.
    """
    if len(values) == 0:
        raise ValueError("a quantile needs at least one value")
    rank = min(len(values), max(1, int(np.ceil(level * len(values)))))
    return float(np.sort(values)[rank - 1])


def predictive_shortfalls(fold: FoldResiduals) -> dict[str, np.ndarray]:
    """Each strategy's predictive sample of the shortfall below the fold trend.

    The planning baseline is the fold's own trend forecast, shared by every
    strategy, so the comparison is between distributions rather than between
    different definitions of a shortfall. A harvest above trend is not a
    shortfall and contributes zero, which is what makes the loss one-sided.
    """
    deviations = fold.trend * (fold.train_anomalies
                               - float(np.mean(fold.train_anomalies)))
    spread = float(np.std(deviations, ddof=1))
    # An asymmetric loss rewards a wider predictive distribution on its own,
    # because over-provisioning is the cheap error. This control gives the
    # no-weather user the model's uncertainty without the model's information,
    # so a saving that is really just conservatism shows up here too.
    scale = (float(np.std(fold.signed, ddof=1)) / spread if spread > 1e-12
             else 1.0)
    samples = {
        # No weather: the training years' own deviations from trend.
        "climatology": fold.trend * (1.0 + fold.train_anomalies),
        "climatology_scaled": fold.trend + deviations * scale,
        # The conformal predictive distribution: forecast plus its residuals.
        "model": fold.point + fold.signed,
        # The model's centre carrying climatology's spread.
        "model_point_only": fold.point + deviations,
    }
    return {name: np.clip(fold.trend - sample, 0.0, None)
            for name, sample in samples.items()}


def spread_diagnostic(folds: Iterable[FoldResiduals]) -> pd.DataFrame:
    """How wide the model's uncertainty is beside the climatological spread.

    A ratio above one means the model is the more uncertain of the two, which
    under an asymmetric loss would buy it a cheap advantage; below one means any
    saving it records had to come from where it placed the distribution rather
    than from how wide it drew it.
    """
    rows = []
    for fold in folds:
        deviations = fold.trend * (fold.train_anomalies
                                   - float(np.mean(fold.train_anomalies)))
        climatological = float(np.std(deviations, ddof=1))
        residual = float(np.std(fold.signed, ddof=1))
        rows.append({
            "feature_set": fold.feature_set, "year": fold.year,
            "model_residual_sd": residual,
            "climatology_sd": climatological,
            "spread_ratio": (residual / climatological
                             if climatological > 1e-12 else np.nan),
        })
    return pd.DataFrame(rows)


def decide(folds: Iterable[FoldResiduals], *,
           penalty_ratios: Sequence[float] = PENALTY_RATIOS,
           strategies: Sequence[str] = STRATEGIES) -> pd.DataFrame:
    """One row per fold, penalty ratio, and strategy, with the realised cost.

    The buffer is chosen from information available before the harvest; the cost
    is then scored against what arrived. `no_buffer` provisions nothing and is
    included to show what the risk costs when it is ignored.
    """
    if any(ratio <= 0 for ratio in penalty_ratios):
        raise ValueError("penalty ratios must be positive")
    unknown = set(strategies).difference(STRATEGIES)
    if unknown:
        raise ValueError(f"unknown strategies: {sorted(unknown)}")
    rows = []
    for fold in folds:
        realised = max(0.0, fold.trend - fold.actual)
        samples = predictive_shortfalls(fold)
        for ratio in penalty_ratios:
            critical = ratio / (ratio + 1.0)
            for strategy in strategies:
                buffer = (0.0 if strategy == "no_buffer"
                          else _newsvendor_quantile(samples[strategy], critical))
                short = max(0.0, realised - buffer)
                unused = max(0.0, buffer - realised)
                rows.append({
                    "feature_set": fold.feature_set, "year": fold.year,
                    "penalty_ratio": ratio, "strategy": strategy,
                    "trend_yield": fold.trend, "predicted_yield": fold.point,
                    "actual_yield": fold.actual, "actual_shortfall": realised,
                    "buffer": buffer, "shortfall_unprovisioned": short,
                    "buffer_unused": unused,
                    "cost": ratio * short + unused,
                })
    return pd.DataFrame(rows, columns=DECISION_COLUMNS)


def score_decisions(decisions: pd.DataFrame, *,
                    group_columns: Sequence[str] = ("feature_set",),
                    baseline: str = "climatology",
                    cvar_level: float = CVAR_LEVEL) -> pd.DataFrame:
    """Total, mean, and tail cost per strategy, against the no-weather baseline.

    `cvar_cost` is the mean cost of the worst harvests above `cvar_level`, which
    is the number a risk manager cares about more than the average. Reductions
    are signed so that positive always means the strategy cost less than
    climatology.
    """
    if decisions.empty:
        return pd.DataFrame()
    keys = [*group_columns, "penalty_ratio"]
    rows = []
    for values, frame in decisions.groupby(keys, sort=False):
        reference = frame.loc[frame["strategy"] == baseline, "cost"]
        for strategy, costs in frame.groupby("strategy", sort=False):
            cost = costs["cost"].to_numpy(dtype=float)
            tail = int(max(1, round(len(cost) * (1.0 - cvar_level))))
            rows.append({
                **dict(zip(keys, values if isinstance(values, tuple) else (values,))),
                "strategy": strategy, "harvests": len(cost),
                "total_cost": float(cost.sum()),
                "mean_cost": float(cost.mean()),
                "cvar_cost": float(np.sort(cost)[-tail:].mean()),
                "worst_cost": float(cost.max()),
                "mean_buffer": float(costs["buffer"].mean()),
                "years_short": int((costs["shortfall_unprovisioned"] > 1e-12).sum()),
                "total_cost_vs_baseline": (float(reference.sum() - cost.sum())
                                           if len(reference) else np.nan),
                "cost_reduction": (float(1.0 - cost.sum() / reference.sum())
                                   if len(reference) and reference.sum() > 1e-12
                                   else np.nan),
            })
    return pd.DataFrame(rows)


def bootstrap_difference(decisions: pd.DataFrame, *,
                         group_columns: Sequence[str] = ("feature_set",),
                         strategy: str = "model",
                         baseline: str = "climatology",
                         draws: int = BOOTSTRAP_DRAWS,
                         seed: int = 20261020) -> pd.DataFrame:
    """Resample whole harvests to put an interval on the cost difference.

    Harvests are the unit of resampling because a crop year is the independent
    observation here; the sixteen of them are few enough that a point estimate
    of the saving on its own would overstate what is known. A positive value is
    a saving relative to climatology.
    """
    if decisions.empty:
        return pd.DataFrame()
    keys = [*group_columns, "penalty_ratio"]
    rows = []
    for values, frame in decisions.groupby(keys, sort=False):
        pivot = frame.pivot_table(index="year", columns="strategy", values="cost")
        if not {strategy, baseline}.issubset(pivot.columns):
            continue
        difference = (pivot[baseline] - pivot[strategy]).to_numpy(dtype=float)
        generator = np.random.default_rng(seed)
        samples = difference[generator.integers(
            0, len(difference), size=(draws, len(difference)))].mean(axis=1)
        rows.append({
            **dict(zip(keys, values if isinstance(values, tuple) else (values,))),
            "strategy": strategy, "baseline": baseline, "harvests": len(difference),
            "draws": draws,
            "mean_saving_per_harvest": float(difference.mean()),
            "bootstrap_p025": float(np.quantile(samples, 0.025)),
            "bootstrap_median": float(np.quantile(samples, 0.50)),
            "bootstrap_p975": float(np.quantile(samples, 0.975)),
            "probability_positive": float((samples > 0).mean()),
            "harvests_improved": int((difference > 1e-12).sum()),
            "harvests_worsened": int((difference < -1e-12).sum()),
        })
    return pd.DataFrame(rows)


def severity_strata(decisions: pd.DataFrame, *,
                    group_columns: Sequence[str] = ("feature_set",),
                    baseline: str = "climatology") -> pd.DataFrame:
    """Split the saving by whether the harvest actually fell short.

    The strata report found the model's skill concentrated in the seasons
    furthest from trend, so the saving is expected to concentrate there too.
    Seasons at or above trend are the test of whether a conditional forecast
    costs anything when nothing goes wrong.
    """
    if decisions.empty:
        return pd.DataFrame()
    frame = decisions.copy()
    frame["regime"] = np.where(frame["actual_shortfall"] > 1e-12,
                               "shortfall", "at_or_above_trend")
    keys = [*group_columns, "penalty_ratio", "regime"]
    rows = []
    for values, group in frame.groupby(keys, sort=False):
        pivot = group.pivot_table(index="year", columns="strategy", values="cost")
        if baseline not in pivot.columns:
            continue
        for strategy in pivot.columns:
            rows.append({
                **dict(zip(keys, values if isinstance(values, tuple) else (values,))),
                "strategy": strategy, "harvests": len(pivot),
                "years": ";".join(str(year) for year in sorted(pivot.index)),
                "total_cost": float(pivot[strategy].sum()),
                "mean_cost": float(pivot[strategy].mean()),
                "total_cost_vs_baseline": float(
                    pivot[baseline].sum() - pivot[strategy].sum()),
            })
    return pd.DataFrame(rows)
