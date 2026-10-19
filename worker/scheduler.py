"""Long-running process: fire reminders + DB backups on a fixed interval.

``worker/reminders.py`` and ``scripts/backup_db.sh`` both do real work when
invoked, but neither invokes itself — they're designed to be hung off an
external cron/Scheduled Deployment (see ``deploy/RUNBOOK.md``). The Docker
Compose stack has no host crontab to hang those off of, so this gives it an
in-process equivalent: run as the ``scheduler`` service and it calls both on
a timer, forever. Replit deploys should still prefer a native Scheduled
Deployment invoking those commands directly, as documented in the runbook.

    python -m worker.scheduler

Env:
    REMINDER_INTERVAL_HOURS   default 24
    BACKUP_INTERVAL_HOURS     default 24
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from db.session import init_db
from services.reminders import send_inactivity_reminders
from utils.env import env_float

logger = logging.getLogger(__name__)

_REMINDER_INTERVAL_S = env_float("REMINDER_INTERVAL_HOURS", 24.0) * 3600
_BACKUP_INTERVAL_S = env_float("BACKUP_INTERVAL_HOURS", 24.0) * 3600
_BACKUP_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup_db.sh"


def _run_reminders() -> None:
    try:
        sent = send_inactivity_reminders()
        logger.info("Scheduled reminder run complete: %d notifications sent.", sent)
    except Exception:  # noqa: BLE001 - a bad run must not kill the scheduler
        logger.exception("Scheduled reminder run failed.")


def _run_backup() -> None:
    try:
        subprocess.run(["bash", str(_BACKUP_SCRIPT)], check=True)
    except Exception:  # noqa: BLE001 - a bad run must not kill the scheduler
        logger.exception("Scheduled backup run failed.")


def main() -> int:
    from utils.logging_setup import configure as configure_logging

    configure_logging()
    init_db()

    logger.info(
        "Scheduler starting: reminders every %.1fh, backups every %.1fh.",
        _REMINDER_INTERVAL_S / 3600,
        _BACKUP_INTERVAL_S / 3600,
    )
    next_reminder = time.monotonic() + _REMINDER_INTERVAL_S
    next_backup = time.monotonic() + _BACKUP_INTERVAL_S
    poll = min(60.0, _REMINDER_INTERVAL_S, _BACKUP_INTERVAL_S)

    while True:
        now = time.monotonic()
        if now >= next_reminder:
            _run_reminders()
            next_reminder = now + _REMINDER_INTERVAL_S
        if now >= next_backup:
            _run_backup()
            next_backup = now + _BACKUP_INTERVAL_S
        time.sleep(poll)


if __name__ == "__main__":
    raise SystemExit(main())
