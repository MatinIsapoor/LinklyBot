"""
``database.crud`` - small, focused user persistence helpers.

Only user resolution lives here. Link persistence lives in
:class:`services.link_service.LinkService` so business rules
(ownership checks, code generation, expiry parsing) stay in one place.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User


async def ensure_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> User:
    """
    Get the user row for ``telegram_id``, creating it on first contact.

    The call commits its own transaction so middleware can use a
    short-lived session without extra bookkeeping.

    Args:
        session: Active async session.
        telegram_id: Telegram numeric user id (unique).
        username: Latest ``@username`` (may be None).
        full_name: Latest display name.

    Returns:
        The persistent (committed) :class:`User` row.
    """
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user: User | None = result.scalar_one_or_none()

    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name or "",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    # Keep profile fields fresh without spamming UPDATEs.
    changed = False
    if username is not None and user.username != username:
        user.username = username
        changed = True
    if full_name and user.full_name != full_name:
        user.full_name = full_name
        changed = True
    if changed:
        await session.commit()
        await session.refresh(user)
    return user


async def get_user_by_telegram_id(
    session: AsyncSession, telegram_id: int
) -> User | None:
    """Return the user row for ``telegram_id`` or ``None``."""
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()
