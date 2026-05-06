"""
Local SQLite persistence for file transfer metadata.

The files table must exist in the database (defined in schema.py).
"""

import aiosqlite
from client.config import DB_PATH


async def save_file(
    id: str,
    name: str,
    size: int,
    total_chunks: int,
    from_user: str,
    to_user: str,
    local_path: str,
    timestamp: int,
    direction: str,
    file_key: str | None = None,
) -> None:
    """Persist a new file transfer record.

    Args:
        id:           Unique file transfer ID.
        name:         Original filename.
        size:         Total file size in bytes.
        total_chunks: Number of chunks the file is split into.
        from_user:    Username of the sender.
        to_user:      Username of the recipient.
        local_path:   Absolute path on disk (source for send, dest for recv).
        timestamp:    Unix epoch when the transfer was initiated.
        direction:    'send' or 'recv'.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO files
               (id, name, size, total_chunks, from_user, to_user,
                local_path, timestamp, direction, received_chunks, complete, file_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name,
                   size=excluded.size,
                   total_chunks=excluded.total_chunks,
                   from_user=excluded.from_user,
                   to_user=excluded.to_user,
                   local_path=excluded.local_path,
                   timestamp=excluded.timestamp,
                   direction=excluded.direction,
                   file_key=COALESCE(excluded.file_key, files.file_key)""",
            (id, name, size, total_chunks, from_user, to_user,
             local_path, timestamp, direction, file_key),
        )
        await db.commit()


async def get_recent_files(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM files ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_file(id: str) -> dict | None:
    """Retrieve a file transfer record by ID.

    Args:
        id: File transfer ID.

    Returns:
        Dict of all columns, or None if not found.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM files WHERE id = ?", (id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_file(id: str, **kwargs) -> None:
    """Update arbitrary columns on a file transfer record.

    Args:
        id:      File transfer ID.
        **kwargs: Column name → new value pairs.

    Raises:
        ValueError: If no keyword arguments are provided.
    """
    if not kwargs:
        raise ValueError("update_file requires at least one keyword argument")

    set_clause = ", ".join(f"{col} = ?" for col in kwargs)
    values = list(kwargs.values()) + [id]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE files SET {set_clause} WHERE id = ?",
            values,
        )
        await db.commit()


async def get_pending_receives(self_user: str) -> list[dict]:
    """Fetch all incomplete inbound file transfers for a user.

    Args:
        self_user: Local username (the recipient).

    Returns:
        List of file record dicts where direction='recv' and complete=0.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM files
               WHERE to_user = ? AND direction = 'recv' AND complete = 0
               ORDER BY timestamp ASC""",
            (self_user,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def mark_complete(id: str, local_path: str) -> None:
    """Mark a file transfer as fully received and set its final path.

    Args:
        id:         File transfer ID.
        local_path: Absolute path where the reassembled file was written.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE files SET complete = 1, local_path = ? WHERE id = ?",
            (local_path, id),
        )
        await db.commit()
