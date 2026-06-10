"""CLI: print the operator funnel/cohort report (the founder dashboard).

    python -m worker.funnel_report                 # human-readable, last 30 days
    python -m worker.funnel_report --days 7        # weekly
    python -m worker.funnel_report --json          # for piping / dashboards

Cron-friendly: schedule weekly and pipe to email/Slack to watch the validation
funnel without any third-party product-analytics tool. Reads only.
"""

from __future__ import annotations

import argparse
import json
import sys

from services.funnel import compute_funnel, render_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operator funnel/cohort report.")
    parser.add_argument("--days", type=int, default=30, help="Rolling window (days).")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args(argv)

    report = compute_funnel(window_days=args.days)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
