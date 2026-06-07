"""CLI: dump or ship the current in-process metrics snapshot.

The metrics registry is in-memory and per-process, so this only sees what's
been recorded in *this* container. Use it for ad-hoc inspection, or schedule
``--push`` to a Prometheus Pushgateway as a stop-gap until a scraper hits the
API's ``/metrics`` endpoint directly.

    python -m worker.metrics_dump                 # human-readable text
    python -m worker.metrics_dump --json          # JSON for piping to jq
    python -m worker.metrics_dump --prometheus    # Prometheus exposition text
    python -m worker.metrics_dump --push          # POST to a Pushgateway
                                                  # (METRICS_PUSHGATEWAY_URL)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict

from utils.metrics import render_prometheus, render_snapshot_text, snapshot

logger = logging.getLogger(__name__)


def push_to_gateway(text: str, url: str, job: str) -> bool:
    """POST Prometheus text to a Pushgateway. Best-effort; returns success."""
    import requests

    endpoint = f"{url.rstrip('/')}/metrics/job/{job}"
    try:
        resp = requests.post(
            endpoint, data=text.encode("utf-8"),
            headers={"Content-Type": "text/plain"}, timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 - shipping must not crash the cron
        logger.warning("Pushgateway push to %s failed: %s", endpoint, exc)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dump or ship the metrics snapshot.")
    parser.add_argument(
        "--json", action="store_true",
        help="Emit a JSON-serialisable dict instead of the human-readable view.",
    )
    parser.add_argument(
        "--prometheus", action="store_true",
        help="Emit Prometheus text exposition format.",
    )
    parser.add_argument(
        "--push", action="store_true",
        help="POST the Prometheus text to METRICS_PUSHGATEWAY_URL.",
    )
    args = parser.parse_args(argv)

    snap = snapshot()

    if args.push:
        url = os.getenv("METRICS_PUSHGATEWAY_URL")
        if not url:
            print("METRICS_PUSHGATEWAY_URL is not set.", file=sys.stderr)
            return 2
        job = os.getenv("METRICS_JOB", "joboffer_scanner")
        ok = push_to_gateway(render_prometheus(snap), url, job)
        print("pushed" if ok else "push failed", file=sys.stderr)
        return 0 if ok else 1

    if args.prometheus:
        print(render_prometheus(snap), end="")
    elif args.json:
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
