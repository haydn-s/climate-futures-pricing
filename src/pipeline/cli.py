"""The pipeline's command line: check-config, fetch, clean, status.

    PYTHONPATH=src python -m pipeline check-config
    PYTHONPATH=src python -m pipeline fetch --source nclimgrid --years 2012 --months 4-10
    PYTHONPATH=src python -m pipeline clean --source usdm
    PYTHONPATH=src python -m pipeline status

This module owns the dispatch contract, and nothing else. `fetch --source X`
imports pipeline.ingest.X and calls its `fetch(request)`; `clean --source X`
imports pipeline.clean.X and calls its `clean(request)`. Both take a single
request object, so four modules written by four hands cannot disagree about
argument order, and a new source needs no change here.

    # src/pipeline/ingest/usdm.py
    from pipeline.cli import FetchRequest
    from pipeline.storage import RawRecord

    def fetch(request: FetchRequest) -> list[RawRecord]:
        ...

Two guarantees the CLI makes so the ingest modules do not each have to:
a source declaring an api_key_env is resolved BEFORE dispatch, so a missing key
fails immediately with a message naming where to get one; and --dry-run is
passed through rather than interpreted here, because only the source's own
module knows which URLs it would have fetched.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import (
    DEFAULT_GEOGRAPHY_PATH,
    DEFAULT_SOURCES_PATH,
    ConfigError,
    Geography,
    SourceSpec,
    load_dotenv,
    load_geography,
    load_sources,
)
from .storage import ProcessedStore, RawStore, default_data_root

INGEST_PACKAGE = "pipeline.ingest"
CLEAN_PACKAGE = "pipeline.clean"


@dataclass(frozen=True)
class FetchRequest:
    """Everything `pipeline.ingest.<source>.fetch(request)` is given.

    Empty `years`, `months` and `states` mean "the source's own default scope",
    not "nothing" -- a module decides what its full scope is, since a month means
    something different to nClimGrid than to Yahoo. `api_key` is already resolved
    (None when the source needs none), so no module reads the environment itself.
    """

    spec: SourceSpec
    geography: Geography
    raw: RawStore
    processed: ProcessedStore
    years: tuple[int, ...] = ()
    months: tuple[int, ...] = ()
    states: tuple[str, ...] = ()
    force: bool = False
    dry_run: bool = False
    api_key: str | None = None
    # Injected rather than read from the clock, so a run can be reproduced and a
    # test cannot drift. Modules should prefer this over datetime.now(). UTC, like
    # every other date in the pipeline: a local "today" would move a publication
    # decision by a day at the boundary, which is the error this project is about.
    today: date = field(default_factory=lambda: datetime.now(timezone.utc).date())


@dataclass(frozen=True)
class CleanRequest:
    """Everything `pipeline.clean.<source>.clean(request)` is given.

    A clean step reads from `raw` and writes named tables through `processed`; it
    must not touch the network, so it gets no key and no scope filters.
    """

    spec: SourceSpec
    geography: Geography
    raw: RawStore
    processed: ProcessedStore
    dry_run: bool = False


# --------------------------------------------------------------------- arguments


def _int_list(values: Iterable[str], label: str, low: int, high: int) -> tuple[int, ...]:
    """Parse `2012 2013` and `2012-2015` alike, so a range needs no shell loop."""
    parsed: list[int] = []
    for value in values:
        for piece in str(value).split(","):
            piece = piece.strip()
            if not piece:
                continue
            start, _, end = piece.partition("-")
            try:
                bounds = (int(start), int(end or start))
            except ValueError:
                raise SystemExit(f"--{label}: {piece!r} is not a number or a range like 4-10")
            if not low <= bounds[0] <= bounds[1] <= high:
                raise SystemExit(f"--{label}: {piece!r} is outside {low}-{high}")
            parsed.extend(range(bounds[0], bounds[1] + 1))
    return tuple(dict.fromkeys(parsed))


def _states(values: Iterable[str]) -> tuple[str, ...]:
    codes = [code.strip().upper() for value in values for code in str(value).split(",") if code.strip()]
    return tuple(dict.fromkeys(codes))


def _global_options() -> argparse.ArgumentParser:
    """The three options every subcommand shares, as a FRESH parent parser.

    Called once per parser rather than shared, and that is not a style choice.
    `parents=[shared]` copies action objects BY REFERENCE, and
    `parser.set_defaults()` reaches into the matching action and overwrites its
    `default` -- so one shared instance means setting the top-level default also
    replaces every subparser's argparse.SUPPRESS with that value. The subparser
    then writes it back over whatever was given before the verb, and
    `--data-root X status` silently loses X while `status --data-root X` works.
    A separate instance per parser keeps SUPPRESS where it belongs.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--sources-config", default=argparse.SUPPRESS)
    common.add_argument("--geography-config", default=argparse.SUPPRESS)
    common.add_argument("--data-root", default=argparse.SUPPRESS,
                        help="data directory holding raw/, processed/ and manifest.jsonl "
                             "(default: the repository's data/)")
    return common


