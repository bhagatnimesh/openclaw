from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
import logging
import os
from pathlib import Path
import time
from typing import Any

from dotenv import dotenv_values, load_dotenv

try:
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
except ModuleNotFoundError as error:  # pragma: no cover - exercised by startup.
    Update = Any  # type: ignore[assignment]
    Application = None  # type: ignore[assignment]
    CommandHandler = None  # type: ignore[assignment]
    ContextTypes = Any  # type: ignore[assignment]
    MessageHandler = None  # type: ignore[assignment]
    filters = None  # type: ignore[assignment]
    TELEGRAM_IMPORT_ERROR: ModuleNotFoundError | None = error
else:
    TELEGRAM_IMPORT_ERROR = None

from claws.n4os.claw import N4OSClaw


LOGGER = logging.getLogger("n4os.telegram")
SETUP_USER_MESSAGE = (
    "Your Telegram user id is: {user_id}. "
    "Add ALLOWED_TELEGRAM_USER_ID={user_id} to .env and restart."
)
UNAUTHORIZED_MESSAGE = "Unauthorized."
HELP_MESSAGE = (
    "N4OS is ready. Send a calendar request, task request, Home Board notice, or day briefing."
)
ERROR_MESSAGE = "Sorry, N4OS hit an error while handling that."


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    allowed_user_id: int | None


@dataclass(frozen=True)
class RouterResult:
    response: str
    route: str
    elapsed_ms: float


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def load_config(env_path: str | Path = ".env") -> TelegramConfig:
    env_values = dotenv_values(env_path)
    load_dotenv(env_path)

    token = _env_value(env_values, "TELEGRAM_BOT_TOKEN") or os.getenv(
        "TELEGRAM_BOT_TOKEN",
        "",
    ).strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing from .env.")

    raw_allowed_user_id = _env_value(env_values, "ALLOWED_TELEGRAM_USER_ID")
    if not raw_allowed_user_id:
        return TelegramConfig(token=token, allowed_user_id=None)

    try:
        allowed_user_id = int(raw_allowed_user_id)
    except ValueError as error:
        raise RuntimeError("ALLOWED_TELEGRAM_USER_ID must be an integer.") from error

    return TelegramConfig(token=token, allowed_user_id=allowed_user_id)


def _env_value(env_values: dict[str, str | None], key: str) -> str:
    value = env_values.get(key)
    return value.strip() if isinstance(value, str) else ""


class N4OSTelegramBot:
    def __init__(
        self,
        config: TelegramConfig,
        claw: N4OSClaw | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.claw = claw or N4OSClaw()
        self.logger = logger or LOGGER

    def route_message(self, text: str) -> RouterResult:
        started = time.perf_counter()
        output = StringIO()
        # Existing N4OS claws print their user-facing messages; keep the
        # Telegram transport thin by capturing that router output verbatim.
        with redirect_stdout(output):
            decision = self.claw.handle_request(text) or {}

        elapsed_ms = (time.perf_counter() - started) * 1000
        route = str(decision.get("route", "unknown"))
        response = output.getvalue().strip()
        if not response:
            response = str(decision.get("intent_summary") or "Done.")

        return RouterResult(response=response, route=route, elapsed_ms=elapsed_ms)

    def _authorization_reply(self, user_id: int | None) -> str | None:
        if user_id is None:
            return UNAUTHORIZED_MESSAGE
        if self.config.allowed_user_id is None:
            return SETUP_USER_MESSAGE.format(user_id=user_id)
        if user_id != self.config.allowed_user_id:
            return UNAUTHORIZED_MESSAGE
        return None

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        message = update.effective_message
        if message is None:
            return

        user_id = _effective_user_id(update)
        text = (getattr(message, "text", None) or "").strip()
        self.logger.info("incoming message user_id=%s text=%r", user_id, text)

        auth_reply = self._authorization_reply(user_id)
        if auth_reply is not None:
            await message.reply_text(auth_reply)
            self.logger.info("chosen route=unauthorized execution_ms=0.00")
            return

        self.logger.info("chosen route=help execution_ms=0.00")
        await message.reply_text(HELP_MESSAGE)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        message = update.effective_message
        if message is None:
            return

        user_id = _effective_user_id(update)
        text = (
            getattr(message, "text", None)
            or getattr(message, "caption", None)
            or ""
        ).strip()
        self.logger.info("incoming message user_id=%s text=%r", user_id, text)

        auth_reply = self._authorization_reply(user_id)
        if auth_reply is not None:
            await message.reply_text(auth_reply)
            self.logger.info("chosen route=unauthorized execution_ms=0.00")
            return

        if not text:
            await message.reply_text("Please send a text message.")
            self.logger.info("chosen route=unsupported execution_ms=0.00")
            return

        started = time.perf_counter()
        try:
            result = self.route_message(text)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.exception(
                "error while routing Telegram message execution_ms=%.2f",
                elapsed_ms,
            )
            await message.reply_text(ERROR_MESSAGE)
            return

        self.logger.info(
            "chosen route=%s execution_ms=%.2f",
            result.route,
            result.elapsed_ms,
        )
        await message.reply_text(result.response)


def _effective_user_id(update: Update) -> int | None:
    user = getattr(update, "effective_user", None)
    user_id = getattr(user, "id", None)
    return user_id if isinstance(user_id, int) else None


def build_application(config: TelegramConfig) -> Any:
    if TELEGRAM_IMPORT_ERROR is not None or Application is None:
        raise RuntimeError(
            "python-telegram-bot is not installed in this Python environment."
        ) from TELEGRAM_IMPORT_ERROR

    bot = N4OSTelegramBot(config)
    application = Application.builder().token(config.token).build()
    application.add_handler(CommandHandler("start", bot.handle_help))
    application.add_handler(CommandHandler("help", bot.handle_help))
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, bot.handle_message),
    )
    return application


def main() -> None:
    configure_logging()
    config = load_config()
    LOGGER.info("starting N4OS Telegram bot with long polling")
    build_application(config).run_polling()


if __name__ == "__main__":
    main()
