"""Database schema initialisation.

Creates all tables (including files, chunks, call_sessions) and ensures the
on-disk directories required by the server exist.
"""

import os
import aiosqlite
from server.config import DB_PATH

FILES_DIR = os.path.expanduser("~/.chatterminal_server/files")

_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    username    TEXT UNIQUE NOT NULL,
    password    TEXT NOT NULL,
    public_key  TEXT,
    created_at  INTEGER NOT NULL,
    last_seen   INTEGER
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    username    TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS offline_queue (
    id          TEXT PRIMARY KEY,
    to_user     TEXT NOT NULL,
    packet      TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS room_members (
    room_id     TEXT NOT NULL,
    username    TEXT NOT NULL,
    joined_at   INTEGER NOT NULL,
    PRIMARY KEY (room_id, username)
);

CREATE TABLE IF NOT EXISTS files (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    size          INTEGER NOT NULL,
    total_chunks  INTEGER NOT NULL,
    uploader      TEXT NOT NULL,
    recipient     TEXT NOT NULL,
    encrypted_key TEXT,
    created_at    INTEGER NOT NULL,
    expires_at    INTEGER
);

CREATE TABLE IF NOT EXISTS chunks (
    id           TEXT PRIMARY KEY,
    file_id      TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    checksum     TEXT NOT NULL,
    delivered    INTEGER DEFAULT 0,
    UNIQUE(file_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS call_sessions (
    id          TEXT PRIMARY KEY,
    caller      TEXT NOT NULL,
    callee      TEXT NOT NULL,
    started_at  INTEGER,
    ended_at    INTEGER,
    relay_used  INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'pending'
);
"""


async def init_db() -> None:
    """Initialise the database and required file-system directories."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(FILES_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_DDL)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN public_key TEXT")
        except aiosqlite.OperationalError:
            pass
        await db.commit()


async def clear_server_state() -> None:
    """Remove all server-side user/session and app state."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        for table in (
            "sessions",
            "offline_queue",
            "room_members",
            "rooms",
            "chunks",
            "files",
            "call_sessions",
            "users",
        ):
            await db.execute(f"DELETE FROM {table}")
        await db.commit()
