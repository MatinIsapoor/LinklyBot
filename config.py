"""
Application configuration module.

Loads environment variables via python-dotenv and exposes them
through a strongly-typed ``Config`` dataclass. A single ``config``
instance is imported everywhere in the project.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

# Load .env from the project root (where config.py lives).
load_dotenv(Path(__file__).resolve().parent / ".env")

_DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./bot.db"


def _get_int_env(name: str, default: int) -> int:
    """Read an integer env var, falling back to ``default`` on bad input."""
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool_env(name: str, default: bool = False) -> bool:
    """Read a boolean env var (``1/true/yes/on``), falling back on absence."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _first_env(*names: str) -> str:
    """Return the first non-empty env var among ``names``."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _normalize_database_url(raw: str) -> str:
    """
    Turn a provider connection string into a SQLAlchemy async URL.

    * ``postgres://`` / ``postgresql://`` -> ``postgresql+asyncpg://``
    * ``sslmode=require`` -> ``ssl=require`` (asyncpg's parameter name)
    * drops ``channel_binding`` (not supported by asyncpg)
    """
    url = (raw or "").strip() or _DEFAULT_DATABASE_URL

    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]

    if not url.startswith("postgresql+asyncpg://") or "?" not in url:
        return url

    parts = urlsplit(url)
    params: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "sslmode":
            key = "ssl"
        elif key == "channel_binding":
            continue
        params.append((key, value))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment)
    )


@dataclass(frozen=True, slots=True)
class Config:
    """Central, immutable application configuration."""

    # --- Telegram ---
    BOT_TOKEN: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", "").strip())

    # --- Public URL used to build short links / the Telegram webhook ---
    # Render provides RENDER_EXTERNAL_URL automatically, so BASE_URL can be
    # omitted there. Set BASE_URL explicitly to use a custom domain.
    BASE_URL: str = field(
        default_factory=lambda: (
            _first_env("BASE_URL", "RENDER_EXTERNAL_URL") or "https://example.com"
        ).rstrip("/")
    )

    # --- Database ---
    DATABASE_URL: str = field(
        default_factory=lambda: _normalize_database_url(
            os.getenv("DATABASE_URL", "")
        )
    )

    # --- Short code generation ---
    SHORT_CODE_LENGTH: int = field(
        default_factory=lambda: _get_int_env("SHORT_CODE_LENGTH", 6)
    )

    # --- Logging ---
    LOG_LEVEL: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # --- Telegram delivery mode ---
    # Webhooks are enabled automatically on Render (RENDER_EXTERNAL_URL is
    # set there), or explicitly via USE_WEBHOOK=true.
    USE_WEBHOOK: bool = field(
        default_factory=lambda: _get_bool_env(
            "USE_WEBHOOK", bool(os.getenv("RENDER_EXTERNAL_URL", "").strip())
        )
    )
    WEBHOOK_SECRET: str = field(
        default_factory=lambda: os.getenv("WEBHOOK_SECRET", "").strip()
    )
    WEBHOOK_PATH: str = field(
        default_factory=lambda: os.getenv(
            "WEBHOOK_PATH", "/telegram/webhook"
        ).strip()
        or "/telegram/webhook"
    )

    # --- Keep-alive (Render free plan spins down after 15 idle minutes) ---
    # Enabled automatically on Render; the app pings its own /healthz URL
    # every KEEP_ALIVE_INTERVAL seconds so inbound traffic never stops.
    KEEP_ALIVE: bool = field(
        default_factory=lambda: _get_bool_env(
            "KEEP_ALIVE", bool(os.getenv("RENDER_EXTERNAL_URL", "").strip())
        )
    )
    KEEP_ALIVE_INTERVAL: int = field(
        default_factory=lambda: max(
            60, min(840, _get_int_env("KEEP_ALIVE_INTERVAL", 600))
        )
    )

    @property
    def sync_database_url(self) -> str:
        """Synchronous SQLite URL (used by the redirect web server, if any)."""
        return self.DATABASE_URL.replace("+aiosqlite", "")


# Singleton used across the project.
config = Config()
