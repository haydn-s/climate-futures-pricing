"""Event study machinery: what did corn do around a drought publication?

An R-squared over every growing-season day asks the wrong question. The signal
this project is looking for lives in a handful of episodes -- 2012, 2002, 2011,
2023 -- and averaging across 3,900 quiet days dilutes it to nothing by
construction. An event study keeps the episodes and throws away the quiet days.

The design, and the one thing it does NOT prove:

Events are large jumps in belt severe-drought coverage. Each is aligned on the
day the Drought Monitor PUBLISHED that jump, not the Tuesday the map describes,
because publication is when the number became public. Cumulative returns are
then measured on both sides of that day. Returns concentrated BEFORE publication
mean the price had already moved by the time the official figure landed.

That is a statement about the DATA being late, not about the market being
clairvoyant. Drought builds over weeks and traders watch the same weather the
Drought Monitor does, so a price that leads the publication may simply be
tracking the underlying weather rather than anticipating the assessment of it.
This module measures the gap; it cannot attribute it. Anything written from it
has to say so.

Two defences against the ways this goes wrong:

* DE-CLUSTERING. Drought builds over several weeks, so consecutive maps all look
  like events and their windows overlap almost completely. Overlapping windows
  are not independent observations, and treating them as such is what turns a
  single 2012 into forty confirmations of a hypothesis. `select_events` keeps
  only the largest jump within `min_spacing_days`.
* A PLACEBO, rather than a t-statistic. Daily commodity returns are fat-tailed
  and seasonal, and event windows overlap even after de-clustering, so a
  parametric standard error here is not credible. `placebo_distribution` instead
  draws the same number of pseudo-events from the same eligible days and reports
  where the real result falls in that distribution.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Drought takes about a month to build, so two events closer than this are
# almost certainly one episode counted twice.
MIN_SPACING_DAYS = 28

# The top 5% of weekly increases in severe-drought coverage.
DEFAULT_QUANTILE = 0.95

# Trading days either side of publication.
PRE, POST = 20, 20


def select_events(timeline: pd.DataFrame, *, column: str = "d2_delta1",
                  quantile: float = DEFAULT_QUANTILE,
                  min_spacing_days: int = MIN_SPACING_DAYS) -> pd.DataFrame:
    """The largest isolated jumps in `column`, one per episode.

    Greedy peak-picking: take the biggest jump, discard everything within
    `min_spacing_days` of it, repeat. The alternative -- every map above the
    threshold -- counts one drought as many events whose windows overlap, and
    overlapping windows are not independent evidence.
    """
    if not 0.0 < quantile < 1.0:
        raise ValueError(f"quantile must be in (0, 1), found {quantile}")
    rows = timeline.loc[timeline[column].notna()
                        & timeline["publication_date"].notna()].copy()
    if rows.empty:
        return rows.assign(magnitude=pd.Series(dtype="float64"))

    positive = rows.loc[rows[column] > 0]
    if positive.empty:
        return positive.assign(magnitude=pd.Series(dtype="float64"))
    # Standard linear interpolation, so `quantile` means the fraction it says.
    # Note the small-sample edge: the cut-off can land BETWEEN two observations,
    # and `>=` then excludes the lower one -- the 1st percentile of [20, 25] is
    # 20.05, which drops the 20. Harmless at 1,395 map dates, surprising at two.
    threshold = positive[column].quantile(quantile)
    candidates = (positive.loc[positive[column] >= threshold]
                  .sort_values(column, ascending=False))

    spacing = pd.Timedelta(days=min_spacing_days)
    kept: list[pd.Timestamp] = []
    chosen: list[int] = []
    for index, row in candidates.iterrows():
        when = row["publication_date"]
        if all(abs(when - already) >= spacing for already in kept):
            kept.append(when)
            chosen.append(index)
    events = (rows.loc[chosen]
              .assign(magnitude=lambda frame: frame[column])
              .sort_values("publication_date", ignore_index=True))
    events.attrs["threshold"] = float(threshold)
    events.attrs["candidates"] = int(len(candidates))
    return events


def trading_positions(publication_dates: pd.Series,
                      trading_dates: pd.Series) -> np.ndarray:
    """Index of the first trading day on or after each publication date.

    On or AFTER, and the publication day itself counts: the Drought Monitor is
    released at 08:30 ET, hours before the grain session closes, so that day's
    close can already reflect it. Shifting to the next day instead would move a
    genuine same-day reaction into the "after" window and overstate the lead.

    Returns -1 where a date falls past the end of the price series.
    """
    left = pd.to_datetime(pd.Series(publication_dates).reset_index(drop=True))
    right = pd.to_datetime(pd.Series(trading_dates).reset_index(drop=True))
    positions = np.searchsorted(right.to_numpy(), left.to_numpy(), side="left")
    return np.where(positions >= len(right), -1, positions)


def event_matrix(values: np.ndarray, positions: np.ndarray, *,
                 pre: int = PRE, post: int = POST) -> np.ndarray:
    """One row per event, columns running from -pre to +post around day 0.

    An event without room for a full window is returned as all-NaN rather than
    dropped silently, so the caller can report how many were lost.
    """
    width = pre + post + 1
    matrix = np.full((len(positions), width), np.nan)
    for row, position in enumerate(positions):
        if position < 0 or position - pre < 0 or position + post >= len(values):
            continue
        matrix[row] = values[position - pre: position + post + 1]
    return matrix


def cumulate(matrix: np.ndarray, *, pre: int = PRE) -> np.ndarray:
    """Cumulative return from the start of the window, zeroed at day -pre.

    Day -pre is the anchor, so every path starts at zero and the vertical
    distance between two offsets is the return earned between them.
    """
    filled = np.nan_to_num(matrix, nan=0.0)
    return np.cumsum(filled, axis=1)


def window_sum(matrix: np.ndarray, start: int, end: int, *, pre: int = PRE) -> np.ndarray:
    """Summed return over offsets [start, end] inclusive, per event.

    Offsets are signed and relative to publication: window_sum(m, -10, -1) is
    the two trading weeks before the figure was public, and (0, 10) is
    publication day onwards.
    """
    lo, hi = start + pre, end + pre + 1
    if lo < 0 or hi > matrix.shape[1]:
        raise ValueError(f"window [{start}, {end}] falls outside the event matrix")
    return np.nansum(matrix[:, lo:hi], axis=1)


def deseasonalise(returns: pd.Series, dates: pd.Series) -> pd.Series:
    """Returns less the average return for that calendar week in OTHER years.

    Corn has a pronounced seasonal pattern and drought events cluster in
    midsummer, so some of any event-window return is just the season. The
    same-week mean is computed leave-one-year-out: including the event's own year
    would subtract part of the very move being measured.
    """
    frame = pd.DataFrame({"r": returns.to_numpy(),
                          "date": pd.to_datetime(dates).to_numpy()})
    frame["week"] = frame["date"].dt.isocalendar().week.astype("int64")
    frame["year"] = frame["date"].dt.year
    totals = frame.groupby("week")["r"].transform("sum")
    counts = frame.groupby("week")["r"].transform("count")
    own_totals = frame.groupby(["week", "year"])["r"].transform("sum")
    own_counts = frame.groupby(["week", "year"])["r"].transform("count")
    others = (totals - own_totals) / (counts - own_counts).replace(0, np.nan)
    return pd.Series(frame["r"] - others.fillna(0.0), index=returns.index)


def placebo_distribution(values: np.ndarray, eligible: np.ndarray, n_events: int,
                         *, start: int, end: int, pre: int = PRE, post: int = POST,
                         draws: int = 2000, seed: int = 0) -> np.ndarray:
    """Mean window return for `draws` sets of pseudo-events drawn from `eligible`.

    The comparison a t-statistic cannot honestly make here: same number of
    events, same pool of candidate days, same window, but no drought jump. Where
    the real figure falls in this distribution is the p-value.
    """
    rng = np.random.default_rng(seed)
    room = eligible[(eligible - pre >= 0) & (eligible + post < len(values))]
    if len(room) < n_events or n_events == 0:
        return np.array([])
    results = np.empty(draws)
    for draw in range(draws):
        picks = rng.choice(room, size=n_events, replace=False)
        matrix = event_matrix(values, picks, pre=pre, post=post)
        results[draw] = np.nanmean(window_sum(matrix, start, end, pre=pre))
    return results


def quantile_of(observed: float, distribution: np.ndarray) -> float:
    """Fraction of the placebo distribution at or below `observed`.

    A two-sided p-value is min(q, 1 - q) * 2; the caller decides, because a
    one-sided reading is appropriate when the hypothesis names a direction.
    """
    if distribution.size == 0:
        return float("nan")
    return float(np.mean(distribution <= observed))
