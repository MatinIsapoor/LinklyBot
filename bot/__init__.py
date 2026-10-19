"""
``bot`` package - Telegram user-facing layer of the application.

The public entry point is :func:`build_dispatcher`, which assembles a
fully configured aiogram ``Dispatcher``:

* ``MemoryStorage`` FSM (single-instance, private-chat-only bot),
* ``AccessMiddleware`` (inner) that resolves/creates the DB user and
  injects ``db_user`` + ``link_service`` into every handler,
* feature routers (start, shorten, manage, stats) restricted to private
  chats via the ``IsPrivate`` filter.

The ``IsPrivate`` filter is registered on the included routers so it is
evaluated *before* the middleware chain - public chats are dropped
before any database access happens.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.filters.private import IsPrivate
from bot.handlers import manage, shorten, start, stats
from bot.middlewares.access import AccessMiddleware

if TYPE_CHECKING:
    from database.database import Database
    from services.link_service import LinkService

__all__ = ["build_dispatcher"]


def build_dispatcher(db: "Database", service: "LinkService") -> Dispatcher:
    """
    Assemble and return the aiogram ``Dispatcher``.

    Args:
        db: Database facade exposing ``sessions`` (an async_sessionmaker);
            consumed by :class:`AccessMiddleware` to resolve users.
        service: ``LinkService`` instance shared with all handlers via
            dependency injection (``link_service`` key).

    Returns:
        Configured ``Dispatcher`` (the caller owns the ``Bot`` and starts
        polling/webhook itself).
    """
    # FSM kept in process memory - acceptable for a single-instance bot.
    dp = Dispatcher(storage=MemoryStorage())

    # ---- Access control (inner middleware) ------------------------------
    # Inner middlewares run AFTER filters, so IsPrivate already rejected
    # group/channel updates before any DB work is done here.
    access = AccessMiddleware(db, service)
    dp.message.middleware(access)
    dp.callback_query.middleware(access)

    # ---- Routers ---------------------------------------------------------
    # Registration order matters: start first (its /start and /cancel
    # clear FSM state), then shorten, then manage, then stats.
    dp.include_router(start.router)
    dp.include_router(shorten.router)
    dp.include_router(manage.router)
    dp.include_router(stats.router)

    # ---- Private-chat-only gate ------------------------------------------
    # Attach IsPrivate at the top-level dispatcher observers so it is the
    # FIRST filter checked for every event, before any router filters and
    # certainly before the access middleware hits the DB.
    dp.message.filter(IsPrivate())
    dp.callback_query.filter(IsPrivate())

    return dp
