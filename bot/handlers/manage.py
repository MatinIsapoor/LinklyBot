"""
``handlers.manage`` - listing, viewing, editing and deleting links.

All service calls are always scoped to ``db_user.id`` - a user can never
touch somebody else's link because ``get``/``edit``/``delete`` verify
ownership by ``user_id`` internally, and we pass exactly ``db_user.id``.

Callback routing is validated through ``CallbackData`` schemas declared
in :mod:`bot.keyboards.inline`; malformed payloads fail the filter and
never reach a handler.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.filters.private import IsPrivate
from bot.keyboards.inline import (
    CatCB,
    EditCB,
    ExpCB,
    LinkCB,
    PageCB,
    category_keyboard,
    delete_confirm_keyboard,
    edit_menu_keyboard,
    expiry_keyboard,
    link_view_keyboard,
    links_list_keyboard,
    main_menu_keyboard,
)
from config import config
from database.models import User
from services.link_service import CATEGORIES

router = Router()

PAGE_SIZE = 10  # exact page size required by the contract

# Same permissive URL heuristic as in shorten.py - full validation is the
# service's job; here we only reject obvious non-URLs.
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


class ManageFSM(StatesGroup):
    """States for editing a link's free-text fields."""

    waiting_title = State()   # expecting new title text
    waiting_url = State()     # expecting new original URL
    waiting_expiry = State()  # expecting ISO8601 datetime or 'none'


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _fmt_dt(value: Optional[datetime]) -> str:
    """Human-friendly UTC timestamp or a dash."""
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _link_card(link) -> str:
    """HTML card describing one link (all values HTML-escaped)."""
    short_url = link.short_url(config.BASE_URL)
    title = html.escape(link.title or "-")
    original = html.escape(link.original_url, quote=True)
    category = html.escape(link.category)
    status = "⛔ منقضی‌شده" if link.is_expired else ("✅ فعال" if link.is_active else "🚫 غیرفعال")

    expires = (
        "🚫 بدون انقضا"
        if link.expires_at is None
        # _fmt_dt() already includes the "UTC" suffix.
        else f"⏰ انقضا: <b>{_fmt_dt(link.expires_at)}</b>"
    )

    return (
        f"🔗 <b><a href=\"{html.escape(short_url, quote=True)}\">"
        f"{html.escape(short_url)}</a></b>\n\n"
        f"📝 عنوان: {title}\n"
        f"🎯 آدرس اصلی: {original}\n"
        f"🏷 دسته‌بندی: <b>{category}</b>\n"
        f"👀 کلیک‌ها: <b>{link.clicks}</b>\n"
        f"📍 وضعیت: {status}\n"
        f"📅 ساخته‌شده: {_fmt_dt(link.created_at)}\n"
        f"{expires}"
    )


def _not_found_text() -> str:
    return "❌ لینک پیدا نشد (شاید حذف شده)."


# ---------------------------------------------------------------------------
# /links - entry point to the paginated list
# ---------------------------------------------------------------------------


@router.message(IsPrivate(), Command("links"))
async def cmd_links(
    message: Message,
    link_service,
    db_user: User,
) -> None:
    """Show the first page of the user's links."""
    await _send_links_page(message, link_service, db_user.id, page=0)


async def _send_links_page(message: Message, link_service, user_id: int, page: int) -> None:
    """Fetch exactly one page (max 10 items) and render it."""
    # Contract: list_links returns up to 10 items for the given page.
    links = await link_service.list_links(user_id=user_id, page=page)
    has_next = len(links) == PAGE_SIZE

    if not links and page == 0:
        await message.answer(
            "📭 هنوز لینکی نداری.\nاز دکمه ➕ <b>لینک جدید</b> استفاده کن یا فقط یه آدرس برام بفرست!",
            reply_markup=main_menu_keyboard(),
        )
        return
    header = f"<b>لینک‌های تو</b> - صفحه {page + 1}\n\n"
    lines = []
    for i, link in enumerate(links):
        label = html.escape(link.title or link.short_code)
        lines.append(f"{page * PAGE_SIZE + i + 1}. <b>{label}</b> - {link.clicks} کلیک")
    body = "\n".join(lines)

    await message.answer(
        header + body + "\n\nبرای مدیریتش روی یه لینک بزن:",
        reply_markup=links_list_keyboard(links, page, has_next),
        link_preview_options={"is_disabled": True},
    )


