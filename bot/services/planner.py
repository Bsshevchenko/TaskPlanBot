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


async def add_to_plan(existing_plan: str, new_transcription_texts: list[str]) -> str:
    combined_new = "\n\n---\n\n".join(
        f"[Новая заметка {i + 1}]: {text}" for i, text in enumerate(new_transcription_texts)
    )
    user_content = f"Существующий план:\n{existing_plan}\n\n===\n\nНовые заметки:\n{combined_new}"
    return await _call_claude(ADD_PROMPT, user_content)
