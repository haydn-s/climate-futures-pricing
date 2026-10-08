"""Does the market price a drought early because the crop is vulnerable NOW?

Run from the repository root, after fetch and clean:

    PYTHONPATH=src python analysis/seasonal_timing.py

cross_crop_events.py found corn's average return timing flipping with the season:
a midsummer corn-belt drought has more return BEFORE the Drought Monitor publishes
it, while a spring event has more after. One candidate mechanism is attention --
during the weeks that decide a crop, traders may already be watching the weather,
so the official figure arrives after part of the move. Outside those weeks the
publication, continuing weather, or another common driver may dominate.

That story makes a prediction sharp enough to be wrong. If this is attention to a
VULNERABLE CROP, each crop should be priced early during ITS OWN critical weeks,
and those weeks differ by crop. The rival explanation is that late summer is
simply when agricultural futures are jumpy, in which case every crop is early in
the same months whatever its own calendar.

Each crop is therefore split into three stages of its own season rather than
into "critical" and "everything else" -- the binary version hides exactly the
comparison that separates the two stories, because it pools the weeks before a
crop is vulnerable with the weeks after.

TWO DESIGN CORRECTIONS a first version of this script needed, both of which
changed the answer:

* GROWING SEASON ONLY. Drought jumps happen year round -- three of 43 pooled
  events land in January -- so an unrestricted "outside the critical window"
  bucket mixes winter dormancy with post-harvest summer and cannot be read as
  "this crop does not care".
* EACH CROP ON ITS OWN BELT. Soybeans share corn's five states exactly, so they
  share its events. Wheat does not: the archived contract is ZW=F, which Chicago
  settles in soft red winter wheat from the Ohio valley, and only that belt's
  droughts can touch it.

The stage calendars below are AGRONOMIC ASSUMPTIONS, not probe findings, and they
are the hinge of the whole test -- get wheat's wrong and the result inverts. They
live here rather than in config/geography.yaml because sensitive_months there is
deliberately the whole season (corn is 4-10), which is the right scope for a
pipeline and far too wide to locate a two-month effect. If this survives, they
belong in config.

Cells run to three to seven events. The pattern is a hypothesis-generating
seasonal diagnostic, not a precise effect estimate or a causal test.
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

# Which belt's droughts each crop is tested against.
CROP_BELT = {"corn": "corn", "soybeans": "corn", "wheat": "wheat"}

# Each crop's season in three stages, with the critical one flagged. Winter wheat
# is the awkward one and the reason stages beat a single window: it fills grain in
# May, is harvested by July, and sows the NEXT crop in September, so it has two
# periods that matter and a dead one in between.
STAGES = {
    "corn": (("Apr-Jun  pre-pollination", (4, 5, 6), False),
             ("Jul-Aug  CRITICAL pollination", (7, 8), True),
             ("Sep-Oct  maturity, harvest", (9, 10), False)),
    "soybeans": (("Apr-Jul  vegetative", (4, 5, 6, 7), False),
                 ("Aug-Sep  CRITICAL pod fill", (8, 9), True),
                 ("Oct      maturity", (10,), False)),
    "wheat": (("Apr-Jun  CRITICAL grain fill", (4, 5, 6), True),
              ("Jul-Aug  harvested, nothing at stake", (7, 8), False),
              ("Sep-Oct  sowing the next crop", (9, 10), False)),
}


def price_table(store: ProcessedStore) -> dict[str, pd.DataFrame]:
    raw = store.read("yahoo_prices_daily")
    tables = {}
    for series in STAGES:
        frame = (raw.loc[raw["series"] == series, ["date", "close"]]
                 .dropna(subset=["close"]).sort_values("date", ignore_index=True))
        frame["ret"] = frame["close"].pct_change().fillna(0.0)
        frame["adjusted"] = events.deseasonalise(frame["ret"], frame["date"])
        tables[series] = frame
    return tables


def belt_events(store: ProcessedStore, geography, belt_crop: str,
                quantile: float) -> pd.DataFrame:
    """Growing-season drought events for one belt. See the docstring on why both
    the season restriction and the per-crop belt matter."""
    usdm = store.read("usdm_county_drought")
    states = tuple(s.postal for s in geography.crop(belt_crop).states)
    if set(states) - set(usdm["state"].unique()):
        return pd.DataFrame()
    belt = panel.belt_drought(usdm, states)
    selected = events.select_events(belt, column="d2_delta1", quantile=quantile)
    in_season = selected["publication_date"].dt.month.isin(list(GROWING_MONTHS))
    return selected.loc[in_season].reset_index(drop=True)


def respond(selected: pd.DataFrame, series: pd.DataFrame,
            window: tuple[int, int]) -> tuple[float, float, int]:
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
        pre=PRE, post=POST, draws=DRAWS, seed=37)
    return (float(np.mean(summed)),
            float(1.0 - events.quantile_of(float(np.mean(summed)), distribution)),
            int(summed.size))


def stars(p: float) -> str:
    if np.isnan(p):
        return ""
    return "***" if p < 0.01 else "**" if p < 0.05 else "*" if p < 0.10 else ""


def main() -> None:
    geography = load_geography()
    store = ProcessedStore(default_data_root())
    prices = price_table(store)

    print(__doc__.splitlines()[0])
    print("\nreturns seasonally adjusted; p one-sided against a placebo; "
          "*** <0.01 ** <0.05 * <0.10")

    for quantile in QUANTILES:
        print(f"\n{'=' * 78}")
        print(f"EVENTS AT THE {quantile:.0%} PERCENTILE, growing season only")

        for crop, stages in STAGES.items():
            selected = belt_events(store, geography, CROP_BELT[crop], quantile)
            if selected.empty:
                print(f"\n{crop}: no drought data for the {CROP_BELT[crop]} belt")
                continue
            months = selected["publication_date"].dt.month
            print(f"\n  {crop} on the {CROP_BELT[crop]} belt "
                  f"({len(selected)} in-season events)")
            print(f"    {'stage':38s} {'n':>3s}  {'before':>10s}  {'after':>10s}")
            for label, window, critical in stages:
                subset = selected.loc[months.isin(list(window))]
                mark = " <--" if critical else ""
                if subset.empty:
                    print(f"    {label:38s}   no events{mark}")
                    continue
                before, p_before, n = respond(subset, prices[crop], PRE_WINDOW)
                after, p_after, _ = respond(subset, prices[crop], POST_WINDOW)
                if n == 0:
                    print(f"    {label:38s}   no full window{mark}")
                    continue
                print(f"    {label:38s} {n:3d}  "
                      f"{before:+7.2%}{stars(p_before):3s}  "
                      f"{after:+7.2%}{stars(p_after):3s}{mark}")

    print(f"\n{'-' * 78}")
    print("WHAT TO LOOK FOR. Corn early (before dominating) in Jul-Aug and nowhere")
    print("else is attention to a crop that is vulnerable right now -- if it were")
    print("merely late-summer jumpiness, Sep-Oct would look the same and it does not.")
    print("Wheat early in Jul-Oct, when its crop is harvested, and NOT in Apr-Jun when")
    print("it is filling grain, is the opposite: that is corn's window, not wheat's,")
    print("so wheat is most likely moving sympathetically with the corn complex -- the")
    print("two belts share Illinois and Indiana -- rather than pricing its own supply.")


if __name__ == "__main__":
    main()
