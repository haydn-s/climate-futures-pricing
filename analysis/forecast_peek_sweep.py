"""The tradeable test: does a public weather forecast predict corn returns?

Run from the repository root, after fetch and clean:

    PYTHONPATH=src python analysis/forecast_peek_sweep.py

THIS IS THE ONE PEEK 0 THAT MEANS SOMETHING. corn_peek_sweep.py scored published
OBSERVATIONS and found nothing at any information set: -0.016 daily, -0.013
weekly, and no skill even when the model was allowed to see data published three
weeks late. That closed off "the data arrives too late" as the explanation, but
it could not test the obvious remaining channel, because an observation is late
by construction. A forecast is not. CPC issues its 6-10 and 8-14 day outlooks
before the weather happens, so peek 0 here is a complete, actionable information
set rather than a hobbled one.

WHAT IS BEING ASKED, precisely: holding only what a trader could have read that
morning, does the published forecast predict the next few days of corn? If the
answer is no, the market's efficiency with respect to public weather information
is established on the channel that actually matters, and the project's headline
null is as strong as it can be made. If the answer is yes, everything earlier
changes.

FOUR THINGS THIS DOES DIFFERENTLY from the observational sweep.

LEVELS AND REVISIONS ARE SEPARATED. A forecast reading "60% above normal" that
has said so for a week is public and stale; the revision that moved it there was
the news. They are scored apart because they are different hypotheses, and
because the raw level carries a trend -- the CPC temperature anomaly averages
about -1pp over 2012-2014 and +8pp over 2022-2026, since the probability is
scored against a climatological baseline that lags a warming record. A model fed
levels is partly learning what year it is. A first difference cannot be.

SHORT HORIZONS COME FIRST. If a forecast is news, the price moves when it is
published, not when the weather arrives. One and two trading days are therefore
the sharp tests and five and ten are context -- the reverse of the observational
sweep, where the question was whether a slow-moving drought state predicted a
week ahead.

PEEK IS NOW A CONTROL, NOT THE EXPERIMENT. There is no late data to buy back, so
a positive peek means letting the model read forecasts issued AFTER the day it is
trading on, which is untradeable by construction. It is run only to show that the
machinery can find something when something is there: if skill appears at peek 7
and not at peek 0, the features work and the market was simply ahead of them.

THE SAMPLE IS SHORTER. The outlook archive starts in April 2012, so this is about
2,200 growing-season days against the observational sweep's 3,911, and 2012 is
the first year rather than one year among twenty-six. Fewer folds, wider error.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pipeline import backtest  # noqa: E402
from pipeline.config import load_geography  # noqa: E402
from pipeline.features import panel  # noqa: E402
from pipeline.storage import ProcessedStore, default_data_root  # noqa: E402

PEEKS = (0, 7, 14)
HORIZONS = (1, 2, 5, 10)
TARGET_SERIES = "corn"
N_SPLITS = 5

# Separated on purpose; see the module docstring.
FEATURE_SETS = {
    "revisions (news)": [f"fc_{p}_rev" for p in panel.FORECAST_PRODUCTS],
    "levels (stale)": [f"fc_{p}" for p in panel.FORECAST_PRODUCTS],
    "levels + revisions": [f"fc_{p}{s}" for p in panel.FORECAST_PRODUCTS
                           for s in ("", "_rev")],
    "temperature only": ["fc_610temp", "fc_610temp_rev",
                         "fc_814temp", "fc_814temp_rev"],
    # The comparison that matters for the project's story: does adding a forecast
    # beat the published drought state the observational sweep already scored?
    "drought only (observational)": ["d2_delta1", "d2_delta2", "d2_delta4"],
    "drought + forecast": ["d2_delta1", "d2_delta2", "d2_delta4"]
                          + [f"fc_{p}_rev" for p in panel.FORECAST_PRODUCTS],
}


def main() -> None:
    geography = load_geography()
    store = ProcessedStore(default_data_root())

    print(__doc__.splitlines()[0])
    print(f"\ntarget: corn forward returns at {HORIZONS} trading days")
    print(f"folds:  {N_SPLITS} expanding, forward only, embargo = the horizon")

    panels = {}
    for peek in PEEKS:
        frame, spec = panel.build(store, geography, crop=TARGET_SERIES,
                                  peek_days=peek, horizons=HORIZONS,
                                  sensitive_months_only=True)
        # The forecast record is shorter than the price record, and rows before it
        # starts carry no forecast at all. Scoring them would dilute the test with
        # years the features could not have existed in.
        covered = frame["fc_610temp"].notna()
        panels[peek] = frame.loc[covered].reset_index(drop=True)
        if peek == PEEKS[0]:
            print(f"rows:   {len(frame)} growing-season days, "
                  f"{int(covered.sum())} with a forecast "
                  f"({frame.loc[covered, 'date'].min().date()}.."
                  f"{frame.loc[covered, 'date'].max().date()})")
            if spec.unavailable:
                print(f"UNFILLED: {', '.join(spec.unavailable)}")
            print()

    for horizon in HORIZONS:
        target = f"fwd{horizon}_{TARGET_SERIES}"
        print(f"{'=' * 78}\nTARGET {target}   ({horizon} trading day"
              f"{'s' if horizon > 1 else ''} forward)")
        for label, features in FEATURE_SETS.items():
            print(f"\n  {label}")
            for peek in PEEKS:
                frame = panels[peek]
                available = [name for name in features if name in frame.columns]
                if len(available) < len(features):
                    print(f"    peek {peek:2d}d: missing "
                          f"{sorted(set(features) - set(available))}")
                    continue
                scores = backtest.evaluate(
                    frame, available, target, label=f"peek {peek}d",
                    embargo=horizon, n_splits=N_SPLITS,
                    tradeable=(peek == 0))
                if not scores:
                    print(f"    peek {peek:2d}d: too few rows to split")
                    continue
                for score in scores:
                    print("  " + score.line())

    # The headline, pulled out of the table above so it cannot be missed.
    print(f"\n{'=' * 78}")
    best_tradeable = -np.inf
    best_label = ""
    for label, features in FEATURE_SETS.items():
        frame = panels[0]
        available = [name for name in features if name in frame.columns]
        if len(available) < len(features):
            continue
        for horizon in HORIZONS:
            for score in backtest.evaluate(
                    frame, available, f"fwd{horizon}_{TARGET_SERIES}",
                    label="x", embargo=horizon, n_splits=N_SPLITS):
                if score.r2 > best_tradeable:
                    best_tradeable = score.r2
                    best_label = f"{label}, {score.model}, fwd{horizon}"
    print(f"BEST TRADEABLE RESULT ANYWHERE AT PEEK 0: R2 {best_tradeable:+.4f}")
    print(f"  ({best_label})")
    print("\nA negative figure means the published forecast does not predict corn at")
    print("any horizon or feature set tested -- measured against the training mean,")
    print("which is the only baseline a forecaster actually has. Combined with the")
    print("observational sweep, that closes both public channels: neither the weather")
    print("that has happened nor the forecast of the weather to come carries an")
    print("exploitable signal, and the latency explanation is gone because a forecast")
    print("has no latency. What remains unexplained is the +7.76% corn earns in the")
    print("fortnight before a drought publication during pollination -- which is not")
    print("a public-information story, since neither public source predicts it.")


if __name__ == "__main__":
    main()
