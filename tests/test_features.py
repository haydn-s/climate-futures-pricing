"""The feature panel: does it only ever see what had been published?

These tests are about leakage, not about arithmetic. The panel's whole claim is
that a feature on day t used no information published after day t, and that
`peek_days` relaxes that by exactly the amount asked for and not a day more. A
bug here does not raise -- it produces a better model, which is why the leakage
tests outnumber the value tests.

Frames are built by hand rather than loaded from fixtures: these functions
consume CLEANED tables, so the captured-response fixtures belong to the clean
tests, and a synthetic frame can hold the one awkward shape a real month does
not (a state missing from a map date, a tmax exactly on the threshold).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from pipeline.calendar import as_of
from pipeline.features import panel
from pipeline.storage import ProcessedStore


# ------------------------------------------------------------------- scaffolding


def drought_frame(map_dates: list[str], states: tuple[str, ...] = ("IA", "IL"),
                  d2: dict[str, list[float]] | None = None) -> pd.DataFrame:
    """A cleaned usdm table: two counties per state so the nesting is visible."""
    rows = []
    for index, map_date in enumerate(map_dates):
        stamp = pd.Timestamp(map_date)
        for state in states:
            values = (d2 or {}).get(state)
            severe = values[index] if values else float(index)
            for county in ("001", "003"):
                rows.append({
                    "fips": f"{state}{county}", "county": f"{county} County", "state": state,
                    "map_date": stamp,
                    # The real lag: a Tuesday map is released that Thursday.
                    "publication_date": stamp + pd.Timedelta(days=2),
                    "none": 100.0 - severe, "d0": severe, "d1": severe, "d2": severe,
                    "d3": 0.0, "d4": 0.0, "statistic_format_id": 1,
                })
    return pd.DataFrame(rows)


def power_frame(start: str, days: int, tmax: list[float] | float = 30.0,
                precip: float = 1.0, crop: str = "corn",
                points: tuple[str, ...] = ("Iowa", "Illinois")) -> pd.DataFrame:
    dates = pd.date_range(start, periods=days, freq="D")
    rows = []
    for offset, day in enumerate(dates):
        for point in points:
            rows.append({
                "crop": crop, "point": point, "country": "United States",
                "lat": 42.0, "lon": -93.6, "date": day,
                "tmax_c": tmax[offset] if isinstance(tmax, list) else tmax,
                "precip_mm": precip,
                "publication_date": day + pd.Timedelta(days=5),
                "api_version": "v2.10.0", "source_model": "MERRA2+POWER",
            })
    return pd.DataFrame(rows)


def price_frame(start: str, days: int, series: tuple[str, ...] = ("corn", "corn_fund"),
                closes: list[float] | None = None) -> pd.DataFrame:
    dates = pd.date_range(start, periods=days, freq="B")
    rows = []
    for name in series:
        for offset, day in enumerate(dates):
            rows.append({
                "series": name, "symbol": "ZC=F", "currency": "USD", "exchange": "CBOT",
                "date": day, "open": 100.0, "high": 101.0, "low": 99.0,
                "close": (closes[offset] if closes else 100.0 + offset),
                "volume": 1000.0, "publication_date": day,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def store(tmp_path, geography) -> ProcessedStore:
    """A processed store holding the three tables the corn panel reads."""
    processed = ProcessedStore(tmp_path)
    processed.write("yahoo_prices_daily", price_frame("2012-01-02", 260))
    processed.write("usdm_county_drought",
                    drought_frame([f"2012-{m:02d}-03" for m in range(1, 13)],
                                  states=("IA", "IL", "IN", "MN", "NE")))
    processed.write("nasa_power_point_daily", power_frame("2011-06-01", 500))
    return processed


# ----------------------------------------------------------------------- leakage


def test_the_vectorised_join_agrees_with_calendar_as_of() -> None:
    """The panel's as-of join must mean the same thing as pipeline.calendar.as_of.

    _as_of_join exists only because calling as_of once per observation is
    quadratic; if the two ever disagree, the fast one is wrong and the slow one
    is the specification.
    """
    timeline = panel.belt_drought(drought_frame(
        ["2012-06-05", "2012-06-12", "2012-06-19", "2012-06-26", "2012-07-03"]))
    grid = pd.DataFrame({"date": pd.to_datetime(
        ["2012-06-06", "2012-06-07", "2012-06-13", "2012-06-20", "2012-07-01", "2012-07-05"])})
    grid["cutoff"] = grid["date"]

    fast = panel._as_of_join(grid, timeline, ["d2"], peek_days=0)
    for position, cutoff in enumerate(grid["date"]):
        visible = as_of(timeline, "publication_date", cutoff.date())
        expected = visible["d2"].iloc[-1] if not visible.empty else np.nan
        actual = fast["d2"].iloc[position]
        assert (np.isnan(actual) and np.isnan(expected)) or actual == expected, (
            f"disagreement at {cutoff.date()}: fast={actual} as_of={expected}")


def test_a_feature_never_uses_a_map_published_after_the_observation(store, geography) -> None:
    """peek_days=0: the drought level on day t is the last map RELEASED by then.

    A map is valid from its Tuesday but released the Thursday after, so the two
    days in between must still show the previous map. This is the off-by-two the
    whole timing argument lives on.
    """
    frame, _ = panel.build(store, geography, crop="corn", peek_days=0)
    indexed = frame.set_index("date")["d2"]
    # The 2012-03-03 map carries d2=2.0 and is released 2012-03-05.
    assert indexed.loc["2012-03-02"] == 1.0, "a Friday before release saw the February map"
    assert indexed.loc["2012-03-05"] == 2.0, "release day is inclusive"
    assert indexed.loc["2012-03-06"] == 2.0


def test_peek_days_admits_later_data_by_exactly_the_amount_asked_for(store, geography) -> None:
    honest, _ = panel.build(store, geography, peek_days=0)
    peeking, spec = panel.build(store, geography, peek_days=3)
    assert spec.peek_days == 3

    honest = honest.set_index("date")["d2"]
    peeking = peeking.set_index("date")["d2"]
    # 2012-03-02 is three days before the 03-05 release, so a 3-day peek sees it
    # and a 2-day peek does not.
    assert honest.loc["2012-03-02"] == 1.0
    assert peeking.loc["2012-03-02"] == 2.0
    two_day, _ = panel.build(store, geography, peek_days=2)
    assert two_day.set_index("date")["d2"].loc["2012-03-02"] == 1.0
    # A peek can only ever add information, never remove it.
    assert (peeking.notna() >= honest.notna()).all()


def test_a_negative_peek_is_refused_rather_than_quietly_reversed(store, geography) -> None:
    with pytest.raises(ValueError, match="peek_days must be >= 0"):
        panel.build(store, geography, peek_days=-7)


def test_an_anomaly_is_standardised_only_against_earlier_years() -> None:
    """A z-score built from the whole sample would leak the future everywhere.

    The first year has no prior year, so it must be NaN rather than zero -- zero
    would read as "exactly average" and is the quiet version of this bug.
    """
    # Three Julys, each hotter than the last, same calendar days.
    frames = [power_frame(f"{year}-07-01", 31, tmax=36.0 + year - 2010)
              for year in (2010, 2011, 2012)]
    belt = panel.belt_weather(pd.concat(frames, ignore_index=True), "corn")
    z = belt.set_index("date")["heat_dd7_z"]
    assert z.loc["2010-07-31":"2010-07-31"].isna().all(), "year one standardised against itself"
    # 2012 is the hottest of the three and the only year with two priors.
    assert z.loc["2012-07-31"] > 0


# ------------------------------------------------------------------- aggregation


def test_heat_is_degree_days_above_the_threshold_not_a_mean() -> None:
    """Below-threshold days must contribute zero, not a negative offset.

    Averaging tmax lets a cool week cancel a hot one, which is the Schlenker and
    Roberts finding inverted: damage above 29 C does not undo itself below it.
    """
    assert panel.HEAT_THRESHOLD_C == 29.0
    tmax = [39.0] + [19.0] * 6          # one day 10 C over, six days 10 C under
    belt = panel.belt_weather(power_frame("2012-07-01", 7, tmax=tmax), "corn")
    assert belt["heat_dd7"].iloc[-1] == pytest.approx(10.0)
    # Exactly on the threshold contributes nothing.
    on_threshold = panel.belt_weather(power_frame("2012-07-01", 7, tmax=29.0), "corn")
    assert on_threshold["heat_dd7"].iloc[-1] == pytest.approx(0.0)


def test_the_belt_average_waits_for_the_last_point_to_publish() -> None:
    """An average of five points is knowable only when the slowest one lands."""
    early = power_frame("2012-07-01", 10, points=("Iowa",))
    late = power_frame("2012-07-01", 10, points=("Illinois",))
    late["publication_date"] = late["date"] + pd.Timedelta(days=9)   # four days slower
    belt = panel.belt_weather(pd.concat([early, late], ignore_index=True), "corn")
    row = belt.loc[belt["date"] == pd.Timestamp("2012-07-01")].iloc[0]
    assert row["points"] == 2
    assert row["publication_date"] == pd.Timestamp("2012-07-10")


def test_counties_are_averaged_within_a_state_before_states_are_averaged() -> None:
    """Equal weight per STATE, not per county.

    A flat mean over counties lets Iowa's 99 outvote Illinois's 102 by accident
    of how the states were subdivided, which is not a modelling choice anyone
    made on purpose.
    """
    rows = drought_frame(["2012-07-03"], states=("IA", "IL"),
                         d2={"IA": [100.0], "IL": [0.0]})
    # Give IA four counties against IL's two: a flat mean would read 66.7.
    extra = rows.loc[rows["state"] == "IA"].copy()
    extra["fips"] = extra["fips"] + "x"
    belt = panel.belt_drought(pd.concat([rows, extra], ignore_index=True))
    assert belt["d2"].iloc[0] == pytest.approx(50.0)
    assert belt["states"].iloc[0] == 2


def test_a_drought_delta_is_a_change_between_maps_not_between_days() -> None:
    weekly = [f"2012-0{m}-03" for m in (5, 6, 7)]
    belt = panel.belt_drought(drought_frame(weekly, states=("IA",),
                                            d2={"IA": [10.0, 25.0, 60.0]}))
    assert belt["d2_delta1"].tolist() == pytest.approx([np.nan, 15.0, 35.0], nan_ok=True)
    assert belt["d2_delta2"].iloc[-1] == pytest.approx(50.0)


# ------------------------------------------------------------------------ schema


def test_an_unfillable_feature_is_declared_rather_than_dropped(tmp_path, geography) -> None:
    """A half-backfilled store must produce the same columns as a full one.

    Otherwise the feature set depends on download progress, and a model trained
    on Tuesday is not the model retrained on Thursday.
    """
    processed = ProcessedStore(tmp_path)
    processed.write("yahoo_prices_daily", price_frame("2012-01-02", 60))
    processed.write("usdm_county_drought", drought_frame(["2012-01-03", "2012-02-07"]))
    # Cocoa points only: corn's weather cannot be built from this.
    processed.write("nasa_power_point_daily",
                    power_frame("2012-01-01", 40, crop="cocoa", points=("Soubre",)))

    frame, spec = panel.build(processed, geography, crop="corn")
    for column in ("heat_dd7", "heat_dd30_z", "precip30", "cty_tmax_c"):
        assert column in frame.columns, f"{column} was dropped instead of declared"
        assert frame[column].isna().all()
        assert column in spec.unavailable
    assert "d2" not in spec.unavailable
    assert "heat_dd7" in spec.feature_sources


def test_the_tradeable_split_follows_publication_lag_not_preference(store, geography) -> None:
    """nClimGrid is a month late, so its features can never be a trading signal."""
    _, spec = panel.build(store, geography)
    assert "d2" in spec.tradeable_features
    assert "heat_dd7" in spec.tradeable_features
    assert "cty_tmax_c" in spec.untradeable_features
    assert not set(spec.tradeable_features) & set(spec.untradeable_features)


def test_a_missing_price_series_names_itself(tmp_path, geography) -> None:
    processed = ProcessedStore(tmp_path)
    processed.write("yahoo_prices_daily", price_frame("2012-01-02", 20, series=("corn",)))
    processed.write("usdm_county_drought", drought_frame(["2012-01-03"]))
    processed.write("nasa_power_point_daily", power_frame("2012-01-01", 20))
    with pytest.raises(SystemExit, match="corn_fund"):
        panel.build(processed, geography, price_series=("corn", "corn_fund"))


# ----------------------------------------------------------------------- targets


def test_a_forward_return_looks_forward_and_the_last_rows_are_empty(store, geography) -> None:
    """The target is the only column allowed to see the future."""
    frame, spec = panel.build(store, geography, horizons=(5,))
    closes = frame["close_corn"].to_numpy()
    expected = closes[5] / closes[0] - 1.0
    assert frame["fwd5_corn"].iloc[0] == pytest.approx(expected)
    assert frame["fwd5_corn"].iloc[-5:].isna().all(), "a horizon must run off the end"
    assert spec.horizons == (5,)


def test_the_grid_is_the_primary_contract_trading_days(store, geography) -> None:
    """Features are joined onto days a position could actually be taken."""
    prices = store.read("yahoo_prices_daily")
    trading = prices.loc[prices["series"] == "corn", "date"].sort_values()
    frame, _ = panel.build(store, geography)
    assert frame["date"].tolist() == trading.tolist()
    assert not frame["date"].dt.dayofweek.isin([5, 6]).any()


def test_the_sensitive_window_filter_keeps_only_the_growing_season(store, geography) -> None:
    frame, _ = panel.build(store, geography, sensitive_months_only=True)
    window = geography.crop("corn").sensitive_months
    assert (window.start, window.end) == (4, 10)
    assert sorted(frame["month"].unique()) == list(range(4, 11))
