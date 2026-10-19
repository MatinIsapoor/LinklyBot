"""
``keyboards.inline`` - inline keyboards and callback-data schemas.

Design rules enforced here:

* callback payloads are validated with pydantic-based ``CallbackData``
  schemas, so malformed or forged payloads simply never reach handlers;
* the **original URL is never placed into a URL button** - the only
  external button is the short link (clicks are counted by the web
  side).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

if TYPE_CHECKING:
    from database.models import Link

# ---------------------------------------------------------------------------
# Callback data schemas (validated automatically by aiogram filters).
# ---------------------------------------------------------------------------


class PageCB(CallbackData, prefix="pg"):
    """Pagination navigation: ``pg:<page>``."""

    page: int


class LinkCB(CallbackData, prefix="lnk"):
    """
    Per-link actions: ``lnk:<action>:<link_id>:<page>``.

    ``page`` keeps the user's position in the list so "back" returns to
    the same page they came from.
    """

    action: str  # view | edit | del | confirm
    link_id: int
    page: int = 0


class EditCB(CallbackData, prefix="edt"):
    """Field selection inside the edit dialog: ``edt:<field>:<link_id>``."""

    field: str  # title | category | url | expiry
    link_id: int


class CatCB(CallbackData, prefix="cat"):
    """Category choice during editing: ``cat:<category>:<link_id>``."""

    category: str
    link_id: int


class ExpCB(CallbackData, prefix="exp"):
    """Expiry presets: ``exp:<preset>:<link_id>`` (preset: none|1h|1d|7d|30d)."""

    preset: str
    link_id: int


# Simple action callbacks (validated with F.data == ... in handlers).
CB_NEW_LINK = "new_link"
CB_STATS = "stats"
CB_MENU = "to_menu"


# ---------------------------------------------------------------------------
# Main menu / list keyboards
# ---------------------------------------------------------------------------


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Compact main menu shown after /start."""
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ لینک جدید", callback_data=CB_NEW_LINK)
    kb.button(text="📋 لینک‌های من", callback_data=PageCB(page=0).pack())
    kb.button(text="📊 آمار", callback_data=CB_STATS)
    kb.adjust(2, 1)
    return kb.as_markup()


def links_list_keyboard(links: list["Link"], page: int, has_next: bool) -> InlineKeyboardMarkup:
    """
    Keyboard for the "My links" list: one row per link plus a pager row.

    Each row opens the link's detail view.  Only the *short* URL is ever
    exposed as an external URL button - never the original URL.
    """
    kb = InlineKeyboardBuilder()
    for link in links:
        label = (link.title or link.short_code)[:48]
        kb.button(
            text=f"🔗 {label}",
            callback_data=LinkCB(action="view", link_id=link.id, page=page).pack(),
        )

    # Pager row ----------------------------------------------------------
    if page > 0 or has_next:
        if page > 0:
            kb.button(text="⬅️ قبلی", callback_data=PageCB(page=page - 1).pack())
        if has_next:
            kb.button(text="بعدی ➡️", callback_data=PageCB(page=page + 1).pack())

    kb.button(text="🏠 منو", callback_data=CB_MENU)
    kb.adjust(1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 1)
    return kb.as_markup()


def link_view_keyboard(link_id: int, page: int, base_url: str, short_code: str) -> InlineKeyboardMarkup:
    """
    Detail view for a single link.

    The single external button opens the **short URL** (clicks are counted
    by the web server).  The original URL is shown as plain escaped text
    in the message body only, never as a tappable URL button.
    """
    kb = InlineKeyboardBuilder()
    # Open in browser -> short URL only.
    kb.button(
        text="🌐 باز کردن لینک کوتاه",
        url=f"{base_url.rstrip('/')}/{short_code}",
    )
    kb.button(
        text="✏️ ویرایش",
        callback_data=LinkCB(action="edit", link_id=link_id, page=page).pack(),
    )
    kb.button(
        text="🗑 حذف",
        callback_data=LinkCB(action="del", link_id=link_id, page=page).pack(),
    )
    if page > 0:
        kb.button(text="⬅️ برگشت", callback_data=PageCB(page=page).pack())
    else:
        kb.button(text="⬅️ برگشت", callback_data=PageCB(page=0).pack())
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def edit_menu_keyboard(link_id: int) -> InlineKeyboardMarkup:
    """Choose which field of the link to edit."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🏷 دسته‌بندی", callback_data=EditCB(field="category", link_id=link_id).pack())
    kb.button(text="⏰ انقضا", callback_data=EditCB(field="expiry", link_id=link_id).pack())
    kb.button(text="📝 عنوان", callback_data=EditCB(field="title", link_id=link_id).pack())
    kb.button(text="🔗 آدرس", callback_data=EditCB(field="url", link_id=link_id).pack())
    kb.button(text="✖️ انصراف", callback_data="edit_cancel")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def category_keyboard(link_id: int) -> InlineKeyboardMarkup:
    """One button per known category + a cancel button."""
    from services.link_service import CATEGORIES

    kb = InlineKeyboardBuilder()
    for cat in CATEGORIES:
        kb.button(text=cat, callback_data=CatCB(category=cat, link_id=link_id).pack())
    kb.button(text="✖️ انصراف", callback_data="edit_cancel")
    kb.adjust(2)
    return kb.as_markup()


def expiry_keyboard(link_id: int) -> InlineKeyboardMarkup:
    """Quick expiry presets; 'None' removes expiration."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🚫 بدون انقضا", callback_data=ExpCB(preset="none", link_id=link_id).pack())
    kb.button(text="۱ ساعت", callback_data=ExpCB(preset="1h", link_id=link_id).pack())
    kb.button(text="۱ روز", callback_data=ExpCB(preset="1d", link_id=link_id).pack())
    kb.button(text="۷ روز", callback_data=ExpCB(preset="7d", link_id=link_id).pack())
    kb.button(text="۳۰ روز", callback_data=ExpCB(preset="30d", link_id=link_id).pack())
    kb.button(text="✖️ انصراف", callback_data="edit_cancel")
    kb.adjust(2, 2, 1, 1)
    return kb.as_markup()


def delete_confirm_keyboard(link_id: int, page: int) -> InlineKeyboardMarkup:
    """Yes/no confirmation before the destructive delete."""
    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ بله، حذفش کن",
        callback_data=LinkCB(action="confirm", link_id=link_id, page=page).pack(),
    )
    kb.button(
        text="❌ نه، نگهش دار",
        callback_data=LinkCB(action="view", link_id=link_id, page=page).pack(),
    )
    kb.adjust(2)
    return kb.as_markup()
