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
