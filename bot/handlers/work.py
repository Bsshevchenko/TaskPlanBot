import json
import logging
from datetime import datetime, timedelta

import pytz
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import settings
from bot.services import planner, storage
from bot.states import WorkStates
from bot.utils.plan_renderer import extract_tasks_with_categories

logger = logging.getLogger(__name__)
router = Router()

MOSCOW_TZ = pytz.timezone("Europe/Moscow")
DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


# ── CallbackData ───────────────────────────────────────────────────────────────

class TaskToggle(CallbackData, prefix="wt"):
    idx: int

class StartWork(CallbackData, prefix="ws"):
    pass

class TaskDone(CallbackData, prefix="wdone"):
    pass

class TaskDefer(CallbackData, prefix="wdefer"):
    pass

class StartReWork(CallbackData, prefix="wsre"):
    pass


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_allowed(message: Message) -> bool:
    if settings.allowed_user_id is None:
        return True
    return message.from_user is not None and message.from_user.id == settings.allowed_user_id


def _today() -> str:
    return datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (datetime.now(MOSCOW_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")


def _day_name() -> str:
    return DAY_NAMES[datetime.now(MOSCOW_TZ).weekday()]


def _pending_count(tasks: list[dict]) -> int:
    return sum(1 for t in tasks if t["status"] == "pending")


def _next_pending_from(tasks: list[dict], start: int) -> int | None:
    """Ищет следующую pending-задачу начиная с start, циклически."""
    n = len(tasks)
    if n == 0:
        return None
    for offset in range(n):
        idx = (start + offset) % n
        if tasks[idx]["status"] == "pending":
            return idx
    return None


def _progress_line(tasks: list[dict]) -> str:
    cats: dict[str, dict] = {}
    for t in tasks:
        em = t["emoji"]
        cats.setdefault(em, {"done": 0, "total": 0})
        cats[em]["total"] += 1
        if t["status"] == "done":
            cats[em]["done"] += 1

    total_done = sum(1 for t in tasks if t["status"] == "done")
    cat_parts = [f"{em} {v['done']}/{v['total']}" for em, v in cats.items()]
    return f"{total_done}/{len(tasks)}  •  " + "  ".join(cat_parts)


def _selection_text(tasks: list[dict], selected: set[int]) -> str:
    now = datetime.now(MOSCOW_TZ)
    header = f"📅 <b>{_day_name()}, {now.strftime('%d.%m.%Y')}</b>"
    lines = [header, ""]
    for i, t in enumerate(tasks):
        mark = "✓" if i in selected else "○"
        lines.append(f"{mark} {t['emoji']} {i + 1}. {t['text']}")
    return "\n".join(lines)


def _selection_keyboard(n: int, selected: set[int]):
    builder = InlineKeyboardBuilder()
    for i in range(n):
        label = f"✓{i + 1}" if i in selected else str(i + 1)
        builder.button(text=label, callback_data=TaskToggle(idx=i))
    builder.adjust(5)
    builder.row()
    count = len(selected)
    builder.button(
        text=f"▶️ Начать день ({count})" if count else "▶️ Начать день",
        callback_data=StartWork(),
    )
    return builder.as_markup()


def _re_work_keyboard(n: int, selected: set[int]):
    builder = InlineKeyboardBuilder()
    for i in range(n):
        label = f"✓{i + 1}" if i in selected else str(i + 1)
        builder.button(text=label, callback_data=TaskToggle(idx=i))
    builder.adjust(5)
    builder.row()
    count = len(selected)
    builder.button(
        text=f"➕ Добавить ({count})" if count else "➕ Добавить",
        callback_data=StartReWork(),
    )
    return builder.as_markup()


def _task_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сделал", callback_data=TaskDone())
    builder.button(text="⏭ Отложить", callback_data=TaskDefer())
    return builder.as_markup()


async def _show_task(bot: Bot, chat_id: int, user_id: int, tasks: list[dict], date_str: str, display_idx: int) -> None:
    task = tasks[display_idx]
    pending = _pending_count(tasks)
    sent = await bot.send_message(
        chat_id,
        f"{task['emoji']} <b>{task['text']}</b>\n\n<i>{_progress_line(tasks)}  •  осталось: {pending}</i>",
        parse_mode="HTML",
        reply_markup=_task_keyboard(),
    )
    await storage.update_session_message_id(user_id, date_str, sent.message_id)
    await storage.update_session_display_idx(user_id, date_str, display_idx)


# ── /work ──────────────────────────────────────────────────────────────────────

@router.message(Command("work"))
async def cmd_work(message: Message, bot: Bot, state: FSMContext) -> None:
    if not _is_allowed(message):
        return

    user_id = message.from_user.id  # type: ignore[union-attr]
    today = _today()

    # Продолжить активную сессию
    session = await storage.get_work_session(user_id, today)
    if session:
        tasks = json.loads(session["tasks"])
        display_idx = _next_pending_from(tasks, session.get("display_idx", 0) or 0)
        if display_idx is None:
            done = sum(1 for t in tasks if t["status"] == "done")
            await message.reply(f"🎉 Все задачи на сегодня выполнены! ({done}/{len(tasks)})")
            return
        await message.reply("Продолжаем! 👇")
        await _show_task(bot, message.chat.id, user_id, tasks, today, display_idx)
        return

    # Нет сессии — запускаем выбор задач
    weekly_plan = await storage.get_week_plan(user_id)
    if not weekly_plan:
        await message.reply("Нет активного плана. Создай командой /plan")
        return

    all_tasks = extract_tasks_with_categories(weekly_plan["plan_html"])
    if not all_tasks:
        await message.reply("В плане нет задач.")
        return

    # Незавершённые вчера
    yesterday_session = await storage.get_work_session(user_id, _yesterday())
    yesterday_texts: list[str] = []
    if yesterday_session:
        for t in json.loads(yesterday_session["tasks"]):
            if t["status"] == "pending":
                yesterday_texts.append(t["text"])

    # Выполненные на этой неделе
    done_texts: list[str] = []
    for s in await storage.get_week_sessions(user_id):
        for t in json.loads(s["tasks"]):
            if t["status"] == "done":
                done_texts.append(t["text"])

    # Убираем уже выполненные задачи из списка выбора
    done_set = set(done_texts)
    all_tasks = [t for t in all_tasks if t["text"] not in done_set]
    if not all_tasks:
        await message.reply("🎉 Все задачи из плана уже выполнены на этой неделе!")
        return

    selected: set[int] = set()

    await state.set_state(WorkStates.selecting)
    await state.update_data(tasks=all_tasks, selected=list(selected))

    await message.reply(
        _selection_text(all_tasks, selected),
        reply_markup=_selection_keyboard(len(all_tasks), selected),
    )


# ── /re_work ───────────────────────────────────────────────────────────────────

@router.message(Command("re_work"))
async def cmd_re_work(message: Message, state: FSMContext) -> None:
    if not _is_allowed(message):
        return

    user_id = message.from_user.id  # type: ignore[union-attr]
    today = _today()

    session = await storage.get_work_session(user_id, today)
    if not session:
        await message.reply("Сначала запусти /work для планирования дня.")
        return

    weekly_plan = await storage.get_week_plan(user_id)
    if not weekly_plan:
        await message.reply("Нет активного плана.")
        return

    plan_tasks = extract_tasks_with_categories(weekly_plan["plan_html"])
    session_texts = {t["text"] for t in json.loads(session["tasks"])}

    new_tasks = [t for t in plan_tasks if t["text"] not in session_texts]
    if not new_tasks:
        await message.reply("Новых задач в плане нет.")
        return

    selected: set[int] = set()  # ничего не выбрано по умолчанию
    await state.set_state(WorkStates.selecting)
    await state.update_data(tasks=new_tasks, selected=list(selected), mode="re_work")

    await message.reply(
        _selection_text(new_tasks, selected),
        reply_markup=_re_work_keyboard(len(new_tasks), selected),
    )


# ── /clear_work ───────────────────────────────────────────────────────────────

@router.message(Command("clear_work"))
async def cmd_clear_work(message: Message, state: FSMContext) -> None:
    if not _is_allowed(message):
        return

    user_id = message.from_user.id  # type: ignore[union-attr]
    today = _today()

    session = await storage.get_work_session(user_id, today)
    if not session:
        await message.reply("Сессии на сегодня нет.")
        return

    await storage.delete_work_session(user_id, today)
    await state.clear()
    await message.reply("🗑 Сессия на сегодня сброшена. Запусти /work чтобы начать заново.")


# ── /report ────────────────────────────────────────────────────────────────────

@router.message(Command("report"))
async def cmd_report(message: Message, bot: Bot) -> None:
    if not _is_allowed(message):
        return

    user_id = message.from_user.id  # type: ignore[union-attr]

    plan = await storage.get_week_plan(user_id)
    if not plan:
        await message.reply("Нет активного плана на эту неделю.")
        return

    sessions = await storage.get_week_sessions(user_id)
    if not sessions:
        await message.reply("На этой неделе не было рабочих сессий (/work).")
        return

    status_msg = await message.reply("⏳ Анализирую неделю...")
    await bot.send_chat_action(message.chat.id, "typing")

    try:
        report = await planner.generate_weekly_report(plan["plan_html"], sessions)
        await status_msg.delete()
        await message.answer(f"📊 <b>Итоги недели</b>\n\n{report}", parse_mode="HTML")
    except Exception as e:
        logger.error("report error: %s", e)
        await status_msg.edit_text("Не удалось сгенерировать отчёт. Попробуй ещё раз.")


# ── Selection callbacks ────────────────────────────────────────────────────────

@router.callback_query(TaskToggle.filter(), WorkStates.selecting)
async def toggle_task(callback: CallbackQuery, callback_data: TaskToggle, state: FSMContext) -> None:
    data = await state.get_data()
    tasks = data["tasks"]
    selected = set(data["selected"])
    mode = data.get("mode", "work")

    idx = callback_data.idx
    selected.discard(idx) if idx in selected else selected.add(idx)
    await state.update_data(selected=list(selected))

    kb = _re_work_keyboard(len(tasks), selected) if mode == "re_work" else _selection_keyboard(len(tasks), selected)
    await callback.message.edit_text(  # type: ignore[union-attr]
        _selection_text(tasks, selected),
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(StartWork.filter(), WorkStates.selecting)
async def start_session(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    tasks: list[dict] = data["tasks"]
    selected = set(data["selected"])

    if not selected:
        await callback.answer("Выбери хотя бы одну задачу!", show_alert=True)
        return

    session_tasks = [{**tasks[i], "status": "pending"} for i in sorted(selected)]

    user_id = callback.from_user.id
    today = _today()

    await storage.create_work_session(user_id, today, session_tasks)
    await state.clear()
    await callback.message.delete()  # type: ignore[union-attr]

    await _show_task(bot, callback.message.chat.id, user_id, session_tasks, today, 0)  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(StartReWork.filter(), WorkStates.selecting)
async def start_re_work(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tasks: list[dict] = data["tasks"]
    selected = set(data["selected"])

    if not selected:
        await callback.answer("Выбери хотя бы одну задачу!", show_alert=True)
        return

    user_id = callback.from_user.id
    today = _today()

    session = await storage.get_work_session(user_id, today)
    if not session:
        await callback.answer("Сессия не найдена.", show_alert=True)
        return

    session_tasks = json.loads(session["tasks"])
    added = [{**tasks[i], "status": "pending"} for i in sorted(selected)]
    session_tasks.extend(added)

    await storage.update_session_tasks(user_id, today, session_tasks)
    await state.clear()
    await callback.message.delete()  # type: ignore[union-attr]

    await callback.message.answer(  # type: ignore[union-attr]
        f"Добавлено <b>{len(added)}</b> задач в сегодняшний список:\n\n"
        + "\n".join(f"• {t['emoji']} {t['text']}" for t in added)
    )
    await callback.answer()


# ── Task action callbacks ──────────────────────────────────────────────────────

@router.callback_query(TaskDone.filter())
async def task_done(callback: CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    today = _today()
    session = await storage.get_work_session(user_id, today)
    if not session:
        await callback.answer()
        return

    tasks = json.loads(session["tasks"])
    display_idx = session.get("display_idx", 0) or 0

    tasks[display_idx]["status"] = "done"
    await storage.update_session_tasks(user_id, today, tasks)
    await callback.message.delete()  # type: ignore[union-attr]

    next_idx = _next_pending_from(tasks, (display_idx + 1) % len(tasks))
    if next_idx is None:
        done = sum(1 for t in tasks if t["status"] == "done")
        await bot.send_message(
            callback.message.chat.id,  # type: ignore[union-attr]
            f"🎉 <b>Все задачи выполнены!</b>\n\n{_progress_line(tasks)}\n\n/report — итоги недели",
            parse_mode="HTML",
        )
    else:
        await _show_task(bot, callback.message.chat.id, user_id, tasks, today, next_idx)  # type: ignore[union-attr]

    await callback.answer()


@router.callback_query(TaskDefer.filter())
async def task_defer(callback: CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    today = _today()
    session = await storage.get_work_session(user_id, today)
    if not session:
        await callback.answer()
        return

    tasks = json.loads(session["tasks"])
    display_idx = session.get("display_idx", 0) or 0

    # Циклически переходим к следующей pending-задаче (текущая остаётся pending)
    next_idx = _next_pending_from(tasks, (display_idx + 1) % len(tasks))
    if next_idx is None or next_idx == display_idx:
        await callback.answer("Больше задач нет", show_alert=True)
        return

    await callback.message.delete()  # type: ignore[union-attr]
    await _show_task(bot, callback.message.chat.id, user_id, tasks, today, next_idx)  # type: ignore[union-attr]
    await callback.answer()
