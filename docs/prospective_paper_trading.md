# Prospective paper-trading protocol

The frozen v1 model has exhausted its retrospective holdout. Its next valid
effectiveness evidence must begin with a decision saved before the entry and
outcome exist. The paper-trading ledger enforces that boundary and never submits
an order.

## Evidence model

Each observation has two immutable JSON records beneath ignored
`data/paper_trading/corn-wheat-all-public-boosting-v1/`:

1. A **decision record** is written on the model's decision date. It hashes the
   source signal, LSEG contract snapshot, and execution config. An actionable
   decision is rejected if the supplied contract panel already contains any
   shared ZC/ZW session after the decision date.
2. An **outcome record** is appended only after the next-open entry and five
   further shared sessions are present. It hashes the decision record and the
   resolving contract snapshot, records selected contracts, integer quantities,
   prices, costs, status, and return on average leg notional.

Neither record can be overwritten. Re-resolving an existing observation checks
that revised contract data do not alter its entry date, exit date, or execution
status. HOLD and stale-input decisions remain visible, but only a same-day
`READY_FOR_PAPER_TRADING` registration is eligible as prospective evidence.

## Weekly workflow

### 1. Refresh information available at the decision

Run the public-source fetch, clean, and feature workflow. Refresh the licensed
contract panel only through the current decision session. Workspace must be open
and signed in for the licensed download.

### 2. Save the frozen model decision

Do this on the decision date. Do not use `--dry-run`, because registration needs
the immutable saved signal record.

```bash
PYTHONPATH=src python -m pipeline signal --json
```

The command prints the record and saves it under ignored
`data/signals/corn-wheat-all-public-boosting-v1/`.

### 3. Register before the next session

```bash
PYTHONPATH=src python -m pipeline paper-register \
  --signal-record data/signals/corn-wheat-all-public-boosting-v1/RECORD.json \
  --contracts data/processed/futures_contracts.parquet
```

Registration refuses future decisions, late decisions, snapshots containing
future observations, models other than frozen v1, and any source record that
does not explicitly state that execution is unauthorized and zero orders were
submitted.

### 4. Resolve after the horizon completes

Refresh and import the LSEG contract panel, then run:

```bash
PYTHONPATH=src python -m pipeline paper-resolve \
  --contracts data/processed/futures_contracts.parquet
```

The resolver processes all pending decisions in chronological order so the
configured no-overlap rule applies across the prospective ledger. Use
`--dry-run` to inspect which outcomes are ready without writing them.

Inspect the accumulated evidence at any time without recalculating it:

```bash
PYTHONPATH=src python -m pipeline paper-status
```

## Interpretation boundary

- The workflow observes paper outcomes; it has no broker integration or order
  authority.
- The current prospective resolver remains price-only and reports average-leg-
  notional returns for continuity with the frozen replay. Historical margin is
  now available for the separate June 2020-June 2026 capital study, but has not
  been retrofitted into already registered prospective records.
- Operational failures, stale inputs, HOLD decisions, overlaps, and missing
  contracts belong in the ledger rather than being deleted from evaluation.
- The model, threshold, features, horizon, and execution config must remain
  frozen during the initial prospective period.
- Performance should not drive a v1 change until a predeclared minimum sample or
  full growing season is complete.

