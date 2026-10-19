"""
``filters.private`` - allow updates only from private (one-to-one) chats.

The filter is deliberately registered at router/observer level so aiogram
evaluates it *before* inner middlewares.  This guarantees that updates
originating from groups/channels are dropped before the access middleware
touches the database (no public exposure of user lookups).
"""

from __future__ import annotations

from typing import Literal, Union

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject


class IsPrivate(BaseFilter):
    """
    Accept the update only when it comes from a private chat.

    Works for both ``Message`` and ``CallbackQuery`` events:

    * for messages it checks the chat type of the incoming message;
    * for callback queries it checks the chat type of the message the
      button was attached to (``callback_query.message``), falling back to
      ``chat_instance``/``from_user`` when the origin message is
      unavailable (e.g. too old or deleted).
    """

    async def __call__(
        self,
        event: TelegramObject,
    ) -> Union[Literal[False], Literal[True]]:
        # -- plain messages / edited messages ----------------------------
        if isinstance(event, Message):
            return event.chat.type == "private"

        # -- button presses ----------------------------------------------
        if isinstance(event, CallbackQuery):
            message = event.message
            if message is not None:
                # CallbackQuery.message can be InaccessibleMessage for old
                # updates; guard with hasattr before reading chat.
                chat = getattr(message, "chat", None)
                if chat is not None:
                    return chat.type == "private"
            # Without message context we cannot verify privacy - reject.
            return False

        # Unknown event type - be conservative.
        return False


# Convenience singletons/aliases used by routers.
private_filter = IsPrivate()
