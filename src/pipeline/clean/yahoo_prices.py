"""Archived chart responses to one daily price table, offline.

    PYTHONPATH=src python -m pipeline clean --source yahoo_prices  ->  yahoo_prices_daily

One row per series per session, dated in the exchange's own calendar, with
`currency` carried through because it is not the same across rows: CBOT grain
futures are quoted in USX -- US CENTS per bushel -- while the Teucrium CORN fund
is USD. Comparing a level across the two without converting is meaningless, and
nothing in the numbers themselves says so.

publication_date is the session date: a close is public when the session ends.
That is a same-DAY statement, not a same-instant one, which matters for exactly
one comparison in this project -- a Thursday close against a Drought Monitor map
released at 08:30 ET that morning. Use pipeline.calendar.as_of with a datetime
when that distinction is load-bearing.

These are continuous front-month series: they JUMP AT CONTRACT ROLLS, and the
jump is not a return. `corn_fund` (CORN) is here as the roll-free comparison.
"""

from __future__ import annotations

import pandas as pd

from ..calendar import PUBLICATION_COLUMN
from ..cli import CleanRequest
from ..clients import yahoo_prices as client
from ._common import archived

TABLE = "yahoo_prices_daily"
FIELDS = ("open", "high", "low", "close", "volume")


def clean(request: CleanRequest) -> list:
    expected = str(request.spec.params.get("interval", "1d"))
    rows: list[dict] = []
    series_count = 0

    for record, body in archived(request.raw, request.spec.name):
        series = str(record.params.get("series") or record.key.split("/")[0])
        symbol = str(record.params.get("symbol") or record.key.split("/")[-1])
        answer = client.parse(body, symbol=symbol, url=str(record.path),
                              expected_granularity=expected)
        series_count += 1
        for day, values in answer.sessions:
            rows.append({
                "series": series, "symbol": answer.symbol, "currency": answer.currency,
                "exchange": answer.exchange, "date": day,
                **{name: values.get(name) for name in FIELDS},
                PUBLICATION_COLUMN: day,
            })

    if not rows:
        raise SystemExit(
            "yahoo_prices: nothing archived yet -- run "
            "`python -m pipeline fetch --source yahoo_prices` first"
        )

    table = pd.DataFrame(rows)
    table["date"] = pd.to_datetime(table["date"])
    table[PUBLICATION_COLUMN] = pd.to_datetime(table[PUBLICATION_COLUMN])
    for name in FIELDS:
        table[name] = pd.to_numeric(table[name], errors="coerce")

    # A bar with no close is a holiday or a halt, not a price. Yahoo returns them
    # inside the series (104 of 6,474 for ZC=F), and left in they become NaN
    # returns that a naive fillna would carry forward across a market closure.
    blank = int(table["close"].isna().sum())
    table = table[table["close"].notna()]
    before = len(table)
    table = (table.drop_duplicates(subset=["series", "date"], keep="last")
             .sort_values(["series", "date"]).reset_index(drop=True))
    duplicates = before - len(table)

    print(f"  {len(table)} session-days across {series_count} series "
          f"({', '.join(sorted(table['series'].unique()))}), "
          f"{table['date'].min().date()}..{table['date'].max().date()}")
    for currency, group in table.groupby("currency"):
        print(f"    {currency}: {', '.join(sorted(group['series'].unique()))}")
    if blank:
        print(f"    {blank} bar(s) dropped for having no close (holidays and halts)")
    if duplicates:
        print(f"    {duplicates} duplicated series-day(s) de-duplicated")
    if request.dry_run:
        print(f"  would write {request.processed.path(TABLE)}")
        return []
    return [request.processed.write(TABLE, table)]
