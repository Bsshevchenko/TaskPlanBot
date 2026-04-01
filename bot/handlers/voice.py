import io
import logging
from aiogram import Router, Bot, F
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import settings
from bot.services import transcription, storage
from bot.states import TranscriptionStates

logger = logging.getLogger(__name__)
router = Router()


class TranscriptionAction(CallbackData, prefix="trans"):
    action: str  # "confirm" | "edit"


def _is_allowed(message: Message) -> bool:
    if settings.allowed_user_id is None:
        return True
    return message.from_user is not None and message.from_user.id == settings.allowed_user_id


def _build_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=TranscriptionAction(action="confirm"))
    builder.button(text="✏️ Редактировать", callback_data=TranscriptionAction(action="edit"))
    return builder.as_markup()


@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot, state: FSMContext) -> None:
    if not _is_allowed(message):
        return

    voice = message.voice
    if voice is None:
        return

    if voice.file_size and voice.file_size > 20_000_000:
        await message.reply("Файл слишком большой (лимит 20 МБ).")
        return

    await bot.send_chat_action(message.chat.id, ChatAction.RECORD_VOICE)

    file = await bot.get_file(voice.file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, buf)  # type: ignore[arg-type]
    audio_bytes = buf.getvalue()

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    try:
        text = await transcription.transcribe_voice(audio_bytes)
    except Exception as e:
        logger.error("Transcription error: %s", e)
        await message.reply("Не удалось расшифровать голосовое. Попробуй ещё раз.")
        return

    transcription_msg = await message.reply(
        f"<b>Транскрипция:</b>\n\n{text}",
        reply_markup=_build_keyboard(),
    )

    await state.update_data(
        text=text,
        file_id=voice.file_id,
        duration=voice.duration,
        transcription_message_id=transcription_msg.message_id,
    )


@router.callback_query(TranscriptionAction.filter(F.action == "confirm"))
async def confirm_transcription(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    text = data.get("text", "")
    file_id = data.get("file_id", "")
    duration = data.get("duration")

    user_id = callback.from_user.id
    await storage.save_transcription(user_id, file_id, text, duration)
    await state.clear()

    await callback.message.edit_text(  # type: ignore[union-attr]
        "✅ Сохранено",
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(TranscriptionAction.filter(F.action == "edit"))
async def edit_transcription(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
    prompt = await callback.message.answer("Отправь исправленный текст:")  # type: ignore[union-attr]
    await state.set_state(TranscriptionStates.waiting_for_edit)
    await state.update_data(edit_prompt_message_id=prompt.message_id)
    await callback.answer()


@router.message(TranscriptionStates.waiting_for_edit, F.text)
async def save_edited_transcription(message: Message, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    file_id = data.get("file_id", "")
    duration = data.get("duration")
    transcription_message_id: int | None = data.get("transcription_message_id")
    edit_prompt_id: int | None = data.get("edit_prompt_message_id")

    user_id = message.from_user.id  # type: ignore[union-attr]
    chat_id = message.chat.id

    await storage.save_transcription(user_id, file_id, message.text or "", duration)
    await state.clear()

    # Удаляем: транскрипцию + промпт + сообщение пользователя
    for mid in filter(None, [transcription_message_id, edit_prompt_id, message.message_id]):
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass

    await bot.send_message(chat_id, "✅ Сохранено")
