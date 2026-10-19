"""
SQLAlchemy declarative models.

Two tables:
    * User  - telegram users of the bot
    * Link  - shortened links owned by users
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


def utcnow() -> datetime:
    """Naive UTC timestamp used as a default for created_at columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class User(Base):
    """A Telegram user who interacted with the bot."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), unique=True, index=True
    )
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)

    # A user can own many links.
    links: Mapped[list["Link"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} telegram_id={self.telegram_id}>"


class Link(Base):
    """A shortened URL owned by a user."""

    __tablename__ = "links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    short_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    category: Mapped[str] = mapped_column(String(32), default="Other")
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_clicked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )

    owner: Mapped["User"] = relationship(back_populates="links")

    # ----- helpers -------------------------------------------------

    @property
    def is_expired(self) -> bool:
        """True when expires_at is set and lies in the past."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc).replace(tzinfo=None) >= self.expires_at

    def short_url(self, base_url: str) -> str:
        """Full public URL of this short link."""
        return f"{base_url.rstrip('/')}/{self.short_code}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Link id={self.id} code={self.short_code!r} clicks={self.clicks}>"
