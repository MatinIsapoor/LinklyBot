"""
``services.link_service`` - business logic for shortened links.

All methods are ownership-scoped: every read/write takes ``user_id``
(the internal ``users.id``) and filters by it, so a user can never
touch another user's links.

Short codes are random ``[a-zA-Z0-9]`` strings (default 6 chars,
configurable via ``SHORT_CODE_LENGTH``). Collisions are retried.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import desc, func, select

from database.models import Link

if TYPE_CHECKING:
    from database.database import Database

# Allowed categories (shown in the category picker keyboard).
CATEGORIES: list[str] = ["آموزشی", "ویدیو", "شخصی", "متفرقه"]

# Fields that :meth:`LinkService.edit` accepts.
_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {"title", "original_url", "category", "expires_at"}
)

_ALPHABET: str = string.ascii_letters + string.digits
_PAGE_SIZE: int = 10


def _utcnow() -> datetime:
    """Naive UTC timestamp (consistent with ``models.utcnow``)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class CategoryStat:
    """One row of the per-category histogram."""

    category: str
    count: int


@dataclass(frozen=True, slots=True)
class UserStats:
    """Aggregate statistics for one user (computed fully in SQL)."""

    total_links: int = 0
    total_clicks: int = 0
    active_links: int = 0
    expired_links: int = 0
    by_category: tuple[CategoryStat, ...] = ()
    top_links: tuple[Link, ...] = field(default_factory=tuple)


def _normalize_url(url: str) -> str:
    """Strip whitespace and validate the URL scheme. Raises ``ValueError``."""
    cleaned = (url or "").strip()
    if len(cleaned) > 2048:
        raise ValueError("آدرس خیلی طولانیه (حداکثر ۲۰۴۸ کاراکتر).")
    lowered = cleaned.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise ValueError("آدرس باید با http:// یا https:// شروع بشه.")
    # Basic host presence check (``https://`` alone is not a URL).
    remainder = cleaned.split("://", 1)[1] if "://" in cleaned else ""
    if not remainder or remainder.strip() == "":
        raise ValueError("آدرس باید اسم هاست داشته باشه.")
    if any(ch.isspace() for ch in cleaned):
        raise ValueError("آدرس نباید فاصله داشته باشه.")
    return cleaned


