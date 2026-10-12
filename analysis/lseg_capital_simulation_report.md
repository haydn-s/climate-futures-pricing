# Frozen v1 capital-constrained LSEG replay

## Decision

**Dated exchange margins make the replay executable, but they do not rescue
the weak development evidence.** Capital changes position feasibility and
the return denominator; it does not create a stable pre-cost edge.

The least-negative development result in the fixed capital grid is -3.7% on $100,000 of starting capital. The holdout remains already consumed and is reported descriptively only.

## Capital scenarios

| Sample | Start capital | Signals | Executed | Margin skips | Net P&L | Return on capital | Max drawdown | Median margin | Max utilization |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| development | $10,000 | 27 | 24 | 0 | $-3,100 | -31.0% | -68.2% | $5,250 | 91.1% |
| development | $25,000 | 27 | 24 | 0 | $-12,585 | -50.3% | -71.2% | $13,438 | 96.4% |
| development | $50,000 | 27 | 24 | 0 | $-3,692 | -7.4% | -35.2% | $20,700 | 73.3% |
| development | $100,000 | 27 | 24 | 0 | $-3,692 | -3.7% | -20.7% | $20,700 | 35.6% |
| holdout | $10,000 | 13 | 13 | 0 | $4,470 | 44.7% | -30.5% | $9,450 | 89.0% |
| holdout | $25,000 | 13 | 13 | 0 | $8,250 | 33.0% | -16.4% | $11,850 | 77.7% |
| holdout | $50,000 | 13 | 13 | 0 | $8,250 | 16.5% | -10.1% | $11,850 | 38.3% |
| holdout | $100,000 | 13 | 13 | 0 | $8,250 | 8.2% | -5.7% | $11,850 | 19.0% |

Each sample starts with fresh capital. Realized net P&L changes the budget
available to the next non-overlapping trade. The simulator sums the two
outright leg margins and applies no portfolio spread credit, a conservative
choice. Costs remain $2.50 commission plus one $12.50 tick of slippage per
contract side. The quantity search still favors the smallest integer pair
with the lowest dollar-notional imbalance rather than scaling merely to use
all available capital.
Integer sizing makes P&L non-monotonic across account sizes; the grid is a
capacity diagnostic, not a choice of an 'optimal' starting balance. Drawdown
is measured on realized trade-close equity and excludes intratrade variation.

## Margin data

- Official [CME historical-margin](https://www.cmegroup.com/solutions/risk-management/margin-services/historical-margins.html) coverage used: 2020-06-30 through 2026-06-30.
- Official PDF rows parsed: 43,853; exact rates matched to LSEG contracts: 28,387.
- Observed outright maintenance range: $600 to $6,700 per contract.
- CME's reports label the values `Minimum Performance Bond Requirements`;
  this study uses that minimum account requirement as maintenance margin.
- `C-01`, `W-01`, and subsequent roll codes are joined to each date's
  expiry-ordered LSEG contract strip. All covered contract rows matched
  exactly by business date and roll index; no current rate was backfilled.
- The public PDFs currently begin June 30, 2020. Earlier frozen decisions
  are excluded. Full 2015 coverage requires an authoritative older source;
  the [CME DataMine agriculture margin CSV](https://datamine.new.cmegroup.com/catalog/dataset/77ccab5f5c224ab6811fad891b497276) is paid.

## Audit boundary

- Margin-enriched contract panel SHA-256: `6a4c51d06049b1df861ac6961c8536d5ca8685c7b2063f49aed3fa86767d9b5f`.
- Corn PDF SHA-256: `39f4a64a37e94eeeb1e9e8454b7a38ce9cc9c69d57f1316cdb4a3c68377d3910`.
- Wheat PDF SHA-256: `fe803f20b218193b0e7aa93c3d6590b6835d86bcebac301c81a3abb925c7f62f`.
- Row-level LSEG-derived panels and capital ledgers remain ignored under `data/`.
- The committed results are aggregates; they do not redistribute licensed prices.
- This is a post-freeze execution study, not a new model selection round.
- Exchange minimums do not include a broker's possible margin add-ons.
