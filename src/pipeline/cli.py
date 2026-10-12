"""The pipeline command line: ingest, features, signals, and futures execution.

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
import hashlib
import importlib
import json
import sys
from dataclasses import asdict, dataclass, field
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

    signal = commands.add_parser(
        "signal", parents=[_global_options()],
        help="fit the frozen model and emit an auditable paper-trading signal")
    signal.add_argument(
        "--as-of", type=date.fromisoformat, default=None,
        help="latest date the run may use (YYYY-MM-DD; default: current UTC date)")
    signal.add_argument(
        "--dry-run", action="store_true",
        help="print the signal without saving a model artifact or decision record")
    signal.add_argument(
        "--json", action="store_true", dest="output_json",
        help="write the complete machine-readable decision record to stdout")

    workspace = commands.add_parser(
        "futures-workspace-download", parents=[_global_options()],
        help="download licensed ZC/ZW/CC contract history through signed-in Workspace")
    workspace.add_argument(
        "--start", type=date.fromisoformat, default=date(2011, 10, 1),
        help="first trading date (YYYY-MM-DD; default: 2011-10-01)")
    workspace.add_argument(
        "--end", type=date.fromisoformat, default=None,
        help="last trading date (YYYY-MM-DD; default: current UTC date)")
    workspace.add_argument(
        "--delivery-years", nargs="+", required=True,
        help="delivery years: 2024, 2012-2026, or a mix")
    workspace.add_argument(
        "--roots", nargs="+", default=["ZC", "ZW"], choices=["ZC", "ZW", "CC"],
        help="licensed product roots to download; CC must be a separate export")
    workspace.add_argument(
        "--out-dir", default=None,
        help="output directory (default: <data-root>/raw/lseg_workspace)")
    workspace.add_argument("--stem", default="zc_zw_daily",
                           help="base filename for export and sidecars")
    workspace.add_argument("--dry-run", action="store_true",
                           help="list the request without opening Workspace")
    workspace.add_argument("--json", action="store_true", dest="output_json")

    futures_import = commands.add_parser(
        "futures-import", parents=[_global_options()],
        help="normalize a licensed individual-contract CSV or Parquet export")
    futures_import.add_argument("--input", required=True, help="vendor CSV or Parquet export")
    futures_import.add_argument(
        "--mapping", default="config/futures_import_template.yaml",
        help="explicit vendor-to-canonical YAML mapping")
    futures_import.add_argument("--out", default="futures_contracts",
                                help="processed table name")
    futures_import.add_argument("--dry-run", action="store_true")
    futures_import.add_argument("--json", action="store_true", dest="output_json")

    futures_audit = commands.add_parser(
        "futures-audit", parents=[_global_options()],
        help="audit a canonical individual-contract CSV or Parquet table")
    futures_audit.add_argument("--contracts", required=True)
    futures_audit.add_argument("--json", action="store_true", dest="output_json")

    futures_margin = commands.add_parser(
        "futures-margin-enrich", parents=[_global_options()],
        help="attach official CME dated minimum-performance-bond history")
    futures_margin.add_argument("--contracts", required=True)
    futures_margin.add_argument("--corn-pdf", required=True)
    futures_margin.add_argument("--wheat-pdf", required=True)
    futures_margin.add_argument(
        "--out", default="futures_contracts_with_margin",
        help="processed table name")
    futures_margin.add_argument("--dry-run", action="store_true")
    futures_margin.add_argument("--json", action="store_true", dest="output_json")

    futures_backtest = commands.add_parser(
        "futures-backtest", parents=[_global_options()],
        help="execute dated BUY/SELL/HOLD decisions against individual contracts")
    futures_backtest.add_argument("--contracts", required=True)
    futures_backtest.add_argument("--signals", required=True)
    futures_backtest.add_argument(
        "--execution-config", default="config/futures_execution.yaml")
    futures_backtest.add_argument("--capital", type=float, default=None,
                                  help="capital available for maintenance margin")
    futures_backtest.add_argument("--out", default="futures_trade_ledger")
    futures_backtest.add_argument("--dry-run", action="store_true")
    futures_backtest.add_argument("--json", action="store_true", dest="output_json")

    paper_register = commands.add_parser(
        "paper-register", parents=[_global_options()],
        help="immutably register a frozen signal before its first entry session")
    paper_register.add_argument("--signal-record", required=True)
    paper_register.add_argument("--contracts", required=True)
    paper_register.add_argument(
        "--execution-config", default="config/futures_execution.yaml")
    paper_register.add_argument("--dry-run", action="store_true")
    paper_register.add_argument("--json", action="store_true", dest="output_json")

    paper_resolve = commands.add_parser(
        "paper-resolve", parents=[_global_options()],
        help="append outcomes for registered paper signals when the horizon is complete")
    paper_resolve.add_argument("--contracts", required=True)
    paper_resolve.add_argument(
        "--execution-config", default="config/futures_execution.yaml")
    paper_resolve.add_argument("--dry-run", action="store_true")
    paper_resolve.add_argument("--json", action="store_true", dest="output_json")

    paper_status = commands.add_parser(
        "paper-status", parents=[_global_options()],
        help="summarize the immutable prospective paper ledger")
    paper_status.add_argument("--json", action="store_true", dest="output_json")
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


def produce_signal(args: argparse.Namespace, geography: Geography,
                   raw: RawStore, processed: ProcessedStore) -> int:
    """Run the frozen candidate without granting it authority to trade."""
    from .signal_inference import render_summary, run_primary_signal, save_run

    as_of = args.as_of or datetime.now(timezone.utc).date()
    try:
        run = run_primary_signal(
            raw=raw, processed=processed, geography=geography, as_of=as_of)
        record = run.record
        saved: tuple[Path, Path] | None = None
        if not args.dry_run:
            saved = save_run(run, raw.root)
            record = json.loads(saved[0].read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as exc:
        print(f"signal error: {exc}", file=sys.stderr)
        return 1

    if args.output_json:
        print(json.dumps(record, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(render_summary(record))
        if args.dry_run:
            print("  dry run: no model artifact or decision record written")
        elif saved:
            print(f"  record:        {saved[0]}")
            print(f"  model:         {saved[1]}")
    return 0


def download_workspace_futures(args: argparse.Namespace, raw: RawStore) -> int:
    from .lseg_workspace_futures import (
        WorkspaceFuturesError,
        contract_requests,
        download_workspace_history,
        save_workspace_download,
    )

    try:
        years = _int_list(args.delivery_years, "delivery-years", 2000, 2099)
        today = datetime.now(timezone.utc).date()
        end = args.end or today
        requests = contract_requests(years, args.roots, as_of=today)
        output_dir = Path(args.out_dir) if args.out_dir else raw.raw_dir / "lseg_workspace"
        if args.dry_run:
            payload = {
                "start": args.start.isoformat(),
                "end": end.isoformat(),
                "delivery_years": list(years),
                "roots": list(args.roots),
                "contracts": [asdict(request) for request in requests],
                "output_dir": str(output_dir),
            }
            if args.output_json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(
                    f"Workspace dry run: {len(requests)} contracts, "
                    f"{args.start}..{end} -> {output_dir}"
                )
                for request in requests:
                    print(f"  {request.contract:6s} {request.ric}")
                print("dry run: Workspace was not opened and nothing was written")
            return 0

        completed = 0

        def report(result: Any) -> None:
            nonlocal completed
            completed += 1
            stream = sys.stderr if args.output_json else sys.stdout
            suffix = (
                f"{result.rows_retained} rows"
                if result.status == "ok"
                else f"ERROR {result.error}"
            )
            print(
                f"[{completed:3d}/{len(requests)}] {result.contract} "
                f"{result.ric}: {suffix}",
                file=stream,
                flush=True,
            )

        download = download_workspace_history(
            start=args.start,
            end=end,
            delivery_years=years,
            roots=args.roots,
            as_of=today,
            progress=report,
        )
        export, manifest, mapping = save_workspace_download(
            download, output_dir, stem=args.stem
        )
    except (WorkspaceFuturesError, OSError, ValueError) as exc:
        print(f"Workspace futures download error: {exc}", file=sys.stderr)
        return 1

    failures = download.requested_contracts - download.successful_contracts
    payload = {
        "rows": len(download.frame),
        "requested_contracts": download.requested_contracts,
        "successful_contracts": download.successful_contracts,
        "failed_contracts": failures,
        "export": str(export),
        "manifest": str(manifest),
        "mapping": str(mapping),
    }
    if args.output_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"downloaded {len(download.frame):,} rows from "
            f"{download.successful_contracts}/{download.requested_contracts} contracts"
        )
        print(f"wrote {export}")
        print(f"wrote {manifest}")
        print(f"wrote {mapping}")
        if failures:
            print(
                f"warning: {failures} contract(s) failed; inspect the query manifest "
                "before importing"
            )
    return 0 if failures == 0 else 2


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_signal_decisions(path: str | Path) -> "pd.DataFrame":
    """Read a simple decision table or one/many production signal records."""
    import pandas as pd

    source = Path(path)
    if not source.exists():
        raise ValueError(f"signal input does not exist: {source}")
    if source.suffix.lower() == ".csv":
        frame = pd.read_csv(source)
    elif source.suffix.lower() in {".parquet", ".pq"}:
        frame = pd.read_parquet(source, engine="pyarrow")
    elif source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else [payload]
        rows = []
        for record in records:
            signal = record.get("signal", record) if isinstance(record, dict) else {}
            decision = record.get("decision", {}) if isinstance(record, dict) else {}
            rows.append({
                "decision_date": decision.get(
                    "date", signal.get("date", signal.get("decision_date"))
                ),
                "action": signal.get("action"),
            })
        frame = pd.DataFrame(rows)
    else:
        raise ValueError("signal input must be .json, .csv, .parquet, or .pq")
    if not {"decision_date", "action"}.issubset(frame.columns):
        raise ValueError("signal input must contain decision_date and action")
    return frame[["decision_date", "action"]]


def import_futures(args: argparse.Namespace, processed: ProcessedStore) -> int:
    from .futures_data import (
        FuturesDataError, import_contract_export, render_audit, save_import,
    )

    try:
        result = import_contract_export(args.input, args.mapping)
    except FuturesDataError as exc:
        print(f"futures import error: {exc}", file=sys.stderr)
        return 1
    audit = result.manifest["audit"]
    if args.output_json:
        print(json.dumps(result.manifest, indent=2, sort_keys=True, default=str))
    else:
        # Render from a fresh audit so the human form stays centralized.
        from .futures_data import audit_contract_panel
        print(render_audit(audit_contract_panel(result.frame)))
    if not audit["pricing_valid"]:
        print("futures import error: pricing audit failed; nothing written", file=sys.stderr)
        return 1
    if args.dry_run:
        if not args.output_json:
            print("dry run: no processed table or manifest written")
        return 0
    table, manifest = save_import(result, processed, name=args.out)
    if not args.output_json:
        print(f"wrote {table}")
        print(f"wrote {manifest}")
    return 0


def audit_futures(args: argparse.Namespace) -> int:
    from .futures_data import FuturesDataError, audit_contract_panel, read_export, render_audit

    try:
        report = audit_contract_panel(read_export(args.contracts))
    except FuturesDataError as exc:
        print(f"futures audit error: {exc}", file=sys.stderr)
        return 1
    if args.output_json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(render_audit(report))
    return 0 if report.pricing_valid else 1


def enrich_futures_margins(args: argparse.Namespace, processed: ProcessedStore) -> int:
    """Parse CME's public PDFs and create the executable covered-date panel."""

    from .cme_margin_data import (
        MarginDataError,
        attach_margin_history,
        parse_cme_margin_pdfs,
        pdf_manifest,
        sha256_file,
    )
    from .futures_data import FuturesDataError, audit_contract_panel, read_export

    contract_path = Path(args.contracts)
    pdf_paths = [Path(args.corn_pdf), Path(args.wheat_pdf)]
    try:
        contracts = read_export(contract_path)
        margins = parse_cme_margin_pdfs(
            corn_pdf=pdf_paths[0], wheat_pdf=pdf_paths[1]
        )
        attachment = attach_margin_history(contracts, margins, drop_unmatched=True)
        audit = audit_contract_panel(attachment.frame)
        if not audit.ready_for_execution:
            raise MarginDataError(
                "margin-enriched panel failed the execution-readiness audit"
            )
    except (MarginDataError, FuturesDataError, OSError, ValueError) as exc:
        print(f"futures margin error: {exc}", file=sys.stderr)
        return 1

    manifest = {
        **attachment.manifest(),
        "contracts_file": contract_path.name,
        "contracts_sha256": sha256_file(contract_path),
        "margin_files": pdf_manifest(pdf_paths),
        "margin_rows": len(margins),
        "roots": sorted(margins["root"].unique()),
        "audit": audit.as_dict(),
    }
    if args.output_json:
        print(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    else:
        print(
            "CME margin coverage: "
            f"{attachment.start_date} through {attachment.end_date}, "
            f"{len(margins):,} dated roll rates"
        )
        print(
            f"matched {attachment.matched_rows:,} of "
            f"{attachment.within_source_dates:,} contract rows; "
            f"dropped {attachment.dropped_unmatched_rows:,} farther expiries"
        )
        print("execution ready: yes")
    if args.dry_run:
        if not args.output_json:
            print("dry run: no processed table or manifest written")
        return 0

    table_path = processed.write(args.out, attachment.frame)
    manifest_path = table_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(
            {**manifest, "processed_table": table_path.name},
            indent=2,
            sort_keys=True,
            default=str,
        ) + "\n",
        encoding="utf-8",
    )
    if not args.output_json:
        print(f"wrote {table_path}")
        print(f"wrote {manifest_path}")
    return 0


