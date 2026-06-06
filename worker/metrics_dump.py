"""CLI: dump the current in-process metrics snapshot.

Run from the same process that's serving traffic (the metrics registry is
in-memory and per-process, so this only sees what's been recorded in *this*
container)::

    python -m worker.metrics_dump            # text format
    python -m worker.metrics_dump --json     # JSON for piping to jq

Useful for ad-hoc inspection during development and for scheduling a
periodic dump into your log pipeline until you have a real metrics backend.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from utils.metrics import render_snapshot_text, snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dump the current metrics snapshot.")
    parser.add_argument(
        "--json", action="store_true",
        help="Emit a JSON-serialisable dict instead of the human-readable view.",
    )
    args = parser.parse_args(argv)

    snap = snapshot()
    if args.json:
        payload = {
            "counters": [asdict(c) for c in snap.counters],
            "histograms": [asdict(h) for h in snap.histograms],
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_snapshot_text(snap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
