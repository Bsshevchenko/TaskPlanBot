import json
import logging
from aiogram import Router, Bot, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import settings
from bot.services import planner, storage
from bot.states import PlanStates
from bot.utils.formatting import split_message

logger = logging.getLogger(__name__)
router = Router()


class PlanAction(CallbackData, prefix="plan"):
    action: str  # "edit"


def _is_allowed(message: Message) -> bool:
    if settings.allowed_user_id is None:
        return True
    return message.from_user is not None and message.from_user.id == settings.allowed_user_id


def _edit_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать", callback_data=PlanAction(action="edit"))
    return builder.as_markup()


async def _delete_messages(bot: Bot, chat_id: int, message_ids: list[int]) -> None:
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass


async def _send_plan(bot: Bot, chat_id: int, plan_html: str) -> list[int]:
    """Отправляет план чанками, возвращает список message_id."""
    chunks = split_message(plan_html)
    sent_ids = []
    for i, chunk in enumerate(chunks):
        markup = _edit_keyboard() if i == len(chunks) - 1 else None
        sent = await bot.send_message(chat_id, chunk, parse_mode="HTML", reply_markup=markup)
        sent_ids.append(sent.message_id)
    return sent_ids


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _is_allowed(message):
        return
    await message.reply(
        "👋 Привет! Я помогу тебе планировать задачи на неделю.\n\n"
        "Отправляй голосовые заметки с задачами и идеями, а когда будешь готов — "
        "используй /plan для генерации плана.\n\n"
        "/history — заметки за эту неделю\n"
        "/plan — показать или обновить план\n"
        "/re_plan — полностью переделать план заново"
    )


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    if not _is_allowed(message):
        return

    user_id = message.from_user.id  # type: ignore[union-attr]
    rows = await storage.get_week_transcriptions(user_id)

    if not rows:
        await message.reply("На этой неделе нет сохранённых заметок.")
        return

    lines = []
    for i, row in enumerate(rows, 1):
        created = row["created_at"][:16]
        lines.append(f"<b>{i}. {created}</b>\n{row['text']}")

    text = "\n\n".join(lines)
    for chunk in split_message(f"📋 <b>Заметки за эту неделю:</b>\n\n{text}"):
        await message.reply(chunk, parse_mode="HTML")


@router.message(Command("plan"))
async def cmd_plan(message: Message, bot: Bot, state: FSMContext) -> None:
    if not _is_allowed(message):
        return

    user_id = message.from_user.id  # type: ignore[union-attr]
    rows = await storage.get_week_transcriptions(user_id)
    existing_plan = await storage.get_week_plan(user_id)

    if not rows and not existing_plan:
        await message.reply("На этой неделе нет сохранённых заметок. Отправь голосовые сначала!")
        return

    current_ids = {row["id"] for row in rows}
    transcription_ids = [row["id"] for row in rows]

    # Сценарий 1: план есть, новых заметок нет — показываем из БД
    if existing_plan:
        used_ids = set(json.loads(existing_plan["transcription_ids"]))
        if current_ids == used_ids:
            sent_ids = await _send_plan(bot, message.chat.id, existing_plan["plan_html"])
            await state.update_data(plan_html=existing_plan["plan_html"], plan_message_ids=sent_ids)
            return

    # Сценарий 2: план есть + есть новые заметки — добавляем к существующему
    if existing_plan:
        used_ids = set(json.loads(existing_plan["transcription_ids"]))
        new_ids = current_ids - used_ids
        new_texts = [row["text"] for row in rows if row["id"] in new_ids]

        status_msg = await message.reply("⏳ Добавляю новые задачи в план...")
        await bot.send_chat_action(message.chat.id, "typing")
        try:
            plan_html = await planner.add_to_plan(existing_plan["plan_html"], new_texts)
        except Exception as e:
            logger.error("Planner error: %s", e)
            await status_msg.edit_text("Не удалось обновить план. Попробуй ещё раз.")
            return

    # Сценарий 3: плана нет — генерируем с нуля
    else:
        status_msg = await message.reply("⏳ Генерирую план задач, подожди немного...")
        await bot.send_chat_action(message.chat.id, "typing")
        transcription_texts = [row["text"] for row in rows]
        try:
            plan_html = await planner.generate_plan(transcription_texts)
        except Exception as e:
            logger.error("Planner error: %s", e)
            await status_msg.edit_text("Не удалось сгенерировать план. Попробуй ещё раз.")
            return

    await storage.upsert_week_plan(user_id, transcription_ids, plan_html)
    await status_msg.delete()

    sent_ids = await _send_plan(bot, message.chat.id, plan_html)
    await state.update_data(plan_html=plan_html, plan_message_ids=sent_ids)


@router.message(Command("re_plan"))
async def cmd_re_plan(message: Message, bot: Bot, state: FSMContext) -> None:
    if not _is_allowed(message):
        return

    user_id = message.from_user.id  # type: ignore[union-attr]
    existing_plan = await storage.get_week_plan(user_id)

    if not existing_plan:
        await message.reply("На этой неделе ещё нет плана. Используй /plan.")
        return

    used_ids = json.loads(existing_plan["transcription_ids"])
    if not used_ids:
        await message.reply("В плане нет связанных заметок.")
        return

    status_msg = await message.reply("⏳ Переделываю план заново...")
    await bot.send_chat_action(message.chat.id, "typing")

    rows = await storage.get_transcriptions_by_ids(used_ids)
    transcription_texts = [row["text"] for row in rows]

    try:
        plan_html = await planner.generate_plan(transcription_texts)
    except Exception as e:
        logger.error("Planner re_plan error: %s", e)
        await status_msg.edit_text("Не удалось переделать план. Попробуй ещё раз.")
        return

    await storage.upsert_week_plan(user_id, used_ids, plan_html)
    await status_msg.delete()

    sent_ids = await _send_plan(bot, message.chat.id, plan_html)
    await state.update_data(plan_html=plan_html, plan_message_ids=sent_ids)


@router.callback_query(PlanAction.filter(F.action == "edit"))
async def edit_plan_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
    prompt = await callback.message.answer(  # type: ignore[union-attr]
        "Отправь исправленный план. Можешь скопировать текст выше, отредактировать и прислать обратно."
    )
    await state.set_state(PlanStates.waiting_for_edit)
    await state.update_data(edit_prompt_message_id=prompt.message_id)
    await callback.answer()


@router.message(PlanStates.waiting_for_edit, F.text)
async def save_edited_plan(message: Message, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    plan_message_ids: list[int] = data.get("plan_message_ids", [])
    edit_prompt_id: int | None = data.get("edit_prompt_message_id")
    new_plan = message.text or ""
    chat_id = message.chat.id
    user_id = message.from_user.id  # type: ignore[union-attr]

    await state.clear()

    # Удаляем: старый план + промпт + сообщение пользователя
    to_delete = plan_message_ids[:]
    if edit_prompt_id:
        to_delete.append(edit_prompt_id)
    to_delete.append(message.message_id)
    await _delete_messages(bot, chat_id, to_delete)

    # Сохраняем в БД и показываем обновлённый план
    await storage.update_week_plan_html(user_id, new_plan)

    sent_ids = await _send_plan(bot, chat_id, new_plan)
    await state.update_data(plan_html=new_plan, plan_message_ids=sent_ids)
