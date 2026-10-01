"""Three crops, two belts: does a drought move the crop whose supply it threatens?

Run from the repository root, after fetch and clean:

    PYTHONPATH=src python analysis/cross_crop_events.py

corn_event_attribution.py ran out of power: de-clustering is what makes drought
events independent and what leaves only five to twelve a side. Two ways to widen
the base, and they are different kinds of widening:

MORE RESPONSES PER EVENT. Soybeans are grown on the same ground as corn, in
rotation, in the same five states -- so a corn-belt drought is a soybean-belt
drought and adds no new events. What it adds is a second, independently traded
price responding to the event corn already has. Two instruments agreeing on one
shock is worth more than one instrument agreeing with itself.

MORE EVENTS. Soft red winter wheat has its own belt -- the Ohio valley, because
the archived contract is ZW=F and Chicago delivers SRW, not Kansas hard red --
overlapping corn's only in Illinois and Indiana. Its droughts are mostly
different weeks in mostly different places, so these are new events.

AND A FALSIFICATION TEST, which is the reason to run all three together rather
than three separate studies. Winter wheat is harvested in June and July. By the
time a corn-belt drought peaks in late July, the wheat crop is already in the bin
and no weather can touch its supply. So:

    a July corn-belt drought SHOULD move corn and soybeans
    a July corn-belt drought should NOT move wheat

If it moves all three alike, the move is not crop-supply news. It is something
common to agricultural futures -- index flows, the dollar, risk appetite -- and
that is a caveat on every price result in this project, not a finding about
wheat. This is the cheapest available check on whether the event study is
measuring agronomy or sentiment, and it is only possible because the three crops
have different calendars.

Everything here inherits the caveats of corn_event_study.py: placebo inference
rather than t-statistics, seasonally adjusted returns, and small samples that the
de-clustering makes smaller.
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
QUANTILES = (0.90, 0.95)
DRAWS = 2000
GROWING_MONTHS = range(4, 11)
PRE_WINDOW = (-10, -1)
POST_WINDOW = (1, 10)

# Which crop's states define each belt, and which prices are measured against it.
# The first entry of each tuple is the crop that belt belongs to.
BELTS = (
    ("corn/soy belt", "corn", ("corn", "soybeans", "wheat")),
    ("SRW wheat belt", "wheat", ("wheat", "corn", "soybeans")),
)

# Corn-belt events split by season. Wheat is standing in April-June and harvested
# by late July, so the two groups ask different questions of the same crop.
SEASONS = {"Apr-Jun (wheat standing)": (4, 5, 6),
           "Jul-Aug (wheat harvested)": (7, 8)}


def price_table(store: ProcessedStore) -> dict[str, pd.DataFrame]:
    raw = store.read("yahoo_prices_daily")
    tables = {}
    for series in ("corn", "soybeans", "wheat"):
        frame = (raw.loc[raw["series"] == series, ["date", "close"]]
                 .dropna(subset=["close"]).sort_values("date", ignore_index=True))
        frame["ret"] = frame["close"].pct_change().fillna(0.0)
        frame["adjusted"] = events.deseasonalise(frame["ret"], frame["date"])
        tables[series] = frame
    return tables


def respond(selected: pd.DataFrame, series: pd.DataFrame,
            window: tuple[int, int]) -> tuple[float, float, int]:
    """Mean window return, one-sided placebo p, and events contributing."""
    values = series["adjusted"].to_numpy()
    positions = events.trading_positions(selected["publication_date"], series["date"])
    matrix = events.event_matrix(values, positions, pre=PRE, post=POST)
    summed = events.window_sum(matrix, window[0], window[1], pre=PRE)
    summed = summed[~np.isnan(summed)]
    if summed.size == 0:
        return float("nan"), float("nan"), 0
    eligible = np.flatnonzero(series["date"].dt.month.isin(list(GROWING_MONTHS)).to_numpy())
    distribution = events.placebo_distribution(
        values, eligible, summed.size, start=window[0], end=window[1],
        pre=PRE, post=POST, draws=DRAWS, seed=31)
    p_value = 1.0 - events.quantile_of(float(np.mean(summed)), distribution)
    return float(np.mean(summed)), float(p_value), int(summed.size)


def table(label: str, selected: pd.DataFrame, prices: dict[str, pd.DataFrame],
          crops: tuple[str, ...]) -> None:
    print(f"\n  {label}  ({len(selected)} events)")
    print(f"    {'crop':10s} {'before':>8s} {'p':>6s}   {'after':>8s} {'p':>6s}  {'n':>3s}")
    for crop in crops:
        before, p_before, _ = respond(selected, prices[crop], PRE_WINDOW)
        after, p_after, n = respond(selected, prices[crop], POST_WINDOW)
        if n == 0:
            print(f"    {crop:10s}      no event has room for a full window")
            continue
        print(f"    {crop:10s} {before:+7.2%} {p_before:6.3f}   "
              f"{after:+7.2%} {p_after:6.3f}  {n:3d}")


def main() -> None:
    geography = load_geography()
    store = ProcessedStore(default_data_root())
    prices = price_table(store)
    usdm = store.read("usdm_county_drought")
    have = set(usdm["state"].unique())

    print(__doc__.splitlines()[0])
    print("\nreturns seasonally adjusted; p is one-sided against a placebo")
    print(f"drought archive covers {len(have)} states: {', '.join(sorted(have))}")

    belts: dict[str, pd.DataFrame] = {}
    for label, crop, _ in BELTS:
        states = tuple(state.postal for state in geography.crop(crop).states)
        missing = set(states) - have
        if missing:
            print(f"\n{label}: SKIPPED -- no drought data for {', '.join(sorted(missing))}. "
                  f"Fetch with: python -m pipeline fetch --source usdm "
                  f"--states {' '.join(sorted(missing))} --years 2000-2026")
            continue
        belts[label] = panel.belt_drought(usdm, states)
        print(f"{label}: {', '.join(states)} | {len(belts[label])} weekly maps")

    for quantile in QUANTILES:
        print(f"\n{'=' * 78}\nEVENTS AT THE {quantile:.0%} PERCENTILE")
        selections: dict[str, pd.DataFrame] = {}
        for label, crop, crops in BELTS:
            if label not in belts:
                continue
            selected = events.select_events(belts[label], column="d2_delta1",
                                            quantile=quantile)
            selections[label] = selected
            print(f"\n{label}: {len(selected)} episodes, jump >= "
                  f"{selected.attrs.get('threshold', float('nan')):.1f}pp")
            table("all events", selected, prices, crops)

        # The falsification test, on the belt that has the crop-calendar contrast.
        corn_belt = selections.get("corn/soy belt")
        if corn_belt is not None and not corn_belt.empty:
            print(f"\n  {'-' * 70}")
            print("  FALSIFICATION: the same corn-belt events, split by season")
            months = corn_belt["publication_date"].dt.month
            for season, keep in SEASONS.items():
                subset = corn_belt.loc[months.isin(list(keep))]
                if subset.empty:
                    print(f"\n  {season}: no events")
                    continue
                table(season, subset, prices, ("corn", "soybeans", "wheat"))

        # How much the base actually widened.
        if len(selections) > 1:
            dates = pd.concat([s["publication_date"] for s in selections.values()])
            overlap = dates.duplicated().sum()
            print(f"\n  pooled: {len(dates)} belt-events across {len(selections)} belts, "
                  f"{overlap} sharing a publication week, "
                  f"{dates.nunique()} distinct weeks")

    print(f"\n{'-' * 78}")
    print("Reading the falsification row: wheat responding to a July corn-belt drought")
    print("as strongly as corn does cannot be supply news -- that crop was harvested")
    print("weeks earlier. It would mean these events move agricultural futures as a")
    print("group, and every price result in this project inherits that caveat.")


if __name__ == "__main__":
    main()