def _parse_expiry(value: str | datetime | None) -> datetime | None:
    """
    Parse an expiry value.

    Accepts ``None``, a ``datetime``, ``"none"``/``""`` (clears expiry)
    or an ISO8601 string like ``2026-12-31T23:59`` (assumed UTC when naive).
    Raises ``ValueError`` on unparsable input.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    if raw.lower() in ("", "none", "never", "null"):
        return None
    # Accept trailing ``Z`` (UTC designator) for convenience.
    candidate = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(
            "تاریخ نامعتبره. با فرمت ISO8601 بفرست مثل 2026-12-31T23:59 یا 'none'."
        ) from exc
    # Normalise to naive UTC (consistent with models.utcnow).
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _generate_code(length: int = 6) -> str:
    """Generate one random short code candidate."""
    length = max(4, min(16, length))  # hard clamp for safety
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


class LinkService:
    """Stateless service; holds only the DB facade + code length."""

    def __init__(self, db: Database, code_length: int = 6) -> None:
        self._db = db
        # Requirement: 6-8 chars. Clamp configured value into that range
        # (with a wider safety net so misconfiguration cannot break us).
        self._code_length = max(4, min(16, code_length or 6))

    # -- create ------------------------------------------------------

    async def create(
        self,
        *,
        user_id: int,
        url: str,
        title: str = "",
        category: str = "متفرقه",
        expires_at: str | datetime | None = None,
    ) -> Link:
        """
        Shorten ``url`` for ``user_id`` and return the persisted :class:`Link`.

        Raises:
            ValueError: On invalid URL / category / expiry.
        """
        original_url = _normalize_url(url)

        if category not in CATEGORIES:
            category = "متفرقه"
        expiry = _parse_expiry(expires_at)
        clean_title = (title or "").strip()[:128]

        async with self._db.sessions() as session:
            # Generate a unique short code (retry on collision).
            short_code = ""
            for _ in range(10):
                candidate = _generate_code(self._code_length)
                exists = await session.execute(
                    select(func.count(Link.id)).where(
                        Link.short_code == candidate
                    )
                )
                if (exists.scalar() or 0) == 0:
                    short_code = candidate
                    break
            if not short_code:
                raise ValueError("نتونستم کد یکتای کوتاه بسازم، دوباره تلاش کن.")

            link = Link(
                original_url=original_url,
                short_code=short_code,
                title=clean_title,
                category=category,
                user_id=user_id,
                clicks=0,
            )
            if expiry is not None:
                link.expires_at = expiry
            session.add(link)
            await session.commit()
            await session.refresh(link)
            return link

    # -- read --------------------------------------------------------

    async def get(self, *, user_id: int, link_id: int) -> Link | None:
        """Return one link owned by ``user_id`` or ``None``."""
        async with self._db.sessions() as session:
            result = await session.execute(
                select(Link).where(Link.id == link_id, Link.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def get_by_code(self, short_code: str) -> Link | None:
        """Return a link by its public short code (no ownership check)."""
        async with self._db.sessions() as session:
            result = await session.execute(
                select(Link).where(Link.short_code == short_code)
            )
            return result.scalar_one_or_none()

    async def list_links(
        self, *, user_id: int, page: int = 0, page_size: int = _PAGE_SIZE
    ) -> list[Link]:
        """
        Return up to ``page_size`` links for ``user_id`` (newest first).

        ``page`` is zero-based: ``page=0`` returns items 0-9, ``page=1``
        returns 10-19, etc.
        """
        page = max(0, page)
        page_size = max(1, min(50, page_size))
        async with self._db.sessions() as session:
            result = await session.execute(
                select(Link)
                .where(Link.user_id == user_id)
                .order_by(desc(Link.id))
                .offset(page * page_size)
                .limit(page_size)
            )
            return list(result.scalars().all())

    # -- statistics (DB aggregation, no link loading) ------------------

    async def get_stats(self, *, user_id: int, top_n: int = 5) -> UserStats:
        """
        Compute aggregate statistics for ``user_id`` fully in SQL.

        Runs a handful of ``COUNT`` / ``SUM`` / ``GROUP BY`` queries plus
        one ``ORDER BY clicks DESC LIMIT`` for the top links - O(1) memory
        regardless of how many links the user owns.

        ``top_n`` is clamped to 1-10. Only top links with at least one
        click are returned (an empty tuple means "nothing clicked yet").
        """
        top_n = max(1, min(10, top_n or 5))
        now = _utcnow()
        async with self._db.sessions() as session:
            total_row = (
                await session.execute(
                    select(
                        func.count(Link.id),
                        func.coalesce(func.sum(Link.clicks), 0),
                    ).where(Link.user_id == user_id)
                )
            ).one()
            total_links: int = int(total_row[0] or 0)
            total_clicks: int = int(total_row[1] or 0)

            if total_links == 0:
                return UserStats()

            active_links = (
                await session.execute(
                    select(func.count(Link.id)).where(
                        Link.user_id == user_id,
                        Link.is_active.is_(True),
                        (Link.expires_at.is_(None)) | (Link.expires_at > now),
                    )
                )
            ).scalar() or 0

            expired_links = (
                await session.execute(
                    select(func.count(Link.id)).where(
                        Link.user_id == user_id,
                        Link.expires_at.is_not(None),
                        Link.expires_at <= now,
                    )
                )
            ).scalar() or 0

            cat_rows = (
                await session.execute(
                    select(Link.category, func.count(Link.id))
                    .where(Link.user_id == user_id)
                    .group_by(Link.category)
                    .order_by(desc(func.count(Link.id)))
                )
            ).all()
            by_category = tuple(
                CategoryStat(category=str(cat), count=int(cnt))
                for cat, cnt in cat_rows
            )

            top_rows = (
                await session.execute(
                    select(Link)
                    .where(Link.user_id == user_id, Link.clicks > 0)
                    .order_by(desc(Link.clicks), desc(Link.id))
                    .limit(top_n)
                )
            ).scalars().all()

            return UserStats(
                total_links=total_links,
                total_clicks=total_clicks,
                active_links=int(active_links),
                expired_links=int(expired_links),
                by_category=by_category,
                top_links=tuple(top_rows),
            )

    # -- update ------------------------------------------------------

    async def edit(
        self, *, user_id: int, link_id: int, field: str, value: str
    ) -> Link:
        """
        Update a single field of a link owned by ``user_id``.

        ``field`` must be one of ``title`` / ``original_url`` /
        ``category`` / ``expires_at``. ``value`` is always the raw string
        coming from the handler (FSM text or callback payload).

        Raises:
            ValueError: On unknown field, failed validation, or missing link
                (handlers translate "not found" into a friendly message).
        """
        if field not in _EDITABLE_FIELDS:
            raise ValueError(f"فیلد ناشناخته: {field}.")

        async with self._db.sessions() as session:
            result = await session.execute(
                select(Link).where(Link.id == link_id, Link.user_id == user_id)
            )
            link: Link | None = result.scalar_one_or_none()
            if link is None:
                raise ValueError("لینک پیدا نشد.")

            if field == "title":
                link.title = (value or "").strip()[:128]
            elif field == "original_url":
                link.original_url = _normalize_url(value)
            elif field == "category":
                if value not in CATEGORIES:
                    raise ValueError(f"دسته‌بندی ناشناخته. انتخاب کن: {', '.join(CATEGORIES)}.")
                link.category = value
            elif field == "expires_at":
                link.expires_at = _parse_expiry(value)

            await session.commit()
            await session.refresh(link)
            return link

    # -- delete ------------------------------------------------------

    async def delete(self, *, user_id: int, link_id: int) -> bool:
        """Delete a link owned by ``user_id``. Returns ``True`` if removed."""
        async with self._db.sessions() as session:
            result = await session.execute(
                select(Link).where(Link.id == link_id, Link.user_id == user_id)
            )
            link: Link | None = result.scalar_one_or_none()
            if link is None:
                return False
            await session.delete(link)
            await session.commit()
            return True

    # -- redirect / click tracking -----------------------------------

    async def resolve_and_track(self, short_code: str) -> Link | None:
        """
        Resolve ``short_code`` for the redirect server.

        On success increments ``clicks``, stamps ``last_clicked_at`` and
        returns the link. Returns ``None`` when the code is unknown,
        disabled, or expired (expired links are NOT counted).
        """
        async with self._db.sessions() as session:
            result = await session.execute(
                select(Link).where(Link.short_code == short_code)
            )
            link: Link | None = result.scalar_one_or_none()
            if link is None:
                return None
            if not link.is_active or link.is_expired:
                return None
            link.clicks += 1
            link.last_clicked_at = _utcnow()
            await session.commit()
            await session.refresh(link)
            return link
