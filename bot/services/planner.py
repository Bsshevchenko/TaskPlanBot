import anthropic
from bot.config import settings

_BLOCKS = """
<b>👨‍💻 Инженерные задачи</b>
Разработка, код-ревью, архитектура, технические решения, дебаг.

• Задача

<b>🤝 Тимлидские задачи</b>
1:1, онбординг, ревью сотрудников, найм, командные процессы, коммуникация с другими командами.

• Задача

<b>🚀 Развитие</b>
Проекты, улучшения, новые инициативы — то, что двигает вперёд.

• Задача

<b>📚 Саморазвитие</b>
Обучение, книги, курсы, навыки.

• Задача

<b>🗂 Организационные моменты</b>
Административное, бытовое, договорённости.

• Задача

<b>📌 Другое</b>
Всё остальное.

• Задача
"""

GENERATE_PROMPT = f"""Ты — персональный коуч по продуктивности. Пользователь — тимлид.

Пользователь даст тебе транскрипции голосовых заметок с задачами, целями и идеями.

Твоя задача:
1. Извлечь все конкретные действия.
2. Убрать дублирование, уточнить размытые формулировки.
3. Распределить задачи по блокам. Блоки без задач не включай.

Формат — строго HTML для Telegram, только теги <b> и <i>:
{_BLOCKS}
Каждая задача — отдельный абзац (пустая строка между задачами). Будь конкретным и лаконичным. Никакого Markdown."""

ADD_PROMPT = f"""Ты — персональный коуч по продуктивности. Пользователь — тимлид.

Пользователь даст тебе существующий план и новые голосовые заметки.

Твоя задача:
- Извлечь конкретные действия из новых заметок.
- Добавить их в нужные блоки существующего плана.
- Не изменять и не удалять уже существующие задачи.
- Не дублировать задачи, которые уже есть в плане.
- Вернуть полный обновлённый план в том же HTML-формате.

Блоки плана:
{_BLOCKS}
Только теги <b> и <i>. Никакого Markdown."""


async def _call_claude(system: str, user_content: str) -> str:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        thinking={"type": "enabled", "budget_tokens": 2000},
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    return next(b.text for b in response.content if b.type == "text")


async def generate_plan(transcription_texts: list[str]) -> str:
    combined = "\n\n---\n\n".join(
        f"[Заметка {i + 1}]: {text}" for i, text in enumerate(transcription_texts)
    )
    return await _call_claude(GENERATE_PROMPT, combined)


async def suggest_daily_tasks(tasks: list[dict], day_name: str, done_texts: list[str], yesterday_texts: list[str]) -> dict:
    """Возвращает {order: [0,2,1,...], reason: '...'}"""
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    task_list = "\n".join(f"{i + 1}. [{t['emoji']}] {t['text']}" for i, t in enumerate(tasks))
    done_block = "\n".join(f"- {t}" for t in done_texts) if done_texts else "ничего"
    yesterday_block = "\n".join(f"- {t}" for t in yesterday_texts) if yesterday_texts else "ничего"

    prompt = f"""Сегодня {day_name}. Помоги тимлиду спланировать рабочий день.

Все задачи из недельного плана:
{task_list}

Выполнено ранее на этой неделе:
{done_block}

Не завершено вчера (перенести на сегодня):
{yesterday_block}

Предложи оптимальный порядок ВСЕХ задач на сегодня. Вчерашние незакрытые — в начало. Соблюдай баланс: приоритет инженерным и тимлидским, но включи хотя бы одну задачу на развитие если есть.

Ответь строго в формате:
ORDER: 1, 3, 2, 5, 4
REASON: Одно предложение с объяснением логики."""

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    result: dict = {"order": list(range(len(tasks))), "reason": ""}

    for line in text.split("\n"):
        if line.startswith("ORDER:"):
            raw = line.replace("ORDER:", "").strip()
            try:
                result["order"] = [int(x.strip()) - 1 for x in raw.split(",") if x.strip().isdigit()]
            except Exception:
                pass
        elif line.startswith("REASON:"):
            result["reason"] = line.replace("REASON:", "").strip()

    return result


async def generate_weekly_report(plan_html: str, sessions: list[dict]) -> str:
    import json as _json
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    done, skipped = [], []
    for s in sessions:
        for t in _json.loads(s["tasks"]):
            if t["status"] == "done":
                done.append(f"{t['emoji']} {t['text']}")
            elif t["status"] == "skipped":
                skipped.append(f"{t['emoji']} {t['text']}")

    done_block = "\n".join(done) if done else "ничего"
    skipped_block = "\n".join(skipped) if skipped else "ничего"

    prompt = f"""Итоги рабочей недели тимлида.

Недельный план:
{plan_html}

Выполнено:
{done_block}

Пропущено:
{skipped_block}

Дай честный анализ: что удалось, что не доделал, на что обратить внимание. Будь конкретным.
Используй HTML для Telegram (только <b> и <i>)."""

    response = await client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        thinking={"type": "enabled", "budget_tokens": 1000},
        system="Ты — персональный коуч по продуктивности. Давай честную конструктивную обратную связь.",
        messages=[{"role": "user", "content": prompt}],
    )

    return next(b.text for b in response.content if b.type == "text")


async def add_to_plan(existing_plan: str, new_transcription_texts: list[str]) -> str:
    combined_new = "\n\n---\n\n".join(
        f"[Новая заметка {i + 1}]: {text}" for i, text in enumerate(new_transcription_texts)
    )
    user_content = f"Существующий план:\n{existing_plan}\n\n===\n\nНовые заметки:\n{combined_new}"
    return await _call_claude(ADD_PROMPT, user_content)
