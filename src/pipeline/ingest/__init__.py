"""One module per source: fetch and archive raw bytes, deciding nothing about shape.

Each module defines `fetch(request: FetchRequest) -> Sequence[RawRecord]` (see
pipeline.cli for the request contract) and is reached by
`python -m pipeline fetch --source <key>`. What an ingest module owes the rest of
the pipeline:

  * archive through RawStore.save, so every fetch is hashed, dated and manifested;
  * pass the source's publication date when it has one -- Last-Modified for
    nClimGrid, mapDate + 2 days for USDM -- and None rather than a guess when it
    does not;
  * skip the download when the archived content is already current, unless
    request.force is set, in which case re-fetch and let a change add a version;
  * honour request.dry_run by reporting the URLs it would have fetched.

One module per source key: nclimgrid.py, nasa_power.py, usdm.py,
yahoo_prices.py, owid_yields.py, nass_production.py. _common.py is not a source
module -- it holds the handful of answers the CLI contract fixes for everyone
(how a dry run reports a fetch it did not make, how a scope becomes a list of
years, how a place name becomes a store key, how long to pause between requests).

This file is a package marker; the modules inside belong to their source's author.
"""
