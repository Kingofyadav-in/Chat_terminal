import aiosqlite
from client.config import DB_PATH


async def save(
    id: str,
    from_user: str,
    to_user: str,
    plaintext: str,
    timestamp: int,
    delivered: int = 0,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO messages
               (id, from_user, to_user, plaintext, timestamp, delivered)
               VALUES (?,?,?,?,?,?)""",
            (id, from_user, to_user, plaintext, timestamp, delivered),
        )
        await db.commit()


async def mark_delivered(id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE messages SET delivered=1 WHERE id=?", (id,))
        await db.commit()


async def mark_read_by_id(id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE messages SET read_status=1 WHERE id=?", (id,))
        await db.commit()


async def edit_message(id: str, plaintext: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE messages SET plaintext=?, edited=1 WHERE id=?",
            (plaintext, id),
        )
        await db.commit()


async def delete_message(id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE messages SET deleted=1 WHERE id=?", (id,))
        await db.commit()


async def get_history(peer: str, self_user: str, limit: int = 100) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM messages
               WHERE (from_user=? AND to_user=?) OR (from_user=? AND to_user=?)
               ORDER BY timestamp DESC, rowid DESC LIMIT ?""",
            (self_user, peer, peer, self_user, limit),
        ) as cur:
            rows = await cur.fetchall()
            return list(reversed([dict(r) for r in rows]))


async def get_room_history(room_id: str, limit: int = 100) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM messages
               WHERE to_user=?
               ORDER BY timestamp DESC, rowid DESC LIMIT ?""",
            (room_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            return list(reversed([dict(r) for r in rows]))


async def mark_read(peer: str, self_user: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE messages SET read_status=1 WHERE from_user=? AND to_user=? AND read_status=0",
            (peer, self_user),
        )
        await db.commit()


async def unread_count(peer: str, self_user: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM messages WHERE from_user=? AND to_user=? AND read_status=0",
            (peer, self_user),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0
