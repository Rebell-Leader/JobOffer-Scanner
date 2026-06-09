"""Telegram bot entry point.

Run with ``python -m bot.main`` (after setting ``TELEGRAM_BOT_TOKEN``).
The handlers live in ``bot.handlers`` and are designed to be testable without
the Telegram runtime.
"""

from __future__ import annotations

import logging
import os
import sys

from bot.handlers import (
    handle_analyze,
    handle_bind,
    handle_help,
    handle_me,
    handle_start,
    handle_unbind,
)

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

    from utils.logging_setup import configure as configure_logging
    configure_logging()

    def _reply_for(message):
        """Adapt Telegram's ``reply_markdown`` (returns a Message) to the
        handlers' ``Reply = Callable[[str], Awaitable[None]]`` contract."""
        async def _reply(text: str) -> None:
            await message.reply_markdown(text)
        return _reply

    async def _start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        await handle_start(_reply_for(update.message), _args="")

    async def _help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        await handle_help(_reply_for(update.message), _args="")

    async def _analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # We want the original text after `/analyze ` to preserve newlines, so
        # use message.text and strip the command + first space.
        if update.message is None:
            return
        raw = update.message.text or ""
        _, _, args = raw.partition(" ")
        await handle_analyze(_reply_for(update.message), args=args)

    async def _bind(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_chat is None:
            return
        raw = update.message.text or ""
        _, _, args = raw.partition(" ")
        chat = update.effective_chat
        await handle_bind(
            _reply_for(update.message),
            args=args,
            chat_id=chat.id,
            chat_username=chat.username,
        )

    async def _unbind(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_chat is None:
            return
        await handle_unbind(
            _reply_for(update.message),
            args="",
            chat_id=update.effective_chat.id,
        )

    async def _me(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_chat is None:
            return
        await handle_me(
            _reply_for(update.message),
            args="",
            chat_id=update.effective_chat.id,
        )

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("help", _help))
    app.add_handler(CommandHandler("analyze", _analyze))
    app.add_handler(CommandHandler("bind", _bind))
    app.add_handler(CommandHandler("unbind", _unbind))
    app.add_handler(CommandHandler("me", _me))

    logger.info("Telegram bot starting — long-poll mode.")
    app.run_polling()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
