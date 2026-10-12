# LSEG Workspace futures extraction

The primary licensed-data route uses the LSEG Data Library against a signed-in
local Workspace session. It does not require Datastream Web Service (DSWS)
credentials and does not store a username, password, app key, or token.

This route was verified on October 12, 2026. The desktop session opened
successfully and returned expired CBOT corn, Chicago wheat, and ICE-US cocoa
contracts with daily open, high, low, settlement, volume, open interest, and
expiration. The adapter is `pipeline.lseg_workspace_futures`.

## 1. Install the optional client

Keep LSEG's licensed client separate from the dependencies needed for the
public-data pipeline:

```bash
python -m pip install -r requirements-lseg.txt
```

Start LSEG Workspace and sign in before downloading. The Data Library connects
to the desktop session on the local machine. Never put Workspace credentials in
`.env` or a repository file.

## 2. Inspect a request without contacting LSEG

The dry run validates the requested products and shows both canonical contract
identifiers and LSEG RICs. It neither opens Workspace nor writes files.

```bash
PYTHONPATH=src python -m pipeline futures-workspace-download \
  --start 2024-01-01 --end 2024-12-31 \
  --delivery-years 2024 --dry-run
```

LSEG reuses live RICs. Expired contracts therefore carry an archive-decade
suffix: for example, December 2012 corn is `CZ2^1`, while December 2024 corn is
`CZ24^2`. LSEG used one-digit archived years through 2023 (`CZ3^2`) and two
digits beginning with the 2024 delivery year. The saved export also carries a
stable canonical identifier such as `ZCZ24`.

ICE-US cocoa has a different convention. The discovery chain is `0#CC:` and
shows constituents such as `/CCZ6`, but the history endpoint accepts `CCZ6`
without the slash. Cocoa retains one-digit years: December 2024 is `CCZ4^2`,
not `CCZ24^2`. The adapter records the stable canonical contract as `CCZ24`.

## 3. Download and validate a narrow sample

```bash
PYTHONPATH=src python -m pipeline futures-workspace-download \
  --start 2024-01-01 --end 2024-12-31 \
  --delivery-years 2024 --stem sample_2024_year

PYTHONPATH=src python -m pipeline futures-import \
  --input data/raw/lseg_workspace/sample_2024_year.csv \
  --mapping data/raw/lseg_workspace/sample_2024_year.mapping.yaml \
  --dry-run
```

The downloader requests each contract separately and saves three ignored files:

- the CSV export with LSEG values in cents per bushel;
- a query manifest with fields, dates, RICs, row counts, transformations, and a
  SHA-256 hash;
- an importer mapping that feeds the vendor-neutral validation pipeline.

Rows missing date, expiration, or any OHLC value, and rows whose high/low does
not contain the session open, are excluded from the import export. The manifest
reports both exclusions contract by contract. Volume and open interest may
remain missing because they do not determine whether a price row is usable.

Before expanding the request, verify that both roots are present, expiration is
constant within each contract, prices are plausible cents-per-bushel values,
and several settlements agree with CME's published settlement records.
Settlement is audited separately from the intraday range because LSEG can report
a valid exchange settlement outside its high/low fields; every such row remains
visible as `SETTLEMENT_OUTSIDE_RANGE` information.

## 4. Expand to the model window

The research feature window begins in 2012, so the execution panel starts in
late 2011 and includes the five standard delivery months for both products.

```bash
PYTHONPATH=src python -m pipeline futures-workspace-download \
  --start 2011-10-01 --delivery-years 2012-2026

PYTHONPATH=src python -m pipeline futures-import \
  --input data/raw/lseg_workspace/zc_zw_daily.csv \
  --mapping data/raw/lseg_workspace/zc_zw_daily.mapping.yaml

PYTHONPATH=src python -m pipeline futures-audit \
  --contracts data/processed/futures_contracts.parquet
```

The command continues after an individual contract error, records it in the
manifest, and exits with status 2 when coverage is partial. Do not treat a
partial export as complete merely because it can be imported.

All `data/` files are ignored. Licensed row-level data and derivatives must not
be committed or redistributed without confirming the applicable LSEG license.

## 4a. Download the cocoa model window separately

Cocoa is quoted in US dollars per metric tonne, while ZC and ZW are quoted in
cents per bushel. Keep it in a separate export so the generated import mapping
can apply the correct 100× dollars-to-cents conversion.

```bash
PYTHONPATH=src python -m pipeline futures-workspace-download \
  --start 2011-10-01 --delivery-years 2012-2026 \
  --roots CC --stem cc_daily

PYTHONPATH=src python -m pipeline futures-import \
  --input data/raw/lseg_workspace/cc_daily.csv \
  --mapping data/raw/lseg_workspace/cc_daily.mapping.yaml \
  --out cocoa_futures_contracts
```

The verified October 12, 2026 pull contains 28,802 valid price rows from all 75
requested contracts. It is pricing-valid but not execution-ready because dated
ICE maintenance margin has not yet been attached. The preliminary model may use
the dated opens and explicit commission/slippage; a capital-constrained replay
must wait for authoritative margin history.

## 5. Attach CME dated margin separately

The Workspace price response does not provide dated maintenance margin. The
generated mapping therefore omits `maintenance_margin_usd`. Download the
official corn and wheat PDFs from [CME's historical-margin page](https://www.cmegroup.com/solutions/risk-management/margin-services/historical-margins.html)
and run:

```bash
PYTHONPATH=src python -m pipeline futures-margin-enrich \
  --contracts data/processed/futures_contracts.parquet \
  --corn-pdf /path/to/C-2020-to-present.pdf \
  --wheat-pdf /path/to/W-2020-to-present.pdf
```

The resulting ignored `futures_contracts_with_margin.parquet` is execution
ready for the public June 30, 2020 through June 30, 2026 source window. The
sidecar records both PDF hashes, the original contract-panel hash, coverage,
match counts, and the mapping interpretation. Do not extend the final rate past
the source end or repeat it into earlier years. Pre-2020 completion requires the
paid [CME DataMine history](https://datamine.new.cmegroup.com/catalog/dataset/77ccab5f5c224ab6811fad891b497276)
or another authoritative dated source.

## Later DSWS option

DSWS credentials are distinct from the Workspace desktop session. If a later
Datastream confirmation supplies those credentials and the required futures
entitlement, add it as a second retrieval backend that produces the same vendor
export schema. The importer, audit, and execution simulator should remain
unchanged.
