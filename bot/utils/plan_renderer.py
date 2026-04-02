from aiogram.utils.keyboard import InlineKeyboardBuilder

SECTION_EMOJI_TO_CATEGORY = {
    "👨‍💻": "engineering",
    "🤝": "tl",
    "🚀": "development",
    "📚": "self_dev",
    "🗂": "org",
    "📌": "other",
}


def render_plan_with_done(plan_html: str, done_texts: set[str]) -> str:
    """Возвращает план с зачёркнутыми выполненными задачами."""
    lines = []
    for line in plan_html.split("\n"):
        stripped = line.strip()
        if stripped.startswith("• "):
            task_text = stripped[2:]
            if task_text in done_texts:
                line = line.replace(f"• {task_text}", f"• <s>{task_text}</s>")
        lines.append(line)
    return "\n".join(lines)


def extract_tasks_with_categories(plan_html: str) -> list[dict]:
    """Возвращает список задач с категориями, сохраняя порядок из плана."""
    tasks = []
    current_category = "other"
    current_emoji = "📌"

    for line in plan_html.split("\n"):
        stripped = line.strip()
        if stripped.startswith("<b>"):
            for emoji, cat in SECTION_EMOJI_TO_CATEGORY.items():
                if emoji in stripped:
                    current_category = cat
                    current_emoji = emoji
                    break
        elif stripped.startswith("• "):
            tasks.append({
                "text": stripped[2:],
                "category": current_category,
                "emoji": current_emoji,
            })

    return tasks
