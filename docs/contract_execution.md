# Contract-level execution simulation

The model's continuous corn and wheat price histories are research inputs, not
tradable instruments. A realistic paper trade needs a particular contract in
each leg, integer quantities, a fill time, transaction costs, and enough margin.
`pipeline.futures_execution` is the first contract-level execution layer.

## Required contract panel

Provide one row per session, root, and listed contract:

| Column | Meaning |
|---|---|
| `date` | Trading session date |
| `root` | `ZC` for corn or `ZW` for Chicago SRW wheat |
| `contract` | Full month contract, such as `ZCZ26` |
| `last_trade_date` | Actual last trading date for that contract |
| `open_cents` | Session open in cents per bushel |
| `settlement_cents` | Official session settlement used by the validation replay |
| `maintenance_margin_usd` | Dated maintenance margin for one contract |

Margin is deliberately required for the full simulator. It changes over time
and by contract, so the simulator does not carry a timeless hard-coded value.
The narrower price-only validation replay described below can run without it.
Contract metadata and prices must come from a source whose license permits
project use; do not reconstruct contracts from the existing Yahoo continuous
series.

## Execution policy

- A decision fills at the next session shared by corn and wheat, using that
  session's open.
- The position exits after five further shared sessions.
- The selected contract must still be more than ten business days from last
  trade at the planned exit. This avoids an implicit mid-trade roll, and the
  ledger records when the nearest contract was skipped for the next eligible
  month.
- BUY is long corn and short wheat; SELL is short corn and long wheat.
- Quantities are the small integer pair with the lowest dollar-notional
  imbalance, subject to the configured contract and margin limits.
- Each entry and exit pays configurable commission and one tick of configurable
  slippage per contract side.
- Signals that overlap an open five-session position are skipped by default.
- P&L is reported in dollars and as return on posted maintenance margin.

Exchange mechanics live beside clearly labelled research assumptions in
`config/futures_execution.yaml`. Every result should retain that config and a
hash of the input panel.

## Current boundary

The validation, normalized import, quality audit, signal reader, ledger engine,
and licensed Workspace price downloader are implemented. The downloader uses
the signed-in local desktop session and creates an ignored export, query
manifest, and ready-to-use import mapping. Follow
[`lseg_extraction_checklist.md`](lseg_extraction_checklist.md).

The commands are deliberately usable before margin is resolved. A sound pricing
export can pass import validation while the audit still says
`execution ready: NO`; the backtest refuses to proceed until dated margin is
present.

```bash
PYTHONPATH=src python -m pipeline futures-workspace-download \
  --start 2024-01-01 --end 2024-12-31 --delivery-years 2024 --dry-run
PYTHONPATH=src python -m pipeline futures-import --input EXPORT.csv \
  --mapping IMPORT_MAPPING.yaml --dry-run
PYTHONPATH=src python -m pipeline futures-audit \
  --contracts data/processed/futures_contracts.parquet
PYTHONPATH=src python -m pipeline futures-backtest \
  --contracts data/processed/futures_contracts.parquet \
  --signals SIGNALS.csv --dry-run
```

## Margin-free validation replay

The licensed price panel supports a narrower validation before historical
margin is available. `analysis/lseg_signal_replay.py` rebuilds the frozen v1
out-of-sample predictions, attaches dated-contract settlement outcomes on the
original target dates, and separately simulates the configured next-open entry
and five-session exit. It does not refit the model or select a new threshold.

The next-open replay sizes the least-imbalanced small-integer spread from entry
prices alone. Return is divided by average leg notional, not by invented margin,
so the output must not be presented as return on capital. It still applies
commission, slippage, roll-window selection, and the no-overlap rule.

```bash
PYTHONPATH=src python analysis/lseg_signal_replay.py
```

The October 12, 2026 replay finds high agreement between Yahoo continuous and
LSEG dated-settlement targets, but unstable executable performance: development
next-open net return is −7.1% after base costs while the 17-trade consumed
holdout remains +7.3%. See
[`../analysis/lseg_signal_replay_report.md`](../analysis/lseg_signal_replay_report.md)
for the full comparison and sensitivity grid. The detailed licensed ledger is
written to ignored `data/analysis/`; only aggregate results belong in version
control.

## Dated-margin capital replay

[CME's public historical-margin page](https://www.cmegroup.com/solutions/risk-management/margin-services/historical-margins.html)
currently links corn and wheat "Minimum
Performance Bond Requirements" PDFs covering June 30, 2020 through June 30,
2026. Install `pypdf` through `requirements.txt`, download both official files,
then create the execution-ready covered-date panel:

```bash
PYTHONPATH=src python -m pipeline futures-margin-enrich \
  --contracts data/processed/futures_contracts.parquet \
  --corn-pdf /path/to/C-2020-to-present.pdf \
  --wheat-pdf /path/to/W-2020-to-present.pdf
PYTHONPATH=src python analysis/lseg_capital_simulation.py
```

The parser retains the CME business date, roll product code, source URL, and
PDF hashes. `C-01`/`W-01` and subsequent codes are mapped to the LSEG strip in
last-trade-date order. Rates are joined only on exact business dates; none are
filled before or after the public source window. The simulation sums the two
outright requirements and takes no SPAN spread credit. It therefore remains a
conservative research approximation and does not include broker add-ons.

The public files do not cover the earlier 2015-2020 model decisions. A complete
full-history replay requires the paid [CME DataMine agriculture margin CSV](https://datamine.new.cmegroup.com/catalog/dataset/77ccab5f5c224ab6811fad891b497276)
or an equivalently authoritative source; current margins must never be repeated
backward.
