"""How early does the corn market know? Train the same model on later and later
information and watch the skill appear.

Run from the repository root, after the pipeline has fetched and cleaned:

    PYTHONPATH=src python analysis/corn_peek_sweep.py

THE QUESTION THIS ANSWERS, and why it is not "can we predict corn". The
preliminary work found corn returns lining up with drought increases published
one to three weeks LATER: the price moves before the data lands. So a model fed
only published weather is predicting something already in the price, and
near-zero out-of-sample skill is the EXPECTED result rather than a failure. The
informative experiment is to vary the information set and measure the skill that
appears:

    peek 0   only what had been published. Tradeable. The honest baseline.
    peek 7   data published up to a week late. NOT tradeable.
    peek 21  three weeks late. NOT tradeable.

Skill at peek 0 would be a trading signal. Skill that appears only at peek 7 or
21 is skill locked inside data that arrives too late to act on -- which is "the
market already knew, and by about this much" stated as a number. A positive peek
is a measurement, never a result; anything reported from one has to say so.

WHAT THIS SCRIPT REFUSES TO DO, because each would manufacture the result:

* No shuffled cross-validation. Splits run forward in time only.
* An EMBARGO of `horizon` days between train and test. A 5-day forward return
  starting inside the training window finishes inside the test window, so an
  ungapped split shares the target across the boundary. This is the single
  easiest way to produce a publishable R-squared from nothing.
* Features are standardised on TRAIN statistics only, refit per fold.
* Out-of-sample R-squared is measured against the TRAINING mean, because that is
  the only baseline forecast available at prediction time -- nobody knows the
  test period's mean return in advance. Note which direction this cuts: a model
  predicting a constant scores exactly ZERO against the train mean and NEGATIVE
  against the test mean, so this is the more lenient of the two choices. A
  negative R-squared below therefore means worse than the naive forecast a
  trader could actually have made, which is a real failure and not an artefact
  of a harsh denominator.
* Every headline is re-run excluding 2012, the year the README already shows
  carrying the annual relationship on its own.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402
from sklearn.linear_model import Ridge  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

from pipeline.config import load_geography  # noqa: E402
from pipeline.features import panel  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

PEEKS = (0, 3, 7, 14, 21)
HORIZON = 5
N_SPLITS = 6
TARGET_SERIES = "corn"

# WHICH FEATURES, and why this is not a detail. Throwing all 19 tradeable columns
# at a 5-day return scores R2 -0.08: thirteen of them are slow-moving LEVELS
# (drought coverage, accumulated heat, trailing rainfall) and a level cannot
# explain a five-day change, so they contribute variance and nothing else. The
# preliminary evidence this project is built on was about drought CHANGES, so
# that is what "deltas" tests, and it scores about zero -- the honest answer
# rather than an overfit one. Run both; report the parsimonious one as the
# headline and the full set as evidence that more features made it worse.
FEATURE_SETS = {
    "deltas": ["d2_delta1", "d2_delta2", "d2_delta4",
               "d0_delta1", "d0_delta2", "d0_delta4"],
    "deltas+heat": ["d2_delta1", "d2_delta2", "d2_delta4",
                    "d0_delta1", "d0_delta2", "d0_delta4",
                    "heat_dd7_z", "heat_dd14_z"],
    "all": None,   # every tradeable feature the panel could fill
}
DEFAULT_SET = "deltas+heat"


@dataclass(frozen=True)
class Score:
    """One model, one information set, out of sample."""

    peek: int
    model: str
    r2: float
    hit_rate: float
    n_train: int
    n_test: int

    def line(self) -> str:
        flag = "" if self.peek == 0 else "  NOT TRADEABLE"
        return (f"  peek {self.peek:2d}d  {self.model:8s}  R2 {self.r2:+.4f}   "
                f"hit {self.hit_rate:.1%}   train {self.n_train:4d} test {self.n_test:4d}{flag}")


def forward_folds(n: int, n_splits: int, embargo: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window splits that run forward in time, with a gap.

    `embargo` rows are dropped from the END of each training block: the last
    training observation's forward return extends `horizon` days into what
    becomes the test block, so without the gap the two share an outcome.
    """
    folds = []
    fold_size = n // (n_splits + 1)
    for split in range(1, n_splits + 1):
        train_end = fold_size * split
        test_end = min(fold_size * (split + 1), n)
        train = np.arange(0, max(train_end - embargo, 0))
        test = np.arange(train_end, test_end)
        if len(train) > 50 and len(test) > 10:
            folds.append((train, test))
    return folds


