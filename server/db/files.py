"""Async SQLite queries for the files and chunks tables."""

import aiosqlite
from server.config import DB_PATH


async def create_file(
    id: str,
    name: str,
    size: int,
    total_chunks: int,
    uploader: str,
    recipient: str,
    encrypted_key: str | None,
    created_at: int,
    expires_at: int | None,
) -> None:
    """Insert a new file metadata record."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO files
                (id, name, size, total_chunks, uploader, recipient,
                 encrypted_key, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, name, size, total_chunks, uploader, recipient,
             encrypted_key, created_at, expires_at),
        )
        await db.commit()


async def get_file(id: str) -> dict | None:
    """Return file metadata dict or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM files WHERE id = ?", (id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def add_chunk(
    id: str,
    file_id: str,
    chunk_index: int,
    checksum: str,
) -> None:
    """Record a newly received chunk (delivered=0 by default)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO chunks (id, file_id, chunk_index, checksum, delivered)
            VALUES (?, ?, ?, ?, 1)
            """,
            (id, file_id, chunk_index, checksum),
        )
        await db.commit()


async def mark_chunk_delivered(file_id: str, chunk_index: int) -> None:
    """Set delivered=1 for a specific chunk."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE chunks SET delivered = 1
             WHERE file_id = ? AND chunk_index = ?
            """,
            (file_id, chunk_index),
        )
        await db.commit()


async def get_chunks(file_id: str) -> list[dict]:
    """Return all chunk records for a file, ordered by chunk_index."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM chunks WHERE file_id = ? ORDER BY chunk_index ASC",
            (file_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def count_delivered(file_id: str) -> int:
    """Return how many chunks have been marked as delivered."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM chunks WHERE file_id = ? AND delivered = 1",
            (file_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def delete_file(id: str) -> None:
    """Delete the file record and all associated chunk records."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chunks WHERE file_id = ?", (id,))
        await db.execute("DELETE FROM files WHERE id = ?", (id,))
        await db.commit()


async def get_expired_files(now: int) -> list[dict]:
    """Return all files whose expires_at timestamp is in the past."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM files WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
