"""Telegram bot entry point.

Run with ``python -m bot.main`` (after setting ``TELEGRAM_BOT_TOKEN``).
The handlers live in ``bot.handlers`` and are designed to be testable without
the Telegram runtime.
"""

from __future__ import annotations

import logging
import os
import sys

from bot.handlers import handle_analyze, handle_help, handle_start

logger = logging.getLogger(__name__)


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print(
            "TELEGRAM_BOT_TOKEN is not set. Get one from @BotFather and "
            "export it, then re-run `python -m bot.main`.",
            file=sys.stderr,
        )
        return 1

    try:
        # Lazy import — keeps the rest of the project importable in
        # environments without python-telegram-bot installed.
        from telegram import Update
        from telegram.ext import (
            ApplicationBuilder,
            CommandHandler,
            ContextTypes,
        )
    except ImportError:
        print(
            "python-telegram-bot is not installed. Add it to your environment "
            "(`pip install 'python-telegram-bot>=21'`) and re-run.",
            file=sys.stderr,
        )
        return 1

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )

    async def _start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await handle_start(update.message.reply_markdown, _args="")

    async def _help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await handle_help(update.message.reply_markdown, _args="")

    async def _analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # ctx.args is everything after the command, split on spaces; we want
        # the original text after `/analyze ` to preserve newlines, so use
        # message.text and strip the command + first space.
        raw = (update.message.text or "")
        _, _, args = raw.partition(" ")
        await handle_analyze(update.message.reply_markdown, args=args)

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("help", _help))
    app.add_handler(CommandHandler("analyze", _analyze))

    logger.info("Telegram bot starting — long-poll mode.")
    app.run_polling()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
