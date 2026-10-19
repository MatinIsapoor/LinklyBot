"""
``handlers.shorten`` - creating short links.

Flow:

* ``/new`` (or the "New link" button) puts the user into the
  ``ShortenFSM.waiting_url`` state and asks for a URL;
* any free-text message that looks like a URL (while not in FSM) is
  shortened immediately;
* the service layer performs validation and may raise ``ValueError`` -
  we convert that into friendly feedback without leaking internals.

Ownership is implicit: ``create`` is always called with ``db_user.id``.
"""

from __future__ import annotations

import html
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.filters.private import IsPrivate
from bot.keyboards.inline import CB_NEW_LINK, main_menu_keyboard
from config import config
from database.models import Link, User

router = Router()

# Very permissive URL heuristic - full validation belongs to the service.
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


class ShortenFSM(StatesGroup):
    """States for the "create link" dialog."""

    waiting_url = State()


def _shorten_success_text(link: Link) -> str:
    """Render the success card for a freshly created link."""
    raw_url = link.short_url(config.BASE_URL)
    href = html.escape(raw_url, quote=True)
    label = html.escape(raw_url)
    return (
        "✅ <b>لینک ساخته شد!</b>\n\n"
        f"🔗 لینک کوتاه: <a href=\"{href}\">{label}</a>\n"
        f"🏷 دسته‌بندی: <b>{html.escape(link.category)}</b>\n"
        f"🆔 شناسه: <code>{link.id}</code>\n\n"
        "بازش کن یا از بخش <b>لینک‌های من</b> مدیریتش کن."
    )


@router.message(IsPrivate(), Command("new"))
async def cmd_new(message: Message, state: FSMContext) -> None:
    """Start the create dialog: ask for the target URL."""
    await state.set_state(ShortenFSM.waiting_url)
    await message.answer(
        "📨 اون <b>آدرسی</b> که می‌خوای کوتاهش کنم رو بفرست.\n"
        "باید با <code>http://</code> یا <code>https://</code> شروع بشه.\n\n"
        "برای لغو /cancel رو بفرست.",
    )


@router.callback_query(F.data == CB_NEW_LINK)
async def cb_new(query: CallbackQuery, state: FSMContext) -> None:
    """'New link' inline button: same as /new, works from the panel."""
    await state.set_state(ShortenFSM.waiting_url)
    if query.message is not None:
        try:
            await query.message.answer(
                "📨 اون <b>آدرسی</b> که می‌خوای کوتاهش کنم رو بفرست.\n"
                "باید با <code>http://</code> یا <code>https://</code> شروع بشه.\n\n"
                "برای لغو /cancel رو بفرست.",
            )
        except Exception:
            pass
    await query.answer()


@router.message(ShortenFSM.waiting_url, F.text)
async def process_url(message: Message, state: FSMContext, link_service, db_user: User) -> None:
    """Receive the URL while in FSM and create the link."""
    text = (message.text or "").strip()
    if not _URL_RE.match(text):
        await message.answer(
            "⚠️ این آدرس معتبر به نظر نمی‌رسه.\n"
            "لطفا لینکی بفرست که با <code>http://</code> یا <code>https://</code> شروع بشه.\n"
            "یا برای لغو /cancel رو بفرست.",
        )
        return

    try:
        link = await link_service.create(user_id=db_user.id, url=text)
    except ValueError as exc:
        # Validation error surfaced by the service - show it safely.
        await message.answer(f"⚠️ نتونستم لینک رو بسازم: {html.escape(str(exc))}")
        return
    except Exception:
        # Generic failure - never leak exception details.
        await message.answer("⚠️ یه مشکلی پیش اومد موقع ساخت لینک. لطفا دوباره تلاش کن.")
        return

    await state.clear()
    await message.answer(
        _shorten_success_text(link),
        reply_markup=main_menu_keyboard(),
        link_preview_options={"is_disabled": True},
    )


@router.message(F.text.regexp(_URL_RE))
async def free_text_url(message: Message, link_service, db_user: User) -> None:
    """
    Free-text URL shortening outside any FSM state.

    Registered *after* the FSM handler, so while a dialog is active the
    FSM route wins; otherwise any pasted URL is shortened on the spot.
    """
    text = (message.text or "").strip()
    try:
        link = await link_service.create(user_id=db_user.id, url=text)
    except ValueError as exc:
        await message.answer(f"⚠️ نتونستم لینک رو بسازم: {html.escape(str(exc))}")
        return
    except Exception:
        await message.answer("⚠️ یه مشکلی پیش اومد موقع ساخت لینک. لطفا دوباره تلاش کن.")
        return

    await message.answer(
        _shorten_success_text(link),
        reply_markup=main_menu_keyboard(),
    )
