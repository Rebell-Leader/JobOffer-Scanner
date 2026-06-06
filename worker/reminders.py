"""CLI: scan all users and send any inactivity reminders.

Run from cron / systemd-timer / k8s CronJob::

    python -m worker.reminders            # all users
    python -m worker.reminders --user 42  # single user (debugging)

Exit code is 0 even if no reminders went out; we only fail on configuration
errors (missing DB, missing TELEGRAM_BOT_TOKEN). That keeps the scheduler
happy on quiet days.
"""

from __future__ import annotations

import argparse
import logging
import sys

from db.session import init_db
from services.reminders import send_inactivity_reminders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send inactivity reminders for stale applications.",
    )
    parser.add_argument(
        "--user", type=int, default=None,
        help="If set, only run for this user_id (useful for debugging).",
    )
    args = parser.parse_args(argv)

    from utils.logging_setup import configure as configure_logging
    configure_logging()
    init_db()
    sent = send_inactivity_reminders(user_id=args.user)
    logging.info("Reminder run complete: %d notifications sent.", sent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
