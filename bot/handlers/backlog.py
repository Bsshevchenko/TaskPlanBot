import logging

from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import settings
from bot.services import storage
from bot.services.transcription import transcribe_voice
from bot.states import BacklogStates

logger = logging.getLogger(__name__)
router = Router()


# ── CallbackData ───────────────────────────────────────────────────────────────

class BacklogConfirm(CallbackData, prefix="blc"):
    pass

class BacklogEditBtn(CallbackData, prefix="ble"):
    pass

class BacklogDone(CallbackData, prefix="bld"):
    item_id: int

class BacklogDelete(CallbackData, prefix="blx"):
    item_id: int


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_allowed(message: Message) -> bool:
    if settings.allowed_user_id is None:
        return True
    return message.from_user is not None and message.from_user.id == settings.allowed_user_id


def _backlog_keyboard(items: list[dict]):
    builder = InlineKeyboardBuilder()
    for n, item in enumerate(items, 1):
        builder.button(text=f"✅ {n}", callback_data=BacklogDone(item_id=item["id"]))
        builder.button(text=f"🗑 {n}", callback_data=BacklogDelete(item_id=item["id"]))
    builder.adjust(2)
    return builder.as_markup()


def _backlog_text(items: list[dict]) -> str:
    lines = [f"📋 <b>Бэклог ({len(items)} задач)</b>", ""]
    for n, item in enumerate(items, 1):
        lines.append(f"{n}. {item['text']}")
    return "\n".join(lines)


async def _refresh_backlog(callback: CallbackQuery, user_id: int) -> None:
    items = await storage.get_backlog_items(user_id)
    if not items:
        await callback.message.edit_text("📋 Бэклог пуст.")  # type: ignore[union-attr]
        return
    await callback.message.edit_text(  # type: ignore[union-attr]
        _backlog_text(items),
        reply_markup=_backlog_keyboard(items),
    )


# ── /add_backlog ───────────────────────────────────────────────────────────────

@router.message(Command("add_backlog"))
async def cmd_add_backlog(message: Message, state: FSMContext) -> None:
    if not _is_allowed(message):
        return
    await state.set_state(BacklogStates.waiting_for_voice)
    await message.reply("🎙 Записывай голосовое — добавлю в бэклог.")


@router.message(F.voice, BacklogStates.waiting_for_voice)
async def backlog_voice(message: Message, bot: Bot, state: FSMContext) -> None:
    status_msg = await message.reply("⏳ Транскрибирую...")

    voice = message.voice  # type: ignore[union-attr]
    file = await bot.get_file(voice.file_id)
    bio = await bot.download_file(file.file_path)  # type: ignore[arg-type]
    audio_bytes = bio.read()  # type: ignore[union-attr]

    try:
        text = await transcribe_voice(audio_bytes, "voice.ogg")
    except Exception as e:
        logger.error("Backlog transcription error: %s", e)
        await status_msg.edit_text("Не удалось транскрибировать. Попробуй ещё раз.")
        return

    await status_msg.delete()

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Добавить в бэклог", callback_data=BacklogConfirm())
    builder.button(text="✏️ Редактировать", callback_data=BacklogEditBtn())
    builder.adjust(2)

    sent = await message.answer(f"📝 <i>{text}</i>", reply_markup=builder.as_markup())

    await state.set_state(BacklogStates.confirming)
    await state.update_data(text=text, transcription_message_id=sent.message_id)


@router.callback_query(BacklogConfirm.filter(), BacklogStates.confirming)
async def backlog_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await storage.add_backlog_item(callback.from_user.id, data["text"])
    await state.clear()
    await callback.message.edit_text("✅ Добавлено в бэклог.")  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(BacklogEditBtn.filter(), BacklogStates.confirming)
async def backlog_edit_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    prompt = await callback.message.answer("Введи исправленный текст:")  # type: ignore[union-attr]
    await state.update_data(edit_prompt_message_id=prompt.message_id)
    await state.set_state(BacklogStates.waiting_for_edit)
    await callback.answer()


@router.message(F.text, BacklogStates.waiting_for_edit)
async def backlog_edit_save(message: Message, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    await storage.add_backlog_item(message.from_user.id, message.text)  # type: ignore[arg-type, union-attr]

    for key in ("transcription_message_id", "edit_prompt_message_id"):
        msg_id = data.get(key)
        if msg_id:
            try:
                await bot.delete_message(message.chat.id, msg_id)
            except Exception:
                pass
    await message.delete()

    await state.clear()
    await bot.send_message(message.chat.id, "✅ Добавлено в бэклог.")


# ── /backlog ───────────────────────────────────────────────────────────────────

@router.message(Command("backlog"))
async def cmd_backlog(message: Message) -> None:
    if not _is_allowed(message):
        return

    user_id = message.from_user.id  # type: ignore[union-attr]
    items = await storage.get_backlog_items(user_id)

    if not items:
        await message.reply("📋 Бэклог пуст.")
        return

    await message.reply(_backlog_text(items), reply_markup=_backlog_keyboard(items))


@router.callback_query(BacklogDone.filter())
async def backlog_item_done(callback: CallbackQuery, callback_data: BacklogDone) -> None:
    await storage.update_backlog_status(callback_data.item_id, callback.from_user.id, "done")
    await _refresh_backlog(callback, callback.from_user.id)
    await callback.answer("Отмечено ✅")


@router.callback_query(BacklogDelete.filter())
async def backlog_item_delete(callback: CallbackQuery, callback_data: BacklogDelete) -> None:
    await storage.delete_backlog_item(callback_data.item_id, callback.from_user.id)
    await _refresh_backlog(callback, callback.from_user.id)
    await callback.answer("Удалено 🗑")