def build_parser() -> argparse.ArgumentParser:
    # The three global options work on either side of the verb: `fetch --data-root
    # X` and `--data-root X fetch` mean the same thing. See _global_options for why
    # each parser gets its own copy of them.
    parser = argparse.ArgumentParser(prog="python -m pipeline", parents=[_global_options()],
                                     description=__doc__.splitlines()[0])
    parser.set_defaults(sources_config=DEFAULT_SOURCES_PATH,
                        geography_config=DEFAULT_GEOGRAPHY_PATH, data_root=None)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("check-config", parents=[_global_options()],
                        help="validate both config files and report key status")

    fetch = commands.add_parser("fetch", parents=[_global_options()],
                                help="archive raw bytes for one source")
    fetch.add_argument("--source", required=True)
    fetch.add_argument("--years", nargs="+", default=[], help="2012, 2000-2025, or a mix")
    fetch.add_argument("--months", nargs="+", default=[], help="7, 4-10, or a mix")
    fetch.add_argument("--states", nargs="+", default=[], help="postal codes: IA IL")
    fetch.add_argument("--force", action="store_true",
                       help="re-fetch even when the content is already archived; a change "
                            "adds a version, it never replaces one")
    fetch.add_argument("--dry-run", action="store_true", help="report what would be fetched")

    clean = commands.add_parser("clean", parents=[_global_options()],
                                help="turn archived bytes into a processed table")
    clean.add_argument("--source", required=True)
    clean.add_argument("--dry-run", action="store_true")

    features = commands.add_parser("features", parents=[_global_options()],
                                   help="build the point-in-time feature panel")
    features.add_argument("--crop", default="corn")
    features.add_argument("--peek", type=int, default=0,
                          help="days of NOT-YET-PUBLISHED data features may use. 0 is the "
                               "tradeable baseline; a positive value measures how much signal "
                               "arrives too late to trade and is never a result on its own")
    features.add_argument("--horizons", nargs="+", default=["5", "10"],
                          help="forward return horizons in trading days")
    features.add_argument("--growing-season", action="store_true",
                          help="keep only the crop's sensitive months")
    features.add_argument("--out", default=None,
                          help="table name to write (default features_<crop>_peek<n>)")
    features.add_argument("--dry-run", action="store_true",
                          help="report the panel's shape and coverage without writing")

    status = commands.add_parser("status", parents=[_global_options()],
                                 help="what is archived, and how current it is")
    status.add_argument("--source", default=None)
    return parser


# --------------------------------------------------------------------- commands


def _spec(sources: dict[str, SourceSpec], name: str) -> SourceSpec:
    if name not in sources:
        raise SystemExit(f"unknown source {name!r} (configured: {', '.join(sorted(sources))})")
    return sources[name]


def _module(package: str, source: str, entry_point: str) -> Any:
    """Import a per-source module, or explain exactly what is missing.

    The ingest and clean modules are written per source and may not exist yet;
    saying so plainly beats an ImportError traceback.
    """
    try:
        module = importlib.import_module(f"{package}.{source}")
    except ModuleNotFoundError as exc:
        if exc.name not in (f"{package}.{source}", source):
            raise
        raise SystemExit(
            f"no {package.rsplit('.', 1)[-1]} module for source {source!r} yet: expected "
            f"src/{package.replace('.', '/')}/{source}.py defining {entry_point}(request)"
        ) from exc
    if not hasattr(module, entry_point):
        raise SystemExit(f"{module.__name__} does not define {entry_point}(request)")
    return module


def check_config(sources: dict[str, SourceSpec], geography: Geography) -> int:
    names = load_dotenv()
    if names:
        print(f".env supplied: {', '.join(sorted(names))}")  # names only, never values

    print(f"{len(sources)} sources")
    unconfigured: list[str] = []
    for name, spec in sources.items():
        lag = "per record" if spec.publication_lag_days is None else f"{spec.publication_lag_days}d"
        key = "keyless"
        if spec.needs_key:
            try:
                spec.api_key()
                key = f"{spec.api_key_env} set"
            except ConfigError:
                key = f"{spec.api_key_env} MISSING"
                unconfigured.append(name)
        print(f"  {name:16s} {spec.kind:5s} lag {lag:11s} {key}")

    print(f"{len(geography.crops)} crops")
    for crop in geography:
        window = crop.sensitive_months
        scope = ", ".join(filter(None, [
            f"{len(crop.states)} states" if crop.states else "",
            f"{len(crop.counties)} counties" if crop.counties else "",
            f"{len(crop.points)} points" if crop.points else "",
        ])) or "no geography yet"
        print(f"  {crop.name:16s} months {window.start:2d}-{window.end:<2d} {scope}")

    for name in unconfigured:
        spec = sources[name]
        print(f"\n{name}: not configured -- {spec.api_key_env} is unset. "
              f"Get a free key at {spec.api_key_signup_url} and export it or put it in .env.")
    print("\nconfig ok")
    return 0


