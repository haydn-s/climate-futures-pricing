"""One module per source: everything that knows how a single API actually behaves.

A client turns a SourceSpec plus a scope into URLs, fetches them through
pipeline.http, and returns bytes together with whatever the response said about
its own vintage (status token, Last-Modified, version sidecar). It does not
decide what to archive or how to reshape anything -- that is ingest/ and clean/.

Module per source key in config/sources.yaml: nclimgrid.py, nasa_power.py,
usdm.py, yahoo_prices.py, owid_yields.py, nass_production.py. Each is written
against captured bytes -- except nass_production.py, which had no key available
and was written from USDA's documentation; it says so at the top and lists what
to verify on the first real run.

This file is a package marker; the modules inside belong to their source's
author.
"""