# ---------------------------------------------------------------------------
# Pagination callbacks
# ---------------------------------------------------------------------------


@router.callback_query(PageCB.filter())
async def cb_page(
    query: CallbackQuery,
    callback_data: PageCB,
    link_service,
    db_user: User,
) -> None:
    """Render another page of the link list (edits the message in place)."""
    page = max(0, callback_data.page)
    links = await link_service.list_links(user_id=db_user.id, page=page)
    has_next = len(links) == PAGE_SIZE

    if not links:
        await query.answer("توی این صفحه لینکی نیست.", show_alert=True)
        return

    lines = []
    for i, link in enumerate(links):
        label = html.escape(link.title or link.short_code)
        lines.append(f"{page * PAGE_SIZE + i + 1}. <b>{label}</b> - {link.clicks} کلیک")
    body = "\n".join(lines)

    text = f"<b>لینک‌های تو</b> - صفحه {page + 1}\n\n{body}\n\nبرای مدیریتش روی یه لینک بزن:"
    markup = links_list_keyboard(links, page, has_next)

    try:
        if query.message is not None:
            await query.message.edit_text(
                text,
                reply_markup=markup,
                link_preview_options={"is_disabled": True},
            )
    except Exception:
        # Message may be unmodifiable (too old); send a fresh copy instead.
        if query.message is not None:
            await query.message.answer(
                text,
                reply_markup=markup,
                link_preview_options={"is_disabled": True},
            )
    await query.answer()


# ---------------------------------------------------------------------------
# Link detail view
# ---------------------------------------------------------------------------


