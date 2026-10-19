"""
``middlewares.access`` - user resolution, throttling and DI injection.

For every incoming message / callback query this middleware:

1. rejects non-private chats as a defensive back-stop (the primary
   ``IsPrivate`` filter already runs earlier in the chain);
2. applies a lightweight bounded-memory per-user throttle;
3. ensures the Telegram user exists in the database via
   ``database.crud.ensure_user`` and stores the row as ``db_user``;
4. exposes the shared ``LinkService`` as ``link_service``.

Handlers then simply accept ``db_user: User`` and
``link_service: LinkService`` as keyword arguments (aiogram injects
anything found in the middleware ``data`` mapping).
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

if TYPE_CHECKING:
    from database.database import Database
    from services.link_service import LinkService

logger = logging.getLogger(__name__)


class _UserThrottle:
    """
    Tiny bounded LRU window limiter (per user).

    Keeps at most ``max_users`` buckets; each bucket stores the timestamps
    of the user's recent events inside a sliding ``window_seconds``
    window.  Events above ``max_events`` within the window are rejected.
    Buckets are evicted LRU-style so memory stays bounded even with many
    users.
    """

    __slots__ = ("window", "max_events", "max_users", "_buckets")

    def __init__(
        self,
        window_seconds: float = 3.0,
        max_events: int = 5,
        max_users: int = 10_000,
    ) -> None:
        self.window = window_seconds
        self.max_events = max_events
        self.max_users = max_users
        # OrderedDict gives O(1) move-to-end for LRU behaviour.
        self._buckets: OrderedDict[int, list[float]] = OrderedDict()

    def allow(self, user_id: int) -> bool:
        """
        Register an event for ``user_id`` and report whether it is allowed.
        """
        now = time.monotonic()
        bucket = self._buckets.get(user_id)

        if bucket is None:
            if len(self._buckets) >= self.max_users:
                # Evict the least recently used bucket.
                self._buckets.popitem(last=False)
            bucket = []
            self._buckets[user_id] = bucket
        else:
            # Touch for LRU ordering.
            self._buckets.move_to_end(user_id)

        # Drop timestamps that fell out of the sliding window.
        cutoff = now - self.window
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)

        if len(bucket) >= self.max_events:
            return False
        bucket.append(now)
        return True


class AccessMiddleware(BaseMiddleware):
    """
    Resolves (or lazily creates) the DB user for the update and injects
    both ``db_user`` and ``link_service`` into the handler kwargs.

    Also enforces the private-chat-only rule as a back-stop and applies a
    simple per-user rate limit.
    """

    def __init__(self, db: "Database", link_service: "LinkService") -> None:
        self._db = db
        # Shared, stateless service instance - the DI contract object.
        self._link_service = link_service
        self._throttle = _UserThrottle()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # ------------------------------------------------------------------
        # 1. Identify the acting Telegram user (works for both event kinds).
        # ------------------------------------------------------------------
        tg_user = data.get("event_from_user")
        if tg_user is None:
            # Fallback: read straight from the event if the context key
            # prepared by aiogram is missing.
            if isinstance(event, (Message, CallbackQuery)):
                tg_user = event.from_user
        if tg_user is None:
            # No user context (channel posts etc.) - drop silently.
            return None

        telegram_id: int = tg_user.id

        # ------------------------------------------------------------------
        # 2. Private-chat back-stop (primary gate is the IsPrivate filter).
        # ------------------------------------------------------------------
        chat = data.get("event_chat")
        if chat is None and isinstance(event, Message):
            chat = event.chat
        if chat is not None and chat.type != "private":
            return None

        # ------------------------------------------------------------------
        # 3. Per-user throttle (bounded memory, sliding window).
        # ------------------------------------------------------------------
        if not self._throttle.allow(telegram_id):
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer("یه کم آروم‌تر! داری خیلی سریع پیام می‌فرستی.")
                else:
                    await event.answer("داری خیلی سریع پیام می‌فرستی، یه کم آروم‌تر لطفا.")
            except Exception:
                # Rate-limit notification is best-effort only.
                pass
            return None

        # ------------------------------------------------------------------
        # 4. Ensure the user exists in the DB and inject dependencies.
        # ------------------------------------------------------------------
        # ensure_user() commits its own transaction, so a short-lived
        # session from the factory is fine here.
        try:
            async with self._db.sessions() as session:
                db_user = await self._crud_ensure_user(
                    session,
                    telegram_id=telegram_id,
                    username=tg_user.username,
                    full_name=tg_user.full_name,
                )
        except Exception:
            # Never leak DB errors to the user or the logs' exception text.
            logger.warning("AccessMiddleware: user resolution failed for %s", telegram_id)
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer("سرویس الان در دسترس نیست، لطفا بعدا دوباره تلاش کن.")
                else:
                    await event.answer("سرویس الان در دسترس نیست، لطفا بعدا دوباره تلاش کن.")
            except Exception:
                pass
            return None

        if db_user is None:  # pragma: no cover - defensive
            logger.warning("AccessMiddleware: could not resolve user %s", telegram_id)
            return None

        # Optional ban enforcement (column exists on the User model).
        if getattr(db_user, "is_banned", False):
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer("دسترسیت محدود شده.")
                else:
                    await event.answer("دسترسیت محدود شده.")
            except Exception:
                pass
            return None

        # Inject everything handlers need (DI contract).
        data["db_user"] = db_user
        data["link_service"] = self._link_service
        return await handler(event, data)

    # ------------------------------------------------------------------
    # Indirection helper kept tiny so tests can monkeypatch it.
    # ------------------------------------------------------------------
    async def _crud_ensure_user(self, session, **kwargs):
        from database.crud import ensure_user  # local import avoids cycles

        return await ensure_user(session, **kwargs)
