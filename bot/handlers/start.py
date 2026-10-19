"""
``handlers.start`` - /start, /help, /cancel and main-menu callbacks.

Contract:

* ``/start`` and ``/cancel`` always clear the FSM state;
* ``/start`` (and the "Menu" button) render the main panel;
* deep context (e.g. pressing buttons after a crash) never breaks -
  every branch answers the callback query.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.inline import main_menu_keyboard
from database.models import User

router = Router()

WELCOME_TEXT = (
    "👋 <b>به لینک‌منیجر خوش اومدی!</b>\n\n"
    "هر آدرسی بفرستی، فوری برات کوتاهش می‌کنم.\n\n"
    "<b>دستورات:</b>\n"
    "/new - ساخت لینک کوتاه جدید\n"
    "/links - دیدن و مدیریت لینک‌هات\n"
    "/stats - دیدن آمار لینک‌هات\n"
    "/cancel - لغو عملیات جاری\n\n"
    "یا از دکمه‌های زیر استفاده کن 👇"
)

CANCELLED_TEXT = "❌ لغو شد. برگشتیم به منوی اصلی."


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    db_user: User,
) -> None:
    """Entry point: reset any FSM state and show the main panel."""
    # Always clear leftover state so a stale dialog cannot block the menu.
    await state.clear()
    await message.answer(
        WELCOME_TEXT,
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Abort the current dialog and return to the panel."""
    await state.clear()
    await message.answer(CANCELLED_TEXT, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "to_menu")
async def cb_to_menu(query: CallbackQuery, state: FSMContext) -> None:
    """'Menu' inline button: clear state and re-render the panel."""
    await state.clear()
    if query.message is not None:
        try:
            await query.message.answer(
                "منوی اصلی:",
                reply_markup=main_menu_keyboard(),
            )
        except Exception:
            # The origin message may be inaccessible (too old) - ignore.
            pass
    await query.answer()


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Short help echoing the welcome text."""
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_keyboard())
