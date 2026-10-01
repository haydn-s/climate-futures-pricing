"""Did corn move before the Drought Monitor said so?

Run from the repository root, after fetch and clean:

    PYTHONPATH=src python analysis/corn_event_study.py

Takes the largest isolated jumps in belt severe-drought coverage, aligns each on
the day that jump was PUBLISHED, and measures cumulative corn returns on both
sides of it. Returns earned before publication are returns the official figure
could not have caused.

Why this and not another regression: the peek sweep in corn_peek_sweep.py found
no skill at any information set, because an R-squared across 3,900
growing-season days averages a handful of real episodes into noise. This keeps
the episodes.

WHAT A PRE-PUBLICATION RETURN DOES AND DOES NOT SHOW. It shows the data is late.
It does NOT show the market anticipated the Drought Monitor's assessment:
drought builds over weeks, traders watch the same rain gauges, and a price
tracking the weather directly will lead any weekly summary of it mechanically.
Both readings predict the same picture here, and this study cannot separate
them. What it can do is put a number on the gap.

Inference is a placebo rather than a t-statistic -- same number of pseudo-events,
same eligible days, no drought jump. Daily commodity returns are fat-tailed and
seasonal and the windows overlap even after de-clustering, so a parametric
standard error would be the most confident wrong number in the output.
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
QUANTILE = 0.95
DRAWS = 2000
GROWING_MONTHS = range(4, 11)
WINDOWS = ((-20, -11), (-10, -1), (0, 0), (1, 5), (1, 10), (11, 20))
SERIES = ("corn", "corn_fund")


def load_prices(store: ProcessedStore, series: str) -> pd.DataFrame:
    prices = store.read("yahoo_prices_daily")
    frame = (prices.loc[prices["series"] == series, ["date", "close"]]
             .dropna(subset=["close"]).sort_values("date", ignore_index=True))
    frame["ret"] = frame["close"].pct_change().fillna(0.0)
    frame["adjusted"] = events.deseasonalise(frame["ret"], frame["date"])
    return frame


def report(label: str, matrix: np.ndarray, values: np.ndarray,
           eligible: np.ndarray, n_events: int) -> None:
    kept = int(np.sum(~np.isnan(matrix).all(axis=1)))
    print(f"\n  {label}  ({kept} events with a full window)")
    print(f"    {'window':>12s}  {'mean':>8s}  {'median':>8s}  {'pos':>5s}  {'placebo p':>9s}")
    for start, end in WINDOWS:
        summed = events.window_sum(matrix, start, end, pre=PRE)
        summed = summed[~np.isnan(summed)]
        if summed.size == 0:
            continue
        distribution = events.placebo_distribution(
            values, eligible, n_events, start=start, end=end,
            pre=PRE, post=POST, draws=DRAWS, seed=11)
        quantile = events.quantile_of(float(np.mean(summed)), distribution)
        # One-sided against the hypothesis that the window is unusually positive.
        p_value = 1.0 - quantile
        span = f"[{start:+d},{end:+d}]"
        print(f"    {span:>12s}  {np.mean(summed):+7.2%}  {np.median(summed):+7.2%}  "
              f"{np.mean(summed > 0):4.0%}  {p_value:9.3f}")


def main() -> None:
    geography = load_geography()
    store = ProcessedStore(default_data_root())
    states = tuple(state.postal for state in geography.crop("corn").states)

    drought = panel.belt_drought(store.read("usdm_county_drought"), states)
    selected = events.select_events(drought, column="d2_delta1", quantile=QUANTILE)

    print(__doc__.splitlines()[0])
    print(f"\nbelt: {', '.join(states)} | {len(drought)} weekly maps "
          f"{drought['map_date'].min().date()}..{drought['map_date'].max().date()}")
    print(f"events: top {1 - QUANTILE:.0%} of weekly severe-drought increases, "
          f"de-clustered at {events.MIN_SPACING_DAYS}d")
    print(f"        {selected.attrs.get('candidates', 0)} candidates above "
          f"{selected.attrs.get('threshold', float('nan')):.1f}pp "
          f"-> {len(selected)} independent episodes")
    print(f"        magnitudes {selected['magnitude'].min():.1f}"
          f"..{selected['magnitude'].max():.1f}pp of belt area")
    by_year = selected.groupby(selected["publication_date"].dt.year).size()
    print(f"        by year: {', '.join(f'{y}x{n}' for y, n in by_year.items())}")

    for series in SERIES:
        prices = load_prices(store, series)
        positions = events.trading_positions(selected["publication_date"], prices["date"])
        unmapped = int(np.sum(positions < 0))
        eligible = np.flatnonzero(prices["date"].dt.month.isin(GROWING_MONTHS).to_numpy())

        print(f"\n{'=' * 70}\n{series}: {len(prices)} trading days "
              f"{prices['date'].min().date()}..{prices['date'].max().date()}")
        if unmapped:
            print(f"  {unmapped} event(s) fall outside this series and are dropped")

        for label, column in (("raw returns", "ret"), ("seasonally adjusted", "adjusted")):
            values = prices[column].to_numpy()
            matrix = events.event_matrix(values, positions, pre=PRE, post=POST)
            report(label, matrix, values, eligible, len(selected))

        # The headline comparison, on the adjusted series.
        values = prices["adjusted"].to_numpy()
        matrix = events.event_matrix(values, positions, pre=PRE, post=POST)
        before = float(np.nanmean(events.window_sum(matrix, -10, -1, pre=PRE)))
        after = float(np.nanmean(events.window_sum(matrix, 1, 10, pre=PRE)))
        print(f"\n  two weeks BEFORE publication {before:+.2%}"
              f"   |   two weeks AFTER {after:+.2%}")
        if abs(before) + abs(after) > 0:
            share = abs(before) / (abs(before) + abs(after))
            print(f"  {share:.0%} of the two-sided move sits before the figure was public")

    print(f"\n{'-' * 70}")
    print("Excluding 2012, the year the README shows carrying the annual relationship:")
    without = selected.loc[selected["publication_date"].dt.year != 2012]
    prices = load_prices(store, "corn")
    positions = events.trading_positions(without["publication_date"], prices["date"])
    values = prices["adjusted"].to_numpy()
    matrix = events.event_matrix(values, positions, pre=PRE, post=POST)
    eligible = np.flatnonzero(prices["date"].dt.month.isin(GROWING_MONTHS).to_numpy())
    report(f"corn, seasonally adjusted, {len(without)} events", matrix, values,
           eligible, len(without))
    print("\nA pre-publication return measures how late the DATA is, not what the")
    print("market knew: the weather itself was observable all along.")


if __name__ == "__main__":
    main()
