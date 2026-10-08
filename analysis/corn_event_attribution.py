"""Is the post-publication move a reaction to the figure, or just more drought?

Run from the repository root, after fetch and clean:

    PYTHONPATH=src python analysis/corn_event_attribution.py

corn_event_study.py found corn up about 5% in the fortnight after a large
drought jump was published, against about 2% in the fortnight before, with the
post window the only strongly significant one. That number has a confound it
cannot answer on its own: events are SELECTED on a large jump, drought is
persistent, so the following fortnight usually contains more drought as well as
whatever the market did about the figure already published. Attribution needs the
two pulled apart.

Three cuts, which fail in different ways, so agreement between them means more
than any one of them:

1. PEAKED VS CONTINUED. Split events on whether belt severe-drought coverage was
   higher or lower two maps after the event. Where the drought stopped, a
   post-publication return cannot be the next week's deterioration.

   The selection runs AGAINST the hypothesis, which is what makes this cut
   worth something: picking events where the drought stopped also picks
   weather that improved, and improving weather should push corn DOWN. A
   positive post-window return among peaked events is therefore a conservative
   reading, not a flattering one.

2. BEFORE THE NEXT MAP. The next Drought Monitor lands about five trading days
   after the last, so [+1,+4] is the window in which the published figure is the
   most recent official word. A reaction to the publication should be visible
   there; deterioration priced from the NEXT map cannot be.

   What this does not remove: traders watch the weather directly and do not wait
   for the Drought Monitor, so continuing drought can still move the price
   inside this window. It isolates the next PUBLICATION, not the next weather.

3. HOLDING SUBSEQUENT DROUGHT FIXED. Regress the post-window return on the
   published jump and on the subsequent drought change together. The coefficient
   on the published jump is the part not attributable to what came next.

POWER. The de-clustering that makes events independent is what makes them few:
15 episodes at the 95th percentile, 12 with a full window, and splitting those
leaves about six a side. So this runs at the 90th percentile as well, and a
disagreement between thresholds should be read as the sample talking rather than
the market.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline import events  # noqa: E402
from pipeline.config import load_geography  # noqa: E402
from pipeline.features import panel  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

PRE, POST = 20, 20
QUANTILES = (0.95, 0.90)
MAPS_AHEAD = 2
DRAWS = 2000
GROWING_MONTHS = range(4, 11)

# [+1,+4] sits before the next weekly map; [+5,+9] sits after it.
BEFORE_NEXT_MAP = (1, 4)
AFTER_NEXT_MAP = (5, 9)
PRE_WINDOW = (-10, -1)
POST_WINDOW = (1, 10)


def prices(store: ProcessedStore, series: str = "corn") -> pd.DataFrame:
    frame = store.read("yahoo_prices_daily")
    frame = (frame.loc[frame["series"] == series, ["date", "close"]]
             .dropna(subset=["close"]).sort_values("date", ignore_index=True))
    frame["ret"] = frame["close"].pct_change().fillna(0.0)
    frame["adjusted"] = events.deseasonalise(frame["ret"], frame["date"])
    return frame


def window_stats(matrix: np.ndarray, window: tuple[int, int], values: np.ndarray,
                 eligible: np.ndarray, n_events: int) -> tuple[float, float, int]:
    """Mean window return, one-sided placebo p, and events contributing."""
    summed = events.window_sum(matrix, window[0], window[1], pre=PRE)
    summed = summed[~np.isnan(summed)]
    if summed.size == 0:
        return float("nan"), float("nan"), 0
    distribution = events.placebo_distribution(
        values, eligible, max(n_events, 1), start=window[0], end=window[1],
        pre=PRE, post=POST, draws=DRAWS, seed=23)
    p_value = 1.0 - events.quantile_of(float(np.mean(summed)), distribution)
    return float(np.mean(summed)), float(p_value), int(summed.size)


def describe(label: str, selected: pd.DataFrame, series: pd.DataFrame,
             eligible: np.ndarray) -> dict[str, float]:
    """Run the four windows for one group of events."""
    values = series["adjusted"].to_numpy()
    positions = events.trading_positions(selected["publication_date"], series["date"])
    matrix = events.event_matrix(values, positions, pre=PRE, post=POST)
    full = int(np.sum(~np.isnan(matrix).all(axis=1)))

    print(f"\n  {label}  ({len(selected)} events, {full} with a full window)")
    if full == 0:
        print("    no event has room for a full window")
        return {}
    print(f"    {'window':>12s}  {'mean':>8s}  {'pos':>5s}  {'p':>6s}  {'n':>3s}")
    results: dict[str, float] = {}
    for name, window in (("before", PRE_WINDOW), ("after", POST_WINDOW),
                         ("pre next map", BEFORE_NEXT_MAP),
                         ("post next map", AFTER_NEXT_MAP)):
        mean, p_value, n = window_stats(matrix, window, values, eligible, full)
        summed = events.window_sum(matrix, window[0], window[1], pre=PRE)
        summed = summed[~np.isnan(summed)]
        positive = float(np.mean(summed > 0)) if summed.size else float("nan")
        span = f"[{window[0]:+d},{window[1]:+d}]"
        print(f"    {span:>12s}  {mean:+7.2%}  {positive:4.0%}  {p_value:6.3f}  {n:3d}")
        results[name] = mean
    return results


def regression(selected: pd.DataFrame, series: pd.DataFrame) -> None:
    """Post-window return on the published jump and the subsequent change.

    Deliberately tiny: two regressors on a dozen events. Reported for the sign
    and relative size of the coefficients, not for inference -- a t-statistic on
    this sample is decoration.
    """
    values = series["adjusted"].to_numpy()
    positions = events.trading_positions(selected["publication_date"], series["date"])
    matrix = events.event_matrix(values, positions, pre=PRE, post=POST)
    post = events.window_sum(matrix, POST_WINDOW[0], POST_WINDOW[1], pre=PRE)

    frame = pd.DataFrame({
        "post": post,
        "published_jump": selected["magnitude"].to_numpy(),
        "subsequent": selected["forward_change"].to_numpy(),
    # dropna is sufficient now that window_sum reports an absent window as NaN:
    # events without a full window carry a NaN post-return and fall out here.
    }).replace([np.inf, -np.inf], np.nan).dropna()
    if len(frame) < 8:
        print(f"\n  regression skipped: {len(frame)} usable events")
        return

    X = np.column_stack([np.ones(len(frame)),
                         frame["published_jump"].to_numpy(),
                         frame["subsequent"].to_numpy()])
    y = frame["post"].to_numpy()
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    residual = y - X @ beta
    dof = max(len(y) - X.shape[1], 1)
    sigma2 = residual @ residual / dof
    standard_errors = np.sqrt(np.diag(sigma2 * np.linalg.pinv(X.T @ X)))
    total = np.sum((y - y.mean()) ** 2)
    r2 = 1.0 - residual @ residual / total if total > 0 else float("nan")

    print(f"\n  post-window return on both drought terms  (n={len(frame)}, R2={r2:+.2f})")
    for name, coefficient, error in zip(("intercept", "published jump", "subsequent change"),
                                        beta, standard_errors):
        t_stat = coefficient / error if error > 0 else float("nan")
        print(f"    {name:20s} {coefficient:+9.5f}  (se {error:.5f}, t {t_stat:+.2f})")
    print("    a published-jump coefficient surviving the subsequent-change control is")
    print("    the part of the move not attributable to the drought carrying on")


def main() -> None:
    geography = load_geography()
    store = ProcessedStore(default_data_root())
    states = tuple(state.postal for state in geography.crop("corn").states)
    drought = panel.belt_drought(store.read("usdm_county_drought"), states)
    series = prices(store)
    eligible = np.flatnonzero(series["date"].dt.month.isin(list(GROWING_MONTHS)).to_numpy())

    print(__doc__.splitlines()[0])
    print(f"\nbelt {', '.join(states)} | {len(drought)} maps | "
          f"corn {series['date'].min().date()}..{series['date'].max().date()}")
    print("returns are seasonally adjusted; p is one-sided against a placebo")

    for quantile in QUANTILES:
        selected = events.select_events(drought, column="d2_delta1", quantile=quantile)
        selected["forward_change"] = events.forward_change(
            selected, drought, column="d2", maps_ahead=MAPS_AHEAD)

        print(f"\n{'=' * 78}")
        print(f"EVENTS AT THE {quantile:.0%} PERCENTILE  "
              f"(jump >= {selected.attrs.get('threshold', float('nan')):.1f}pp, "
              f"{len(selected)} episodes)")

        known = selected.loc[selected["forward_change"].notna()]
        peaked = known.loc[known["forward_change"] <= 0]
        continued = known.loc[known["forward_change"] > 0]
        print(f"  drought over the {MAPS_AHEAD} maps after publication: "
              f"{len(continued)} kept worsening "
              f"(median {continued['forward_change'].median():+.1f}pp), "
              f"{len(peaked)} peaked "
              f"(median {peaked['forward_change'].median():+.1f}pp)")
        if len(selected) != len(known):
            print(f"  {len(selected) - len(known)} event(s) run off the end of the "
                  f"drought record and are excluded from the split")

        describe("ALL events", selected, series, eligible)
        continued_stats = describe("CONTINUED -- drought kept worsening",
                                   continued, series, eligible)
        peaked_stats = describe("PEAKED -- drought stopped (selection runs against us)",
                                peaked, series, eligible)

        if continued_stats and peaked_stats:
            print(f"\n  after-publication, continued {continued_stats['after']:+.2%}"
                  f"   vs peaked {peaked_stats['after']:+.2%}")
            if peaked_stats["after"] > 0:
                share = peaked_stats["after"] / max(continued_stats["after"], 1e-9)
                print(f"  the peaked group retains {share:.0%} of the continued group's move,")
                print("  which is the part not explained by the drought carrying on")
            else:
                print("  the peaked group shows NO post-publication move: on this cut the")
                print("  effect is the drought carrying on, not a reaction to the figure")

        regression(selected, series)

    print(f"\n{'-' * 78}")
    print("Both thresholds and all three cuts have to agree before the 5% in")
    print("corn_event_study.py can be called a reaction to the publication. Six events")
    print("a side is not enough to settle it either way; this narrows what the number")
    print("can mean rather than confirming it.")


if __name__ == "__main__":
    main()
