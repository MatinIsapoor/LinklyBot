"""
``main`` - application entry point.

Starts two things concurrently:

1. the Telegram bot, and
2. a tiny ``aiohttp`` server that serves the Telegram webhook and turns
   short codes into ``302`` redirects while counting clicks::

       GET /{short_code} -> 302 -> original_url (+1 click)
       GET /              -> health page
       GET /healthz       -> bare health probe
       POST /telegram/webhook -> aiogram update feed (webhook mode)

The bot uses **polling** by default (local dev). When ``USE_WEBHOOK=true``
or a Render environment is detected (``RENDER_EXTERNAL_URL``), it registers
a webhook with Telegram instead.

Run with::

    python main.py
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import signal
from typing import Awaitable, Callable

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, Update
from aiohttp import web

from bot import build_dispatcher
from config import config
from database.database import Database
from services.link_service import LinkService

logger = logging.getLogger(__name__)

# Redirect web server bind address (separate from BASE_URL, which is the
# *public* URL printed to users, e.g. https://mydomain.com).
WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT: int = int(os.getenv("PORT", os.getenv("WEB_PORT", "8080")))

# Short codes are generated as ``[A-Za-z0-9]{4,16}`` - accept up to 32
# chars here so a future length bump never 404s, but reject anything
# else (path traversal, emojis, overlong input) without touching the DB.
_SHORT_CODE_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")

# Telegram requires webhook secret tokens to match ``[A-Za-z0-9_-]{1,256}``.
_WEBHOOK_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


def get_webhook_secret() -> str:
    """
    Return a Telegram-valid webhook secret token.

    Uses ``WEBHOOK_SECRET`` when set and valid, otherwise derives a stable
    one from the bot token (sha256 hex is always accepted by Telegram).
    """
    secret = config.WEBHOOK_SECRET
    if secret and _WEBHOOK_SECRET_RE.match(secret):
        return secret
    if secret:
        logger.warning("WEBHOOK_SECRET is invalid; deriving one from BOT_TOKEN.")
    return hashlib.sha256(config.BOT_TOKEN.encode("utf-8")).hexdigest()


def validate_webhook_config() -> None:
    """Fail fast when webhook mode is enabled without a public URL."""
    url = config.BASE_URL
    if not url.startswith(("http://", "https://")) or url == "https://example.com":
        raise RuntimeError(
            "USE_WEBHOOK is enabled but BASE_URL is not set. "
            "Set BASE_URL to your public HTTPS URL (e.g. the Render URL)."
        )


async def handle_redirect(request: web.Request) -> web.Response:
    """
    ``GET /{short_code}`` - count the click and redirect.

    Status contract (bodies are static - the code is never echoed back):

    * ``302`` - active link, redirect to the original URL;
    * ``404`` - malformed or unknown code;
    * ``410`` - link exists but has expired (gone for good);
    * ``403`` - link exists but is disabled by its owner;
    * ``500`` - stored target URL is invalid (should never happen);
    * ``503`` - database failure (transient).
    """
    service: LinkService = request.app["link_service"]
    code: str = request.match_info.get("short_code", "").strip()

    if not code:
        return web.Response(text="لینک‌منیجر تلگرام در حال اجراست. 🤖", status=200)

    if not _SHORT_CODE_RE.match(code):
        return web.Response(text="لینک پیدا نشد. ❌", status=404)

    try:
        # Peek first to distinguish expired/disabled from unknown without
        # counting an extra click: resolve_and_track() already refuses to
        # count expired/disabled links, so a second cheap lookup is safe.
        link = await service.resolve_and_track(code)
    except Exception:
        logger.warning("Redirect failed for code %r: DB error", code)
        return web.Response(
            text="سرویس موقتا در دسترس نیست. لطفا بعدا دوباره تلاش کن.",
            status=503,
        )
    if link is not None:
        target = (link.original_url or "").strip()
        lowered = target.lower()
        if not (
            lowered.startswith("http://") or lowered.startswith("https://")
        ):
            # Defensive: service validates URLs on write, so this means
            # the row was tampered with outside the bot - do not redirect.
            logger.warning("Redirect refused for code %r: bad target URL", code)
            return web.Response(text="این لینک نامعتبره.", status=500)
        # 302 Found -> original URL. Clicks already incremented inside
        # resolve_and_track().
        raise web.HTTPFound(target)

    # Not resolved: check why (for a precise status code).
    try:
        existing = await service.get_by_code(code)
    except Exception:
        logger.warning("Redirect lookup failed for code %r: DB error", code)
        return web.Response(
            text="سرویس موقتا در دسترس نیست. لطفا بعدا دوباره تلاش کن.",
            status=503,
        )
    if existing is not None and existing.is_expired:
        return web.Response(text="این لینک منقضی شده. ⏰", status=410)
    if existing is not None and not existing.is_active:
        return web.Response(text="این لینک غیرفعاله. 🚫", status=403)
    return web.Response(text="لینک پیدا نشد. ❌", status=404)


async def handle_index(_: web.Request) -> web.Response:
    """``GET /`` - simple health page."""
    return web.Response(
        text="سرویس ریدایرکت لینک‌منیجر تلگرام در حال اجراست. 🤖",
        status=200,
    )


async def handle_health(_: web.Request) -> web.Response:
    """``GET /healthz`` - tiny response for uptime probes / keep-alive."""
    return web.Response(text="ok", status=200)


def make_webhook_handler(
    bot: Bot, dispatcher: Dispatcher
) -> Callable[[web.Request], Awaitable[web.Response]]:
    """Build the Telegram webhook endpoint (validates the secret header)."""
    secret = get_webhook_secret()

    async def handle_webhook(request: web.Request) -> web.Response:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if token != secret:
            return web.Response(status=401)
        try:
            payload = await request.json()
            update = Update.model_validate(payload, context={"bot": bot})
            await dispatcher.feed_update(bot, update)
        except Exception:
            logger.exception("Failed to process webhook update")
            return web.Response(status=500)
        return web.Response(text="ok")

    return handle_webhook


def build_web_app(
    service: LinkService,
    *,
    bot: Bot | None = None,
    dispatcher: Dispatcher | None = None,
) -> web.Application:
    """Create the aiohttp app with redirect + optional webhook routes."""
    app = web.Application()
    app["link_service"] = service
    app.router.add_get("/", handle_index)
    app.router.add_get("/healthz", handle_health)
    if bot is not None and dispatcher is not None:
        app.router.add_post(config.WEBHOOK_PATH, make_webhook_handler(bot, dispatcher))
    app.router.add_get("/{short_code}", handle_redirect)
    return app


async def start_webhook(bot: Bot, dp: Dispatcher) -> None:
    """Register the public webhook URL with Telegram."""
    url = f"{config.BASE_URL}{config.WEBHOOK_PATH}"
    await bot.set_webhook(
        url=url,
        secret_token=get_webhook_secret(),
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )
    logger.info("Webhook registered: %s", url)


async def wait_for_shutdown() -> None:
    """Block until SIGINT/SIGTERM (Render sends SIGTERM on redeploy)."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows / unsupported loop - KeyboardInterrupt still works
    await stop.wait()