def backtest_futures(args: argparse.Namespace, processed: ProcessedStore) -> int:
    import pandas as pd

    from .futures_data import FuturesDataError, audit_contract_panel, read_export, render_audit
    from .futures_execution import ExecutionError, load_execution_config, simulate_spread_trades

    contract_path = Path(args.contracts)
    signal_path = Path(args.signals)
    config_path = Path(args.execution_config)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parents[2] / config_path
    try:
        contracts = read_export(contract_path)
        report = audit_contract_panel(contracts)
        if not report.ready_for_execution:
            print(render_audit(report), file=sys.stderr)
            print("futures backtest error: contract data is not execution-ready", file=sys.stderr)
            return 1
        signals = _load_signal_decisions(signal_path)
        config = load_execution_config(config_path)
        ledger = simulate_spread_trades(
            signals, contracts, config=config, capital_usd=args.capital)
    except (FuturesDataError, ExecutionError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"futures backtest error: {exc}", file=sys.stderr)
        return 1

    executed = int((ledger.get("status") == "EXECUTED").sum()) if len(ledger) else 0
    net = float(ledger.get("net_pnl_usd", pd.Series(dtype=float)).sum()) if len(ledger) else 0.0
    if args.output_json:
        print(ledger.to_json(orient="records", date_format="iso", indent=2))
    else:
        print(f"futures ledger: {len(ledger)} decisions, {executed} executed, net ${net:,.2f}")
        if "status" in ledger:
            for status, count in ledger["status"].value_counts().items():
                print(f"  {status:22s} {count:5d}")
    if args.dry_run:
        if not args.output_json:
            print("dry run: no ledger or manifest written")
        return 0

    ledger_path = processed.write(args.out, ledger)
    manifest_path = ledger_path.with_suffix(".manifest.json")
    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "contracts_file": contract_path.name,
        "contracts_sha256": _file_sha256(contract_path),
        "signals_file": signal_path.name,
        "signals_sha256": _file_sha256(signal_path),
        "execution_config": config_path.name,
        "execution_config_sha256": _file_sha256(config_path),
        "execution_assumptions": asdict(config.assumptions),
        "decisions": len(ledger),
        "executed": executed,
        "net_pnl_usd": net,
        "ledger": ledger_path.name,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if not args.output_json:
        print(f"wrote {ledger_path}")
        print(f"wrote {manifest_path}")
    return 0


