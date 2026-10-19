"""
``handlers.stats`` - aggregate statistics for the user's links.

Statistics are computed fully in SQL via ``LinkService.get_stats``
(``COUNT`` / ``SUM`` / ``GROUP BY`` + top-N query), so rendering stats
is O(1) in memory no matter how many links the user owns.
"""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.filters.private import IsPrivate
from bot.keyboards.inline import CB_STATS, main_menu_keyboard
from config import config
from database.models import User
from services.link_service import UserStats

router = Router()

# How many top links to show in the "most clicked" section.
TOP_N = 5


def _build_stats_text(stats: UserStats) -> str:
    """
    Render the statistics card from a :class:`UserStats` snapshot.

    All user-provided strings (titles, codes, categories, URLs) are
    HTML-escaped before being embedded into the message.
    """
    if stats.total_links == 0:
        return (
            "📊 <b>آمار تو</b>\n\n"
            "📭 <b>هنوز لینکی نداری.</b>\n"
            "یه آدرس بفرست تا اولین لینک کوتاهت رو بسازم!"
        )

    lines = [
        "📊 <b>آمار تو</b>",
        "",
        f"🔗 تعداد لینک‌ها: <b>{stats.total_links}</b>",
        f"👀 مجموع کلیک‌ها: <b>{stats.total_clicks}</b>",
        f"✅ فعال: <b>{stats.active_links}</b>",
        f"⏰ منقضی‌شده: <b>{stats.expired_links}</b>",
    ]

    if stats.by_category:
        lines.append("")
        lines.append("🏷 <b>بر اساس دسته‌بندی:</b>")
        for entry in stats.by_category:
            lines.append(f"  • {html.escape(entry.category)}: <b>{entry.count}</b>")

    if stats.top_links:
        lines.append("")
        lines.append(f"🔥 <b>پربازدیدترین {len(stats.top_links)} لینک:</b>")
        for link in stats.top_links:
            label = html.escape(link.title or link.short_code)
            url = link.short_url(config.BASE_URL)
            lines.append(
                f"  • <a href=\"{html.escape(url, quote=True)}\">"
                f"{label}</a> - {link.clicks} کلیک"
            )

    return "\n".join(lines)


async def _render_stats(link_service, user_id: int) -> str:
    """Load stats via the service and render them (never raises)."""
    try:
        stats: UserStats = await link_service.get_stats(
            user_id=user_id, top_n=TOP_N
        )
    except Exception:
        return "⚠️ نتونستم آمار رو بگیرم. لطفا بعدا دوباره تلاش کن."
    return _build_stats_text(stats)


@router.message(IsPrivate(), Command("stats"))
async def cmd_stats(message: Message, link_service, db_user: User) -> None:
    """Show aggregate statistics for the user's links."""
    await message.answer(
        await _render_stats(link_service, db_user.id),
        reply_markup=main_menu_keyboard(),
        link_preview_options={"is_disabled": True},
    )


@router.callback_query(F.data == CB_STATS)
async def cb_stats(query: CallbackQuery, link_service, db_user: User) -> None:
    """Stats via inline button - same content, edits message in place."""
    text = await _render_stats(link_service, db_user.id)
    if query.message is not None:
        try:
            await query.message.edit_text(
                text,
                reply_markup=main_menu_keyboard(),
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            await query.message.answer(
                text,
                reply_markup=main_menu_keyboard(),
                link_preview_options={"is_disabled": True},
            )
    await query.answer()
