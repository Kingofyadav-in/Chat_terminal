"""Async SQLite queries for the call_sessions table."""

import aiosqlite
from server.config import DB_PATH


async def create_call(
    id: str,
    caller: str,
    callee: str,
    started_at: int,
) -> None:
    """Insert a new call session record."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO call_sessions (id, caller, callee, started_at, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (id, caller, callee, started_at),
        )
        await db.commit()


async def update_call(id: str, **kwargs) -> None:
    """Update arbitrary fields on a call session by keyword arguments.

    Only columns that exist in call_sessions are safe to pass; unknown column
    names will cause a runtime SQLite error, which is the intended behaviour
    (fail fast on programmer mistakes).
    """
    if not kwargs:
        return
    set_clause = ", ".join(f"{col} = ?" for col in kwargs)
    values = list(kwargs.values()) + [id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE call_sessions SET {set_clause} WHERE id = ?",
            values,
        )
        await db.commit()


async def get_call(id: str) -> dict | None:
    """Return a call session dict or None if not found."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM call_sessions WHERE id = ?", (id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def end_call(id: str, ended_at: int, relay_used: bool = False) -> None:
    """Mark a call session as ended."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE call_sessions
               SET ended_at = ?, relay_used = ?, status = 'ended'
             WHERE id = ?
            """,
            (ended_at, int(relay_used), id),
        )
        await db.commit()
