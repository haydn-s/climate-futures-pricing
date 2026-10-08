#!/usr/bin/env python3
"""Rebuild the current project from public inputs, one resumable stage at a time.

The default runs the complete keyless workflow: fetch, clean, build the canonical
corn panel, execute every current analysis, and run the offline test suite. Raw
fetches are content-addressed and skipped when already archived, so rerunning the
command resumes rather than replacing prior data.

Examples:

    python reproduce.py
    python reproduce.py --stages analysis test
    python reproduce.py --stages fetch clean --refresh
    python reproduce.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent

STAGE_ORDER = ("fetch", "clean", "features", "analysis", "test")
KEYLESS_SOURCES = (
    "owid_yields",
    "yahoo_prices",
    "nasa_power",
    "usdm",
    "nclimgrid",
    "cpc_outlook",
)
ANALYSES = (
    "analysis/corn_yield_model.py",
    "analysis/corn_peek_sweep.py",
    "analysis/corn_weekly_model.py",
    "analysis/corn_event_study.py",
    "analysis/corn_event_attribution.py",
    "analysis/cross_crop_events.py",
    "analysis/seasonal_timing.py",
    "analysis/forecast_verification.py",
    "analysis/forecast_peek_sweep.py",
    "analysis/cocoa_weather.py",
    "analysis/coffee_weather.py",
)


@dataclass(frozen=True)
class Step:
    stage: str
    label: str
    command: tuple[str, ...]


def build_plan(stages: Sequence[str], *, refresh: bool = False,
               include_nass: bool = False) -> list[Step]:
    """Return the deterministic command plan for the requested stages."""
    requested = set(stages)
    plan: list[Step] = []

    sources = (*KEYLESS_SOURCES, "nass_production") if include_nass else KEYLESS_SOURCES
    if "fetch" in requested:
        for source in sources:
            command = [sys.executable, "-m", "pipeline", "fetch", "--source", source]
            if refresh:
                command.append("--force")
            plan.append(Step("fetch", f"fetch {source}", tuple(command)))

    if "clean" in requested:
        for source in sources:
            plan.append(Step(
                "clean", f"clean {source}",
                (sys.executable, "-m", "pipeline", "clean", "--source", source),
            ))

    if "features" in requested:
        plan.append(Step(
            "features", "build canonical corn feature panel",
            (sys.executable, "-m", "pipeline", "features", "--crop", "corn",
             "--peek", "0", "--horizons", "5", "10", "--growing-season"),
        ))

    if "analysis" in requested:
        for relative in ANALYSES:
            plan.append(Step(
                "analysis", relative,
                (sys.executable, str(ROOT / relative)),
            ))

    if "test" in requested:
        plan.append(Step(
            "test", "offline test suite",
            (sys.executable, "-m", "pytest", "tests", "-q"),
        ))
    return plan


def run(plan: Sequence[Step], *, dry_run: bool = False) -> None:
    env = os.environ.copy()
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"]
                               if env.get("PYTHONPATH") else "")

    for index, step in enumerate(plan, start=1):
        rendered = shlex.join(step.command)
        print(f"\n[{index}/{len(plan)}] {step.label}", flush=True)
        print(f"  {rendered}", flush=True)
        if dry_run:
            continue
        started = time.monotonic()
        subprocess.run(step.command, cwd=ROOT, env=env, check=True)
        print(f"  completed in {time.monotonic() - started:.1f}s", flush=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument(
        "--stages", nargs="+", choices=STAGE_ORDER, default=list(STAGE_ORDER),
        help="stages to run (default: the complete workflow)",
    )
    result.add_argument(
        "--refresh", action="store_true",
        help="force network sources to be fetched again; old versions remain archived",
    )
    result.add_argument(
        "--include-nass", action="store_true",
        help="also fetch and clean USDA NASS; requires NASS_API_KEY and is not needed "
             "for the currently reported results",
    )
    result.add_argument(
        "--dry-run", action="store_true",
        help="print the commands without running them or contacting a source",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    stages = tuple(stage for stage in STAGE_ORDER if stage in args.stages)
    plan = build_plan(stages, refresh=args.refresh, include_nass=args.include_nass)
    if "fetch" in stages and not args.dry_run:
        print("The full archive is roughly 5 GB and the CPC backfill contains more than "
              "12,000 files. Existing raw keys are skipped; use --refresh only when a "
              "new vintage is intentional.", flush=True)
    try:
        run(plan, dry_run=args.dry_run)
    except subprocess.CalledProcessError as exc:
        print(f"\nReproduction stopped after a failed command (exit {exc.returncode}). "
              "Fix the reported problem and rerun; completed fetches are retained.",
              file=sys.stderr)
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
