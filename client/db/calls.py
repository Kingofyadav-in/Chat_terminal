"""
Local SQLite persistence for call history.

The calls table is defined in schema.py.
"""

import aiosqlite
from client.config import DB_PATH


async def save_call(
    id: str,
    from_user: str,
    to_user: str,
    started_at: int,
    status: str,
) -> None:
    """Persist a new call record at the start of a session.

    Args:
        id:         Unique call session ID.
        from_user:  Username of the caller.
        to_user:    Username of the callee.
        started_at: Unix epoch timestamp when the call was initiated.
        status:     Initial status string, e.g. 'ringing', 'active'.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO calls
               (id, from_user, to_user, started_at, status)
               VALUES (?, ?, ?, ?, ?)""",
            (id, from_user, to_user, started_at, status),
        )
        await db.commit()


async def end_call(
    id: str,
    ended_at: int,
    duration: int,
    status: str,
    relay_used: bool,
) -> None:
    """Update a call record when the session ends.

    Args:
        id:         Call session ID.
        ended_at:   Unix epoch timestamp when the call ended.
        duration:   Call duration in seconds.
        status:     Final status string, e.g. 'completed', 'missed', 'declined'.
        relay_used: True if the call was relayed through the server.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE calls
               SET ended_at = ?, duration = ?, status = ?, relay_used = ?
               WHERE id = ?""",
            (ended_at, duration, status, 1 if relay_used else 0, id),
        )
        await db.commit()


async def get_history(self_user: str, limit: int = 20) -> list[dict]:
    """Fetch call history for a user (both inbound and outbound).

    Args:
        self_user: Local username.
        limit:     Maximum number of records to return (most recent first).

    Returns:
        List of call record dicts, ordered by started_at descending.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM calls
               WHERE from_user = ? OR to_user = ?
               ORDER BY started_at DESC
               LIMIT ?""",
            (self_user, self_user, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
