import aiosqlite

from client.config import DB_PATH


async def save_room(room_id: str, name: str, last_message: str | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO rooms (id, name, last_message)
               VALUES (?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name,
                   last_message=COALESCE(excluded.last_message, rooms.last_message)""",
            (room_id, name, last_message),
        )
        await db.commit()


async def delete_room(room_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        await db.commit()


async def all_rooms() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM rooms ORDER BY name ASC") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def update_last_message(room_id: str, text: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE rooms SET last_message = ? WHERE id = ?",
            (text, room_id),
        )
        await db.commit()