async def setup_bot_commands(bot: Bot) -> None:
    """Register the user-visible command list (best-effort)."""
    commands = [
        BotCommand(command="start", description="شروع ربات / منوی اصلی"),
        BotCommand(command="new", description="ساخت لینک کوتاه جدید"),
        BotCommand(command="links", description="دیدن و مدیریت لینک‌ها"),
        BotCommand(command="stats", description="دیدن آمار"),
        BotCommand(command="cancel", description="لغو عملیات جاری"),
        BotCommand(command="help", description="نمایش راهنما"),
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception as exc:  # never crash startup over this
        logger.warning("Could not set bot commands: %s", exc)


async def main() -> None:
    """Wire everything together and run the bot + redirect server."""
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    if not config.BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
        )

    if os.getenv("RENDER") and config.DATABASE_URL.startswith("sqlite"):
        logger.warning(
            "SQLite is ephemeral on Render - data is lost on redeploy, restart "
            "or spin-down. Set DATABASE_URL to a Postgres connection string."
        )

    if config.USE_WEBHOOK:
        validate_webhook_config()
        logger.info("Webhook mode enabled (BASE_URL=%s)", config.BASE_URL)

    # -- database + service -----------------------------------------
    db = Database(config.DATABASE_URL)
    await db.init()
    service = LinkService(db, code_length=config.SHORT_CODE_LENGTH)
    # Driver only - never log the URL itself (it may contain credentials).
    logger.info("Database ready (%s)", config.DATABASE_URL.split("://", 1)[0])

    # -- telegram bot ------------------------------------------------
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher(db, service)
    await setup_bot_commands(bot)

    # -- web server (redirects + webhook) -----------------------------
    if config.USE_WEBHOOK:
        app = build_web_app(service, bot=bot, dispatcher=dp)
    else:
        app = build_web_app(service)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_HOST, WEB_PORT)
    try:
        await site.start()
        logger.info("Web server listening on %s:%s", WEB_HOST, WEB_PORT)
    except OSError as exc:
        # Port already in use etc. - in polling mode the bot still works,
        # short links just need an externally hosted redirect.
        if config.USE_WEBHOOK:
            raise RuntimeError(f"Webhook mode requires the web server: {exc}") from exc
        logger.warning("Could not start redirect server (%s). Bot continues.", exc)
        runner = None  # type: ignore[assignment]


    try:
        if config.USE_WEBHOOK:
            await start_webhook(bot, dp)
            logger.info("Bot running in webhook mode. Base URL: %s", config.BASE_URL)
            await wait_for_shutdown()
        else:
            logger.info("Bot starting (polling)... Base URL: %s", config.BASE_URL)
            # Skip stale updates accumulated while offline.
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
    finally:
        if runner is not None:
            await runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down.")
