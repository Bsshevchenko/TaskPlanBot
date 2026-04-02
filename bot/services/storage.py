import json
from datetime import datetime, timedelta
import aiosqlite
from bot.config import settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transcriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    file_id    TEXT    NOT NULL,
    text       TEXT    NOT NULL,
    duration   INTEGER,
    created_at DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_plans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    week_start        TEXT    NOT NULL,
    transcription_ids TEXT    NOT NULL,
    plan_html         TEXT    NOT NULL,
    created_at        DATETIME DEFAULT (datetime('now')),
    updated_at        DATETIME DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transcriptions_user
    ON transcriptions(user_id, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_plans_user_week
    ON task_plans(user_id, week_start);

CREATE TABLE IF NOT EXISTS work_sessions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                 INTEGER NOT NULL,
    date                    TEXT    NOT NULL,
    tasks                   TEXT    NOT NULL DEFAULT '[]',
    display_idx             INTEGER NOT NULL DEFAULT 0,
    current_task_message_id INTEGER,
    created_at              DATETIME DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_user_date
    ON work_sessions(user_id, date);

CREATE TABLE IF NOT EXISTS backlog_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    text       TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending',
    created_at DATETIME DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_backlog_user
    ON backlog_items(user_id, status);

PRAGMA journal_mode=WAL;
"""


def _week_start_str() -> str:
    """Дата понедельника текущей недели в формате YYYY-MM-DD."""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d")


async def init_db() -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


async def save_transcription(user_id: int, file_id: str, text: str, duration: int | None) -> int:
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute(
            "INSERT INTO transcriptions (user_id, file_id, text, duration) VALUES (?, ?, ?, ?)",
            (user_id, file_id, text, duration),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def get_week_transcriptions(user_id: int) -> list[dict]:
    week_start = _week_start_str()
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM transcriptions WHERE user_id = ? AND created_at >= ? ORDER BY created_at ASC",
            (user_id, week_start),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def upsert_week_plan(user_id: int, transcription_ids: list[int], plan_html: str) -> None:
    week_start = _week_start_str()
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            """
            INSERT INTO task_plans (user_id, week_start, transcription_ids, plan_html)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, week_start) DO UPDATE SET
                transcription_ids = excluded.transcription_ids,
                plan_html         = excluded.plan_html,
                updated_at        = datetime('now')
            """,
            (user_id, week_start, json.dumps(transcription_ids), plan_html),
        )
        await db.commit()


async def get_week_plan(user_id: int) -> dict | None:
    week_start = _week_start_str()
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM task_plans WHERE user_id = ? AND week_start = ?",
            (user_id, week_start),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_week_plan_html(user_id: int, plan_html: str) -> None:
    week_start = _week_start_str()
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            """
            UPDATE task_plans SET plan_html = ?, updated_at = datetime('now')
            WHERE user_id = ? AND week_start = ?
            """,
            (plan_html, user_id, week_start),
        )
        await db.commit()


async def get_transcriptions_by_ids(ids: list[int]) -> list[dict]:
    placeholders = ",".join("?" * len(ids))
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"SELECT * FROM transcriptions WHERE id IN ({placeholders}) ORDER BY created_at ASC",
            ids,
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_recent_transcriptions(user_id: int, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM transcriptions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_transcription_count(user_id: int) -> int:
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM transcriptions WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


# ── Work sessions ──────────────────────────────────────────────────────────────

async def get_work_session(user_id: int, date_str: str) -> dict | None:
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM work_sessions WHERE user_id = ? AND date = ?",
            (user_id, date_str),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def create_work_session(user_id: int, date_str: str, tasks: list[dict]) -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO work_sessions (user_id, date, tasks) VALUES (?, ?, ?)",
            (user_id, date_str, json.dumps(tasks, ensure_ascii=False)),
        )
        await db.commit()


async def update_session_tasks(user_id: int, date_str: str, tasks: list[dict]) -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            "UPDATE work_sessions SET tasks = ? WHERE user_id = ? AND date = ?",
            (json.dumps(tasks, ensure_ascii=False), user_id, date_str),
        )
        await db.commit()


async def update_session_display_idx(user_id: int, date_str: str, display_idx: int) -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            "UPDATE work_sessions SET display_idx = ? WHERE user_id = ? AND date = ?",
            (display_idx, user_id, date_str),
        )
        await db.commit()


async def update_session_message_id(user_id: int, date_str: str, message_id: int) -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            "UPDATE work_sessions SET current_task_message_id = ? WHERE user_id = ? AND date = ?",
            (message_id, user_id, date_str),
        )
        await db.commit()


async def delete_work_session(user_id: int, date_str: str) -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            "DELETE FROM work_sessions WHERE user_id = ? AND date = ?",
            (user_id, date_str),
        )
        await db.commit()


async def get_week_sessions(user_id: int) -> list[dict]:
    week_start = _week_start_str()
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM work_sessions WHERE user_id = ? AND date >= ? ORDER BY date ASC",
            (user_id, week_start),
        )
        return [dict(row) for row in await cursor.fetchall()]


# ── Backlog ────────────────────────────────────────────────────────────────────

async def add_backlog_item(user_id: int, text: str) -> int:
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute(
            "INSERT INTO backlog_items (user_id, text) VALUES (?, ?)",
            (user_id, text),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def get_backlog_items(user_id: int) -> list[dict]:
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM backlog_items WHERE user_id = ? AND status = 'pending' ORDER BY created_at ASC",
            (user_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def update_backlog_status(item_id: int, user_id: int, status: str) -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            "UPDATE backlog_items SET status = ? WHERE id = ? AND user_id = ?",
            (status, item_id, user_id),
        )
        await db.commit()


async def delete_backlog_item(item_id: int, user_id: int) -> None:
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            "DELETE FROM backlog_items WHERE id = ? AND user_id = ?",
            (item_id, user_id),
        )
        await db.commit()