def fetch_source(args: argparse.Namespace, sources: dict[str, SourceSpec], geography: Geography,
                 raw: RawStore, processed: ProcessedStore) -> int:
    spec = _spec(sources, args.source)
    # Order matters: reject a bad --years before anything else, then resolve the
    # key so an unset NASS_API_KEY says so immediately rather than after a module
    # has started work, and only then import the module.
    years = _int_list(args.years, "years", 1951, 2100)
    months = _int_list(args.months, "months", 1, 12)
    states = _states(args.states)
    api_key = spec.api_key()
    module = _module(INGEST_PACKAGE, spec.name, "fetch")
    records = module.fetch(FetchRequest(
        spec=spec,
        geography=geography,
        raw=raw,
        processed=processed,
        years=years,
        months=months,
        states=states,
        force=args.force,
        dry_run=args.dry_run,
        api_key=api_key,
    )) or []
    verb = "would fetch" if args.dry_run else "archived"
    print(f"{spec.name}: {verb} {len(records)} file(s)")
    for record in records:
        published = record.publication_date.isoformat() if record.publication_date else "unpublished"
        print(f"  {record.key:28s} {record.sha256[:8]} {record.bytes:>9d}B published {published}")
    return 0


def clean_source(args: argparse.Namespace, sources: dict[str, SourceSpec], geography: Geography,
                 raw: RawStore, processed: ProcessedStore) -> int:
    spec = _spec(sources, args.source)
    module = _module(CLEAN_PACKAGE, spec.name, "clean")
    written = module.clean(CleanRequest(
        spec=spec, geography=geography, raw=raw, processed=processed, dry_run=args.dry_run,
    )) or []
    print(f"{spec.name}: {len(written)} table(s)")
    for path in written:
        print(f"  {path}")
    return 0


def status(args: argparse.Namespace, sources: dict[str, SourceSpec], raw: RawStore,
           processed: ProcessedStore) -> int:
    history = raw.history(args.source)
    print(f"raw archive: {raw.root}")
    if not history:
        print("  nothing archived yet")
    for name in raw.sources():
        if args.source and name != args.source:
            continue
        records = [record for record in history if record.source == name]
        versions = {(record.key, record.sha256) for record in records}
        newest = max(records, key=lambda record: record.fetched_at)
        published = [record.publication_date for record in records if record.publication_date]
        print(f"  {name:16s} {len(set(r.key for r in records)):4d} keys "
              f"{len(versions):5d} versions {len(records):5d} fetches"
              f"  newest fetch {newest.fetched_at.date()}"
              f"  newest publication {max(published).isoformat() if published else 'n/a'}")

    tables = processed.names()
    print(f"processed tables: {', '.join(tables) if tables else 'none'}")
    missing = [spec.api_key_env for spec in sources.values()
               if spec.needs_key and _key_missing(spec)]
    if missing:
        print(f"unconfigured secrets: {', '.join(missing)}")
    return 0


def _key_missing(spec: SourceSpec) -> bool:
    try:
        spec.api_key()
    except ConfigError:
        return True
    return False


def build_features(args: argparse.Namespace, geography: Geography,
                   processed: ProcessedStore) -> int:
    """Build and write the feature panel, reporting what it could not fill.

    The coverage report is not decoration. A feature the archive cannot fill is
    an all-NaN column rather than a missing one (see features.panel.PanelSpec),
    so a half-backfilled store still produces a full schema -- and the only place
    that becomes visible is here.
    """
    from .features import panel as feature_panel

    horizons = _int_list(args.horizons, "horizons", 1, 250)
    frame, spec = feature_panel.build(
        processed, geography, crop=args.crop, peek_days=args.peek,
        horizons=horizons, sensitive_months_only=args.growing_season)

    name = args.out or f"features_{args.crop}_peek{args.peek}"
    span = f"{frame['date'].min().date()}..{frame['date'].max().date()}"
    print(f"{args.crop}: {len(frame)} rows x {len(frame.columns)} columns, {span}, "
          f"peek {args.peek}d")
    print(f"  tradeable   {len(spec.tradeable_features):2d}  {', '.join(spec.tradeable_features)}")
    print(f"  untradeable {len(spec.untradeable_features):2d}  "
          f"{', '.join(spec.untradeable_features)}  (publication lag exceeds the horizon)")
    if spec.unavailable:
        print(f"  UNFILLED    {len(spec.unavailable):2d}  {', '.join(spec.unavailable)}"
              f"  -- archived data does not cover these; fetch and clean their source")
    for column in (f"fwd{h}_{spec.price_series[0]}" for h in horizons):
        print(f"  target {column}: {frame[column].notna().sum()} of {len(frame)} rows")
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    path = processed.write(name, frame)
    print(f"wrote {path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.data_root) if args.data_root else default_data_root()
    raw, processed = RawStore(root), ProcessedStore(root)
    try:
        sources = load_sources(args.sources_config)
        geography = load_geography(args.geography_config)
        if args.command == "check-config":
            return check_config(sources, geography)
        if args.command == "fetch":
            return fetch_source(args, sources, geography, raw, processed)
        if args.command == "clean":
            return clean_source(args, sources, geography, raw, processed)
        if args.command == "features":
            return build_features(args, geography, processed)
        return status(args, sources, raw, processed)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # `python -m pipeline.cli` also works
    raise SystemExit(main())