def evaluate(frame: pd.DataFrame, features: list[str], target: str, *,
             peek: int, embargo: int) -> list[Score]:
    """Out-of-sample R-squared and directional hit rate, averaged over folds."""
    usable = frame.dropna(subset=[*features, target]).reset_index(drop=True)
    X, y = usable[features].to_numpy(), usable[target].to_numpy()
    folds = forward_folds(len(usable), N_SPLITS, embargo)
    if not folds:
        return []

    candidates = {
        "ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "gbm": lambda: HistGradientBoostingRegressor(
            max_depth=3, max_iter=200, learning_rate=0.05, random_state=0),
    }
    scores = []
    for name, make in candidates.items():
        r2s, hits, n_train, n_test = [], [], [], []
        for train, test in folds:
            model = make().fit(X[train], y[train])
            predicted = model.predict(X[test])
            # The train mean is the only baseline knowable at prediction time. A
            # constant prediction scores exactly 0.0 against it, so anything
            # negative here is worse than that naive forecast.
            baseline = y[train].mean()
            residual = np.sum((y[test] - predicted) ** 2)
            total = np.sum((y[test] - baseline) ** 2)
            r2s.append(1.0 - residual / total if total > 0 else np.nan)
            moved = predicted != 0
            hits.append(np.mean(np.sign(predicted[moved]) == np.sign(y[test][moved]))
                        if moved.any() else np.nan)
            n_train.append(len(train))
            n_test.append(len(test))
        scores.append(Score(peek=peek, model=name, r2=float(np.nanmean(r2s)),
                            hit_rate=float(np.nanmean(hits)),
                            n_train=int(np.mean(n_train)), n_test=int(np.mean(n_test))))
    return scores


def main(feature_set: str = DEFAULT_SET) -> None:
    if feature_set not in FEATURE_SETS:
        raise SystemExit(f"unknown feature set {feature_set!r} "
                         f"(choose from {', '.join(FEATURE_SETS)})")
    geography = load_geography()
    store = ProcessedStore(default_data_root())
    target = f"fwd{HORIZON}_{TARGET_SERIES}"

    print(__doc__.split("\n\n")[0])
    print(f"\ntarget: {target} ({HORIZON} trading days forward, {TARGET_SERIES})")
    print(f"folds:  {N_SPLITS} expanding, forward only, {HORIZON}-day embargo")

    results: list[Score] = []
    excluding_2012: list[Score] = []
    for peek in PEEKS:
        frame, spec = panel.build(store, geography, crop="corn", peek_days=peek,
                                  horizons=(HORIZON,), sensitive_months_only=True)
        # Tradeable features only, and never one the archive could not fill.
        chosen = FEATURE_SETS[feature_set]
        features = [name for name in (chosen or spec.tradeable_features)
                    if name in spec.feature_sources and name not in spec.unavailable]
        untradeable = set(features) & set(spec.untradeable_features)
        if untradeable:
            raise SystemExit(f"feature set {feature_set!r} names untradeable "
                             f"columns {sorted(untradeable)}")
        if peek == PEEKS[0]:
            print(f"rows:   {len(frame)} growing-season trading days, "
                  f"{frame['date'].min().date()}..{frame['date'].max().date()}")
            print(f"feature set '{feature_set}' ({len(features)}): {', '.join(features)}")
            if spec.unavailable:
                print(f"UNFILLED, excluded: {', '.join(spec.unavailable)}")
            print()
        results.extend(evaluate(frame, features, target, peek=peek, embargo=HORIZON))
        without = frame.loc[frame["year"] != 2012]
        excluding_2012.extend(evaluate(without, features, target, peek=peek, embargo=HORIZON))

    print("ALL YEARS")
    for score in results:
        print(score.line())
    print("\nEXCLUDING 2012")
    for score in excluding_2012:
        print(score.line())

    tradeable = [s for s in results if s.peek == 0]
    best_peek = max((s for s in results if s.peek > 0), key=lambda s: s.r2, default=None)
    print("\n" + "-" * 72)
    print("READING THIS: out-of-sample R-squared near or below zero at peek 0 means the")
    print("published weather adds nothing to a 5-day forward return -- the market has")
    print("already priced it. Skill rising with peek measures how much of the signal is")
    print("locked in data that arrives too late to trade.")
    if tradeable:
        print(f"\npeek 0 (tradeable):  best R2 {max(s.r2 for s in tradeable):+.4f}")
    if best_peek is not None:
        print(f"best peeking model:  R2 {best_peek.r2:+.4f} at peek {best_peek.peek}d "
              f"({best_peek.model}) -- NOT a trading result")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SET)
