"""Yahoo Finance: a plausible response at the wrong frequency is the danger here.

`range=max` returns MONTHLY bars. The envelope, the fields and the types are all
correct -- there are simply 269 bars where there should be ~6,500 -- so nothing
about it looks like a failure, and it would quietly destroy the daily timing test
this project rests on. The captured fixture for that is the most valuable file in
tests/fixtures/yahoo, and the assertion on it is the most valuable one here.

Second theme: currency is not constant across symbols. ZC=F is quoted in USX --
US CENTS per bushel -- while CORN and CC=F are USD. Nothing in the numbers says
so, so the column has to survive into the table.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pipeline.calendar import PUBLICATION_COLUMN
from pipeline.clean import yahoo_prices as clean_yahoo
from pipeline.clients import yahoo_prices as client
from pipeline.ingest import yahoo_prices as ingest_yahoo


@pytest.fixture
def yahoo(fixtures: Path) -> Path:
    return fixtures / "yahoo"


@pytest.fixture
def corn(yahoo: Path) -> bytes:
    return (yahoo / "chart_ZC-F_2012-07_1d.json").read_bytes()


# ----------------------------------------------------------------------- the URL


def test_the_symbol_is_percent_quoted_because_it_sits_in_the_path(sources) -> None:
    url = client.url(sources["yahoo_prices"], symbol="ZC=F",
                     start=date(2012, 7, 1), end=date(2012, 7, 31))
    assert "ZC%3DF" in url and "ZC=F" not in url


def test_both_period_bounds_are_sent_as_epoch_seconds(sources) -> None:
    # Explicitly, always: range=max is the alternative and it changes the frequency.
    url = client.url(sources["yahoo_prices"], symbol="ZC=F",
                     start=date(2012, 7, 1), end=date(2012, 7, 31))
    assert "period1=1341100800" in url
    # The end is pushed to the following midnight so the last session is included.
    assert "period2=1343779200" in url
    assert "interval=1d" in url


def test_a_reversed_window_is_refused(sources) -> None:
    with pytest.raises(client.YahooResponseError, match="ends before it starts"):
        client.url(sources["yahoo_prices"], symbol="ZC=F",
                   start=date(2012, 7, 31), end=date(2012, 7, 1))


# -------------------------------------------------------------- the range=max trap


def test_range_max_returns_monthly_bars_and_is_refused(yahoo: Path) -> None:
    body = (yahoo / "chart_ZC-F_rangemax_monthly_trap.json").read_bytes()
    payload = json.loads(body)["chart"]["result"][0]
    # The request asked for interval=1d. What came back:
    assert payload["meta"]["dataGranularity"] == "1mo"
    assert payload["meta"]["range"] == "max"
    assert len(payload["timestamp"]) == 269, "26 years of MONTHLY bars"

    # Nothing about the envelope is malformed, so only the granularity check catches it.
    with pytest.raises(client.YahooResponseError) as caught:
        client.parse(body, symbol="ZC=F", url="fixture", expected_granularity="1d")
    assert "range=max returns monthly bars" in str(caught.value)


def test_a_matching_granularity_is_accepted(corn) -> None:
    answer = client.parse(corn, symbol="ZC=F", url="fixture", expected_granularity="1d")
    assert answer.granularity == "1d"
    assert len(answer.sessions) == 21


# --------------------------------------------------------------------- currency


def test_a_futures_contract_and_its_fund_are_quoted_in_different_units(yahoo: Path) -> None:
    grain = client.parse((yahoo / "chart_ZC-F_2012-07_1d.json").read_bytes(), symbol="ZC=F")
    fund = client.parse((yahoo / "chart_CORN_2012-07_1d.json").read_bytes(), symbol="CORN")
    cocoa = client.parse((yahoo / "chart_CC-F_2012-07_1d.json").read_bytes(), symbol="CC=F")

    assert grain.currency == "USX", "US cents per bushel"
    assert fund.currency == "USD"
    assert cocoa.currency == "USD", "a future, but quoted in dollars per tonne"
    assert grain.exchange == "CBOT" and fund.exchange == "NYSEArca"
    # Same window, wildly different levels -- which is exactly why the unit matters.
    assert grain.sessions[0][1]["close"] > 10 * fund.sessions[0][1]["close"]


# ------------------------------------------------------------------ session dates


def test_the_session_date_is_read_in_the_exchange_offset(corn) -> None:
    answer = client.parse(corn, symbol="ZC=F")
    assert answer.gmt_offset_seconds == -14400
    assert answer.timezone_name == "America/New_York"
    assert answer.sessions[0][0] == date(2012, 7, 2)
    assert answer.last_session == date(2012, 7, 31)


def test_a_bar_is_dated_by_its_own_offset_not_by_utc_normalisation() -> None:
    # 1341201600 is 2012-07-02 04:00 UTC, which is 2012-07-02 00:00 in EDT. For a
    # venue whose session opened the previous evening the two answers differ, so
    # the offset is applied rather than assumed away.
    assert client._session_date(1341201600, -14400) == date(2012, 7, 2)
    assert client._session_date(1341201600, -21600 - 18000) == date(2012, 7, 1)


# --------------------------------------------------------------------- refusals


def test_an_unknown_symbol_raises_with_the_services_own_message(yahoo: Path) -> None:
    body = (yahoo / "error_404_unknown_symbol.json").read_bytes()
    # result is null, not an empty list.
    assert json.loads(body)["chart"]["result"] is None
    with pytest.raises(client.YahooResponseError, match="symbol may be delisted"):
        client.parse(body, symbol="NOTASYMBOL=F", url="fixture")


def test_an_empty_window_is_a_200_with_no_timestamp_key_at_all(yahoo: Path) -> None:
    body = (yahoo / "empty_weekend_window.json").read_bytes()
    payload = json.loads(body)["chart"]["result"][0]
    assert "timestamp" not in payload, "not an empty array -- the key is absent"
    assert payload["meta"]["symbol"] == "ZC=F"  # a complete meta block regardless
    with pytest.raises(client.YahooResponseError, match="no timestamps"):
        client.parse(body, symbol="ZC=F", url="fixture")


def test_a_quote_array_shorter_than_the_timestamps_is_refused(corn) -> None:
    payload = json.loads(corn)
    payload["chart"]["result"][0]["indicators"]["quote"][0]["close"].pop()
    with pytest.raises(client.YahooResponseError, match="against"):
        client.parse(json.dumps(payload).encode(), symbol="ZC=F", url="fixture")


def test_a_non_json_body_is_refused() -> None:
    with pytest.raises(client.YahooResponseError, match="not JSON"):
        client.parse(b"<html>rate limited</html>", symbol="ZC=F")


# ----------------------------------------------------------------------- ingest


def test_the_scope_flags_are_refused_out_loud_rather_than_silently_truncating(
        harness, no_network, capsys) -> None:
    # The window is not part of the key, so a narrowed fetch would write a short
    # series to the same key and `clean` would rebuild the table from it.
    ingest_yahoo.fetch(harness.fetch("yahoo_prices", years=(2012,), dry_run=True,
                                     today=date(2026, 9, 27)))
    out = capsys.readouterr().out
    assert "--years" in out and "does not narrow this source" in out.replace("do(es)", "does")


def test_a_dry_run_plans_one_key_per_configured_series(harness, no_network) -> None:
    records = ingest_yahoo.fetch(harness.fetch("yahoo_prices", dry_run=True,
                                               today=date(2026, 9, 27)))
    keys = [record.key for record in records]
    assert "corn/ZC=F" in keys and "corn_fund/CORN" in keys
    assert len(keys) == len(harness.sources["yahoo_prices"].params["symbols"])
    assert not harness.raw.manifest_path.exists()


def test_the_planned_window_always_starts_at_the_projects_first_year(harness, no_network) -> None:
    records = ingest_yahoo.fetch(harness.fetch("yahoo_prices", years=(2012,), dry_run=True,
                                               today=date(2026, 9, 27)))
    assert "period1=946684800" in records[0].url, "2000-01-01, not 2012"


# ------------------------------------------------------------------------ clean


def seed(harness, yahoo: Path, name: str, series: str, symbol: str) -> None:
    harness.seed("yahoo_prices", f"{series}/{symbol}", (yahoo / name).read_bytes(),
                 params={"series": series, "symbol": symbol})


def test_clean_builds_a_session_table_carrying_the_currency(harness, yahoo: Path) -> None:
    seed(harness, yahoo, "chart_ZC-F_2012-07_1d.json", "corn", "ZC=F")
    seed(harness, yahoo, "chart_CORN_2012-07_1d.json", "corn_fund", "CORN")
    clean_yahoo.clean(harness.clean("yahoo_prices"))
    table = harness.processed.read(clean_yahoo.TABLE)

    assert len(table) == 42
    assert dict(table.groupby("series")["currency"].first()) == {"corn": "USX", "corn_fund": "USD"}
    assert set(table.columns) >= {"series", "symbol", "currency", "exchange", "date",
                                  "open", "high", "low", "close", "volume", PUBLICATION_COLUMN}


def test_a_close_is_published_on_the_day_of_its_own_session(harness, yahoo: Path) -> None:
    seed(harness, yahoo, "chart_ZC-F_2012-07_1d.json", "corn", "ZC=F")
    clean_yahoo.clean(harness.clean("yahoo_prices"))
    table = harness.processed.read(clean_yahoo.TABLE)
    # Lag 0: a close is public when the session ends. Same day, not same instant.
    assert (table[PUBLICATION_COLUMN] == table["date"]).all()


def test_a_holiday_bar_with_no_close_is_dropped(harness, yahoo: Path, capsys) -> None:
    seed(harness, yahoo, "chart_ZC-F_2025-07_1d_null_close.json", "corn", "ZC=F")
    clean_yahoo.clean(harness.clean("yahoo_prices"))
    table = harness.processed.read(clean_yahoo.TABLE)
    # 2025-07-04 is in the series with a null close; left in it becomes a NaN return.
    assert date(2025, 7, 4) not in set(table["date"].dt.date)
    assert len(table) == 22
    assert table["close"].notna().all()
    assert "1 bar(s) dropped" in capsys.readouterr().out


def test_clean_refuses_a_monthly_archive(harness, yahoo: Path) -> None:
    # If a range=max response ever reached the archive, the clean step must not
    # quietly build a monthly table and call it daily.
    seed(harness, yahoo, "chart_ZC-F_rangemax_monthly_trap.json", "corn", "ZC=F")
    with pytest.raises(client.YahooResponseError, match="1mo"):
        clean_yahoo.clean(harness.clean("yahoo_prices"))


def test_clean_on_an_empty_archive_says_what_to_run(harness) -> None:
    with pytest.raises(SystemExit, match="nothing archived yet"):
        clean_yahoo.clean(harness.clean("yahoo_prices"))