@router.callback_query(LinkCB.filter(F.action == "view"))
async def cb_link_view(
    query: CallbackQuery,
    callback_data: LinkCB,
    link_service,
    db_user: User,
) -> None:
    """Show one link with all its fields plus management buttons."""
    # Ownership enforced: get() is scoped to db_user.id.
    link = await link_service.get(user_id=db_user.id, link_id=callback_data.link_id)
    if link is None:
        await query.answer(_not_found_text(), show_alert=True)
        return

    text = _link_card(link)
    markup = link_view_keyboard(
        link_id=link.id,
        page=callback_data.page,
        base_url=config.BASE_URL,
        short_code=link.short_code,
    )

    if query.message is not None:
        try:
            await query.message.edit_text(
                text,
                reply_markup=markup,
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            await query.message.answer(
                text,
                reply_markup=markup,
                link_preview_options={"is_disabled": True},
            )
    await query.answer()


# ---------------------------------------------------------------------------
# Edit menu
# ---------------------------------------------------------------------------


@router.callback_query(LinkCB.filter(F.action == "edit"))
async def cb_link_edit_menu(
    query: CallbackQuery,
    callback_data: LinkCB,
    link_service,
    db_user: User,
) -> None:
    """Show the field-selection edit menu for one link."""
    link = await link_service.get(user_id=db_user.id, link_id=callback_data.link_id)
    if link is None:
        await query.answer(_not_found_text(), show_alert=True)
        return

    text = (
        "✏️ <b>ویرایش لینک</b>\n\n"
        f"{html.escape(link.title or link.short_code)}\n\n"
        "کدوم فیلد رو می‌خوای عوض کنی؟"
    )
    if query.message is not None:
        try:
            await query.message.edit_text(
                text,
                reply_markup=edit_menu_keyboard(link.id),
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            await query.message.answer(
                text,
                reply_markup=edit_menu_keyboard(link.id),
                link_preview_options={"is_disabled": True},
            )
    await query.answer()


# ---------------------------------------------------------------------------
# Field-specific editing (category / expiry via buttons, title & URL via FSM)
# ---------------------------------------------------------------------------


@router.callback_query(EditCB.filter(F.field == "category"))
async def cb_edit_category(
    query: CallbackQuery,
    callback_data: EditCB,
    link_service,
    db_user: User,
) -> None:
    """Show the category picker for the link."""
    link = await link_service.get(user_id=db_user.id, link_id=callback_data.link_id)
    if link is None:
        await query.answer(_not_found_text(), show_alert=True)
        return

    text = f"🏷 یه <b>دسته‌بندی</b> جدید برای <b>{html.escape(link.title or link.short_code)}</b> انتخاب کن:"
    if query.message is not None:
        try:
            await query.message.edit_text(
                text,
                reply_markup=category_keyboard(link.id),
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            await query.message.answer(
                text,
                reply_markup=category_keyboard(link.id),
                link_preview_options={"is_disabled": True},
            )
    await query.answer()


@router.callback_query(EditCB.filter(F.field == "expiry"))
async def cb_edit_expiry(
    query: CallbackQuery,
    callback_data: EditCB,
    link_service,
    db_user: User,
) -> None:
    """Show the expiry preset picker for the link."""
    link = await link_service.get(user_id=db_user.id, link_id=callback_data.link_id)
    if link is None:
        await query.answer(_not_found_text(), show_alert=True)
        return

    text = (
        "⏰ یه <b>انقضای</b> جدید برای "
        f"<b>{html.escape(link.title or link.short_code)}</b> انتخاب کن:\n\n"
        "یا یه تاریخ دلخواه با فرمت ISO8601 بفرست، مثلا "
        "<code>2026-12-31T23:59</code> (به وقت UTC)، یا کلمه <code>none</code>."
    )
    if query.message is not None:
        try:
            await query.message.edit_text(
                text,
                reply_markup=expiry_keyboard(link.id),
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            await query.message.answer(
                text,
                reply_markup=expiry_keyboard(link.id),
                link_preview_options={"is_disabled": True},
            )
    await query.answer()


@router.callback_query(EditCB.filter(F.field == "title"))
async def cb_edit_title(
    query: CallbackQuery,
    callback_data: EditCB,
    state: FSMContext,
    link_service,
    db_user: User,
) -> None:
    """Ask for the new title (free text via FSM)."""
    link = await link_service.get(user_id=db_user.id, link_id=callback_data.link_id)
    if link is None:
        await query.answer(_not_found_text(), show_alert=True)
        return

    # Store which link we are editing so the FSM handler knows the target.
    await state.set_state(ManageFSM.waiting_title)
    await state.update_data(edit_link_id=link.id)

    if query.message is not None:
        try:
            await query.message.edit_text(
                "📝 <b>عنوان جدید</b> رو بفرست (متن ساده).\nبرای لغو /cancel رو بفرست.",
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            pass
    await query.answer()


@router.callback_query(EditCB.filter(F.field == "url"))
async def cb_edit_url(
    query: CallbackQuery,
    callback_data: EditCB,
    state: FSMContext,
    link_service,
    db_user: User,
) -> None:
    """Ask for the new target URL (free text via FSM)."""
    link = await link_service.get(user_id=db_user.id, link_id=callback_data.link_id)
    if link is None:
        await query.answer(_not_found_text(), show_alert=True)
        return

    await state.set_state(ManageFSM.waiting_url)
    await state.update_data(edit_link_id=link.id)

    if query.message is not None:
        try:
            await query.message.edit_text(
                "🔗 <b>آدرس اصلی جدید</b> رو بفرست "
                "(باید با <code>http://</code> یا <code>https://</code> شروع بشه).\n"
                "برای لغو /cancel رو بفرست.",
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            pass
    await query.answer()


# ---------------------------------------------------------------------------
# FSM input handlers for editing fields
# ---------------------------------------------------------------------------


@router.message(ManageFSM.waiting_title, F.text)
async def fsm_edit_title(
    message: Message,
    state: FSMContext,
    link_service,
    db_user: User,
) -> None:
    """Apply the new title sent as free text."""
    data = await state.get_data()
    link_id = data.get("edit_link_id")
    title = (message.text or "").strip()[:128]  # model column is String(128)

    try:
        link = await link_service.edit(
            user_id=db_user.id, link_id=link_id, field="title", value=title
        )
    except ValueError as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}")
        return
    except Exception:
        await message.answer("⚠️ نتونستم عنوان رو به‌روز کنم. لطفا دوباره تلاش کن.")
        return

    await state.clear()
    await message.answer(
        "✅ عنوان به‌روز شد.\n\n" + _link_card(link),
        reply_markup=link_view_keyboard(
            link_id=link.id,
            page=data.get("page", 0),
            base_url=config.BASE_URL,
            short_code=link.short_code,
        ),
        link_preview_options={"is_disabled": True},
    )


@router.message(ManageFSM.waiting_url, F.text)
async def fsm_edit_url(
    message: Message,
    state: FSMContext,
    link_service,
    db_user: User,
) -> None:
    """Apply the new original URL sent as free text."""
    data = await state.get_data()
    link_id = data.get("edit_link_id")
    url = (message.text or "").strip()

    if not _URL_RE.match(url):
        await message.answer(
            "⚠️ این آدرس معتبر به نظر نمی‌رسه.\n"
            "یه آدرس بفرست که با <code>http://</code> یا <code>https://</code> شروع بشه، یا /cancel."
        )
        return

    try:
        link = await link_service.edit(
            user_id=db_user.id, link_id=link_id, field="original_url", value=url
        )
    except ValueError as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}")
        return
    except Exception:
        await message.answer("⚠️ نتونستم آدرس رو به‌روز کنم. لطفا دوباره تلاش کن.")
        return

    await state.clear()
    await message.answer(
        "✅ آدرس به‌روز شد.\n\n" + _link_card(link),
        reply_markup=link_view_keyboard(
            link_id=link.id,
            page=data.get("page", 0),
            base_url=config.BASE_URL,
            short_code=link.short_code,
        ),
        link_preview_options={"is_disabled": True},
    )


@router.message(ManageFSM.waiting_expiry, F.text)
async def fsm_edit_expiry(
    message: Message,
    state: FSMContext,
    link_service,
    db_user: User,
) -> None:
    """Apply a custom expiry datetime (ISO8601) or 'none'."""
    data = await state.get_data()
    link_id = data.get("edit_link_id")
    raw = (message.text or "").strip()

    try:
        link = await link_service.edit(
            user_id=db_user.id, link_id=link_id, field="expires_at", value=raw
        )
    except ValueError as exc:
        # Service explains *why* parsing failed - show it plus the hint.
        await message.answer(
            f"⚠️ {html.escape(str(exc))}\n"
            "با فرمت ISO8601 بفرست مثل <code>2026-12-31T23:59</code> "
            "یا <code>none</code> رو بفرست تا انقضا برداشته بشه.\n"
            "یا برای لغو /cancel."
        )
        return
    except Exception:
        await message.answer("⚠️ نتونستم انقضا رو به‌روز کنم. لطفا دوباره تلاش کن.")
        return

    await state.clear()
    await message.answer(
        "✅ انقضا به‌روز شد.\n\n" + _link_card(link),
        reply_markup=link_view_keyboard(
            link_id=link.id,
            page=data.get("page", 0),
            base_url=config.BASE_URL,
            short_code=link.short_code,
        ),
        link_preview_options={"is_disabled": True},
    )


# ---------------------------------------------------------------------------
# Category selection (CatCB) - applies immediately, no FSM needed
# ---------------------------------------------------------------------------


@router.callback_query(CatCB.filter())
async def cb_category_pick(
    query: CallbackQuery,
    callback_data: CatCB,
    link_service,
    db_user: User,
) -> None:
    """Apply the chosen category to the link."""
    # Whitelist check - CatCB unpacking already validates pydantic types,
    # but the value itself must be one of the known categories.
    if callback_data.category not in CATEGORIES:
        await query.answer("دسته‌بندی ناشناخته.", show_alert=True)
        return

    try:
        link = await link_service.edit(
            user_id=db_user.id,
            link_id=callback_data.link_id,
            field="category",
            value=callback_data.category,
        )
    except ValueError as exc:
        await query.answer(str(exc) or "مقدار نامعتبر.", show_alert=True)
        return
    except Exception:
        await query.answer("نتونستم دسته‌بندی رو به‌روز کنم.", show_alert=True)
        return

    if query.message is not None:
        try:
            await query.message.edit_text(
                "✅ دسته‌بندی شد <b>"
                f"{html.escape(link.category)}</b>.\n\n" + _link_card(link),
                reply_markup=link_view_keyboard(
                    link_id=link.id,
                    page=0,
                    base_url=config.BASE_URL,
                    short_code=link.short_code,
                ),
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            pass
    await query.answer("دسته‌بندی به‌روز شد.")


# ---------------------------------------------------------------------------
# Expiry presets (ExpCB) - 1h/1d/7d/30d/none
# ---------------------------------------------------------------------------

_EXPIRY_PRESETS: dict[str, Optional[timedelta]] = {
    "none": None,
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


@router.callback_query(ExpCB.filter())
async def cb_expiry_pick(
    query: CallbackQuery,
    callback_data: ExpCB,
    link_service,
    db_user: User,
) -> None:
    """Apply an expiry preset to the link."""
    delta = _EXPIRY_PRESETS.get(callback_data.preset)
    if callback_data.preset not in _EXPIRY_PRESETS:
        await query.answer("گزینه انقضای ناشناخته.", show_alert=True)
        return

    # Build the value string exactly as the service expects it:
    # 'none' clears expiration, otherwise an ISO8601 UTC timestamp.
    if delta is None:
        value = "none"
    else:
        target = datetime.now(timezone.utc).replace(tzinfo=None) + delta
        value = target.isoformat()

    try:
        link = await link_service.edit(
            user_id=db_user.id,
            link_id=callback_data.link_id,
            field="expires_at",
            value=value,
        )
    except ValueError as exc:
        await query.answer(str(exc) or "تاریخ نامعتبر.", show_alert=True)
        return
    except Exception:
        await query.answer("نتونستم انقضا رو به‌روز کنم.", show_alert=True)
        return

    if query.message is not None:
        try:
            await query.message.edit_text(
                "✅ انقضا به‌روز شد.\n\n" + _link_card(link),
                reply_markup=link_view_keyboard(
                    link_id=link.id,
                    page=0,
                    base_url=config.BASE_URL,
                    short_code=link.short_code,
                ),
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            pass
    await query.answer("انقضا به‌روز شد.")


# ---------------------------------------------------------------------------
# Delete flow: confirm -> apply
# ---------------------------------------------------------------------------


@router.callback_query(LinkCB.filter(F.action == "del"))
async def cb_link_delete_ask(
    query: CallbackQuery,
    callback_data: LinkCB,
    link_service,
    db_user: User,
) -> None:
    """Ask for confirmation before deleting."""
    link = await link_service.get(user_id=db_user.id, link_id=callback_data.link_id)
    if link is None:
        await query.answer(_not_found_text(), show_alert=True)
        return

    text = (
        "🗑 <b>این لینک حذف بشه؟</b>\n\n"
        f"{html.escape(link.title or link.short_code)}\n"
        f"<code>{html.escape(link.short_url(config.BASE_URL))}</code>\n\n"
        "این کار قابل برگشت نیست."
    )
    if query.message is not None:
        try:
            await query.message.edit_text(
                text,
                reply_markup=delete_confirm_keyboard(link.id, callback_data.page),
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            await query.message.answer(
                text,
                reply_markup=delete_confirm_keyboard(link.id, callback_data.page),
                link_preview_options={"is_disabled": True},
            )
    await query.answer()


@router.callback_query(LinkCB.filter(F.action == "confirm"))
async def cb_link_delete_confirm(
    query: CallbackQuery,
    callback_data: LinkCB,
    state: FSMContext,
    link_service,
    db_user: User,
) -> None:
    """Actually delete the link after explicit confirmation."""
    deleted = await link_service.delete(user_id=db_user.id, link_id=callback_data.link_id)

    if not deleted:
        await query.answer(_not_found_text(), show_alert=True)
        return

    await state.clear()
    if query.message is not None:
        try:
            await query.message.edit_text(
                "🗑 لینک حذف شد.",
                reply_markup=main_menu_keyboard(),
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            pass
    await query.answer("لینک حذف شد.")


# ---------------------------------------------------------------------------
# Cancel edit dialog (inline button shared by edit sub-menus)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "edit_cancel")
async def cb_edit_cancel(query: CallbackQuery, state: FSMContext) -> None:
    """Abort an edit dialog without changing anything."""
    await state.clear()
    if query.message is not None:
        try:
            await query.message.edit_text(
                "❌ ویرایش لغو شد.",
                reply_markup=main_menu_keyboard(),
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            pass
    await query.answer("لغو شد.")
