import aiosqlite
from server.config import DB_PATH


async def create_user(
    id: str,
    username: str,
    hashed_pw: str,
    created_at: int,
    public_key: str | None = None,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (id, username, password, public_key, created_at)"
            " VALUES (?,?,?,?,?)",
            (id, username, hashed_pw, public_key, created_at),
        )
        await db.commit()


async def get_by_username(username: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_last_seen(username: str, ts: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET last_seen = ? WHERE username = ?", (ts, username)
        )
        await db.commit()


async def get_public_key(username: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT public_key FROM users WHERE username = ?", (username,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def create_session(
    token: str, user_id: str, username: str, created_at: int, expires_at: int
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (token,user_id,username,created_at,expires_at) VALUES (?,?,?,?,?)",
            (token, user_id, username, created_at, expires_at),
        )
        await db.commit()


async def get_session(token: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sessions WHERE token=? AND expires_at > strftime('%s','now')",
            (token,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_session(token: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sessions WHERE token=?", (token,))
        await db.commit()
