import aiosqlite
from server.config import DB_PATH


async def enqueue(msg_id: str, to_user: str, packet: str, created_at: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO offline_queue (id,to_user,packet,created_at) VALUES (?,?,?,?)",
            (msg_id, to_user, packet, created_at),
        )
        await db.commit()


async def get_queued(to_user: str, since: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM offline_queue WHERE to_user=? AND created_at>? ORDER BY created_at ASC",
            (to_user, since),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def clear_queue(to_user: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM offline_queue WHERE to_user=?", (to_user,))
        await db.commit()
