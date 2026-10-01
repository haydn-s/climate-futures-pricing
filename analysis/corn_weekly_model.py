"""Does the daily grid just blur the signal? Score the same features weekly.

Run from the repository root, after fetch and clean:

    PYTHONPATH=src python analysis/corn_weekly_model.py

Three reasons the daily panel may be the wrong frequency, none of them about
model capacity:

1. THE FEATURES ONLY CHANGE WEEKLY. The Drought Monitor publishes one map a
   week, so a daily panel repeats each drought reading about five times. Four of
   every five rows carry no new information about the feature that matters most.
2. THE TARGETS OVERLAP. A five-day forward return on consecutive days shares
   four of its five days with its neighbour, so 3,911 daily rows hold roughly 780
   independent observations. Cross-validation treats them as 3,911, which is why
   the embargo is necessary but not sufficient.
3. THE PRELIMINARY EVIDENCE WAS WEEKLY. The lead-lag correlations in the README
   were computed week by week through the growing season, so a daily test is not
   the same test.

So weekly is not a smaller sample, it is the same information without the
redundancy -- one observation per drought publication, aligned on the first
trading day the figure could be acted on, with forward returns that no longer
overlap. If the daily null was a frequency artefact, this is where it breaks.

A caution about what a better number here would mean. Weekly observations are
roughly a fifth as many, so the folds are small and a single good fold moves the
average much further than it does daily. The comparison below is therefore
reported alongside the daily figure from corn_peek_sweep.py rather than on its
own, and the peek sweep runs at both frequencies so the shape is comparable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline import backtest, events  # noqa: E402
from pipeline.config import load_geography  # noqa: E402
from pipeline.features import panel  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

PEEKS = (0, 7, 14, 21)
HORIZONS = (5, 10)
GROWING_MONTHS = range(4, 11)
TARGET_SERIES = "corn"

# One row per week, so the embargo is counted in weeks. A five-day forward return
# from one publication day ends just as the next week's begins, so a single row
# of gap separates two targets that would otherwise touch.
WEEKLY_EMBARGO = 1
DAILY_EMBARGO = 5

# The parsimonious set the daily sweep settled on: drought changes plus
# standardised heat. Levels are left out -- they cannot explain a weekly change.
FEATURES = ["d2_delta1", "d2_delta2", "d2_delta4",
            "d0_delta1", "d0_delta2", "d0_delta4",
            "heat_dd7_z", "heat_dd14_z"]


def main() -> None:
    geography = load_geography()
    store = ProcessedStore(default_data_root())
    states = tuple(state.postal for state in geography.crop("corn").states)
    drought = panel.belt_drought(store.read("usdm_county_drought"), states)

    print(__doc__.splitlines()[0])
    print(f"\nfeatures ({len(FEATURES)}): {', '.join(FEATURES)}")
    print(f"maps: {len(drought)} weekly publications "
          f"{drought['publication_date'].min().date()}.."
          f"{drought['publication_date'].max().date()}")

    for horizon in HORIZONS:
        target = f"fwd{horizon}_{TARGET_SERIES}"
        print(f"\n{'=' * 78}\ntarget {target}  ({horizon} trading days forward)")

        for peek in PEEKS:
            # Built unfiltered: a publication date outside the growing season
            # must not snap forward onto the first in-season trading day, which
            # would pile phantom observations at the start of every April.
            frame, spec = panel.build(store, geography, crop="corn", peek_days=peek,
                                      horizons=(horizon,), sensitive_months_only=False)
            positions = events.trading_positions(drought["publication_date"], frame["date"])
            positions = np.unique(positions[positions >= 0])
            weekly = frame.iloc[positions]
            weekly = weekly.loc[weekly["month"].isin(list(GROWING_MONTHS))]

            season = frame.loc[frame["month"].isin(list(GROWING_MONTHS))]
            available = [name for name in FEATURES
                         if name in spec.feature_sources and name not in spec.unavailable]

            if peek == PEEKS[0]:
                print(f"  grid: {len(season)} daily rows -> {len(weekly)} weekly rows "
                      f"({len(season) / max(len(weekly), 1):.1f}x fewer, "
                      f"targets no longer overlapping)")
                if len(available) < len(FEATURES):
                    print(f"  UNFILLED, excluded: "
                          f"{sorted(set(FEATURES) - set(available))}")

            for label, rows, embargo in (("weekly", weekly, WEEKLY_EMBARGO),
                                         ("daily", season, DAILY_EMBARGO)):
                scores = backtest.evaluate(
                    rows, available, target, label=f"{label} peek{peek}d",
                    embargo=embargo, tradeable=(peek == 0))
                if not scores:
                    print(f"  {label} peek{peek}d: too few rows to split")
                    continue
                for score in scores:
                    print(score.line())
            print()

    print("-" * 78)
    print("Reading this: if weekly R-squared at peek 0 is still at or below zero, the")
    print("daily null was not a frequency artefact and the published weather carries no")
    print("tradeable signal at either cadence. Weekly folds are about a fifth the size,")
    print("so treat a single positive fold average with more suspicion than a daily one.")


if __name__ == "__main__":
    main()