def register_paper_signal(args: argparse.Namespace, raw: RawStore) -> int:
    """Admit a saved signal to the prospective ledger before entry can occur."""
    from .futures_data import FuturesDataError
    from .paper_trading import PaperTradingError, register_signal_file

    try:
        record, path = register_signal_file(
            args.signal_record,
            args.contracts,
            data_root=raw.root,
            execution_config_path=args.execution_config,
            dry_run=args.dry_run,
        )
    except (FuturesDataError, PaperTradingError, OSError) as exc:
        print(f"paper registration error: {exc}", file=sys.stderr)
        return 1
    if args.output_json:
        print(json.dumps(record, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(f"paper decision {record['paper_id']}: {record['status']}")
        print(f"  decision:       {record['decision']['date']} {record['decision']['action']}")
        print(
            "  evidence:       "
            + ("prospective" if record["eligible_for_prospective_evidence"] else "ineligible")
        )
        print("  execution:      disabled; zero orders submitted")
        if args.dry_run:
            print("  dry run: decision record not written")
        elif path:
            print(f"  record:         {path}")
    return 0


def resolve_paper_signals(args: argparse.Namespace, raw: RawStore) -> int:
    """Append immutable outcomes once five post-entry sessions are observable."""
    from .futures_data import FuturesDataError
    from .paper_trading import PaperTradingError, resolve_registered_signals

    try:
        outcomes, pending = resolve_registered_signals(
            args.contracts,
            data_root=raw.root,
            execution_config_path=args.execution_config,
            dry_run=args.dry_run,
        )
    except (FuturesDataError, PaperTradingError, OSError) as exc:
        print(f"paper resolution error: {exc}", file=sys.stderr)
        return 1
    if args.output_json:
        print(json.dumps(
            {"outcomes": outcomes, "pending": pending, "dry_run": args.dry_run},
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ))
    else:
        verb = "would append" if args.dry_run else "appended"
        print(f"paper outcomes: {verb} {len(outcomes)}; {pending} pending")
        for outcome in outcomes:
            execution = outcome["execution"]
            net = execution.get("execution_net_return")
            rendered = "" if net is None else f", net {float(net):+.2%}"
            print(
                f"  {outcome['paper_id']} {execution['execution_status']}{rendered}"
            )
        print("execution disabled; zero orders submitted")
    return 0


def show_paper_status(args: argparse.Namespace, raw: RawStore) -> int:
    """Report prospective evidence without recalculating or mutating it."""
    from .paper_trading import PaperTradingError, paper_status

    try:
        report = paper_status(raw.root)
    except (PaperTradingError, OSError) as exc:
        print(f"paper status error: {exc}", file=sys.stderr)
        return 1
    if args.output_json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(
            f"paper ledger: {report['decisions']} decisions, "
            f"{report['outcomes']} outcomes, "
            f"{report['pending_actionable']} actionable pending"
        )
        print(f"  eligible decisions: {report['eligible_decisions']}")
        for status, count in report["decision_statuses"].items():
            print(f"  decision {status:22s} {count:5d}")
        for status, count in report["outcome_statuses"].items():
            print(f"  outcome  {status:22s} {count:5d}")
        print(
            "  aggregate net:     "
            f"{report['aggregate_net_on_average_leg_notional']:+.2%} "
            "on average leg notional"
        )
        print("  orders submitted:  0")
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
        if args.command == "signal":
            return produce_signal(args, geography, raw, processed)
        if args.command == "futures-workspace-download":
            return download_workspace_futures(args, raw)
        if args.command == "futures-import":
            return import_futures(args, processed)
        if args.command == "futures-audit":
            return audit_futures(args)
        if args.command == "futures-margin-enrich":
            return enrich_futures_margins(args, processed)
        if args.command == "futures-backtest":
            return backtest_futures(args, processed)
        if args.command == "paper-register":
            return register_paper_signal(args, raw)
        if args.command == "paper-resolve":
            return resolve_paper_signals(args, raw)
        if args.command == "paper-status":
            return show_paper_status(args, raw)
        return status(args, sources, raw, processed)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # `python -m pipeline.cli` also works
    raise SystemExit(main())
