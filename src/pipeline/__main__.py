"""Entry point for `python -m pipeline`; the commands live in pipeline.cli."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
