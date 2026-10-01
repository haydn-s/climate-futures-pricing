"""One module per source: archived bytes to a tidy processed table, offline.

Each module defines `clean(request: CleanRequest) -> Sequence[Path]` (see
pipeline.cli for the request contract) and is reached by
`python -m pipeline clean --source <key>`. What a clean module owes the rest of
the pipeline:

  * read only from RawStore -- a clean run must work with the network unplugged,
    which is also what makes it reproducible;
  * carry a publication date column named PUBLICATION_COLUMN on anything dated,
    so pipeline.calendar.as_of can reconstruct what was knowable on a past date;
  * convert sentinels to NaN before aggregating (nClimGrid's -999.99 turns a July
    mean of 34.18 into -0.3 if it survives) and never infer a month's length from
    a column count;
  * keep the source's own vintage markers (USDM's statisticFormatID, nClimGrid's
    scaled/prelim status) in the table rather than dropping them.

One module per source key: nclimgrid.py, nasa_power.py, usdm.py,
yahoo_prices.py, owid_yields.py, nass_production.py, each writing one named table.
_common.py is not a source module; it walks the archive for a source and folds
the accents out of a place name.

This file is a package marker; the modules inside belong to their source's author.
"""
