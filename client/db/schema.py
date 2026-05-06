import os
import aiosqlite
from client.config import DB_PATH

_DDL = """
CREATE TABLE IF NOT EXISTS account (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    private_key   TEXT,
    public_key    TEXT,
    server_url    TEXT NOT NULL,
    session_token TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    username      TEXT PRIMARY KEY,
    public_key    TEXT,
    fingerprint   TEXT,
    verified      INTEGER DEFAULT 0,
    added_at      INTEGER NOT NULL,
    status        TEXT DEFAULT 'offline'
);

CREATE TABLE IF NOT EXISTS messages (
    id            TEXT PRIMARY KEY,
    from_user     TEXT NOT NULL,
    to_user       TEXT NOT NULL,
    plaintext     TEXT,
    timestamp     INTEGER NOT NULL,
    delivered     INTEGER DEFAULT 0,
    read_status   INTEGER DEFAULT 0,
    edited        INTEGER DEFAULT 0,
    deleted       INTEGER DEFAULT 0,
    reply_to      TEXT
);

CREATE TABLE IF NOT EXISTS rooms (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    last_message  TEXT,
    unread_count  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS files (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    size            INTEGER NOT NULL,
    total_chunks    INTEGER NOT NULL,
    from_user       TEXT NOT NULL,
    to_user         TEXT NOT NULL,
    local_path      TEXT,
    timestamp       INTEGER NOT NULL,
    direction       TEXT NOT NULL,
    received_chunks INTEGER DEFAULT 0,
    complete        INTEGER DEFAULT 0,
    file_key        TEXT
);

CREATE TABLE IF NOT EXISTS calls (
    id            TEXT PRIMARY KEY,
    from_user     TEXT NOT NULL,
    to_user       TEXT NOT NULL,
    started_at    INTEGER,
    ended_at      INTEGER,
    duration      INTEGER,
    status        TEXT,
    relay_used    INTEGER DEFAULT 0
);
"""


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_DDL)
        try:
            await db.execute("ALTER TABLE files ADD COLUMN file_key TEXT")
        except aiosqlite.OperationalError:
            pass
        await db.commit()


async def clear_local_data() -> None:
    """Delete all local client data while keeping the schema intact."""
    async with aiosqlite.connect(DB_PATH) as db:
        for table in ("account", "contacts", "messages", "rooms", "files", "calls"):
            await db.execute(f"DELETE FROM {table}")
        await db.commit()
