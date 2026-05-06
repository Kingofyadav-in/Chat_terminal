"""Server-side file transfer management: chunk storage, TTL cleanup."""

import logging
import os
import shutil

from server.db.files import (
    add_chunk,
    count_delivered,
    create_file,
    delete_file as db_delete_file,
    get_expired_files,
    get_file,
)
from shared.utils import new_id, now

logger = logging.getLogger(__name__)

FILES_DIR: str = os.path.expanduser("~/.chatterminal_server/files")
FILE_TTL: int = 86400 * 7  # 7 days in seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk_path(file_id: str, chunk_index: int) -> str:
    return os.path.join(FILES_DIR, file_id, f"chunk_{chunk_index:04d}")


def _file_dir(file_id: str) -> str:
    return os.path.join(FILES_DIR, file_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def register_file(file_meta: dict) -> None:
    """Persist file metadata to the DB and create its storage directory.

    *file_meta* is expected to have the keys produced by a FILE_INIT packet:
    ``id``, ``name``, ``size``, ``total_chunks``, ``uploader``, ``recipient``,
    ``encrypted_key`` (optional), plus an auto-computed ``created_at`` /
    ``expires_at``.
    """
    file_id = file_meta["id"]
    created_at = file_meta.get("created_at", now())
    expires_at = file_meta.get("expires_at", created_at + FILE_TTL)

    await create_file(
        id=file_id,
        name=file_meta["name"],
        size=file_meta["size"],
        total_chunks=file_meta["total_chunks"],
        uploader=file_meta["uploader"],
        recipient=file_meta["recipient"],
        encrypted_key=file_meta.get("encrypted_key"),
        created_at=created_at,
        expires_at=expires_at,
    )

    # Ensure the per-file directory exists.
    os.makedirs(_file_dir(file_id), exist_ok=True)


async def store_chunk(
    file_id: str,
    chunk_index: int,
    data: bytes,
    checksum: str,
) -> None:
    """Write *data* for *chunk_index* to disk and record it in the DB."""
    file_dir = _file_dir(file_id)
    os.makedirs(file_dir, exist_ok=True)

    path = _chunk_path(file_id, chunk_index)
    with open(path, "wb") as fh:
        fh.write(data)

    chunk_id = new_id()
    await add_chunk(
        id=chunk_id,
        file_id=file_id,
        chunk_index=chunk_index,
        checksum=checksum,
    )


async def get_chunk(file_id: str, chunk_index: int) -> bytes | None:
    """Read and return the raw bytes for *chunk_index* of *file_id*.

    Returns None if the chunk file does not exist on disk.
    """
    path = _chunk_path(file_id, chunk_index)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


async def is_complete(file_id: str) -> bool:
    """Return True when every chunk for *file_id* has been received."""
    meta = await get_file(file_id)
    if meta is None:
        return False
    delivered = await count_delivered(file_id)
    return delivered >= meta["total_chunks"]


async def delete_file_data(file_id: str) -> None:
    """Remove the on-disk directory and DB records for *file_id*."""
    # Remove from disk first; ignore errors if already gone.
    dir_path = _file_dir(file_id)
    if os.path.isdir(dir_path):
        shutil.rmtree(dir_path, ignore_errors=True)
    await db_delete_file(file_id)


async def cleanup_expired() -> int:
    """Delete all expired files from disk and the database.

    Returns the number of files deleted.
    """
    expired = await get_expired_files(now())
    count = 0
    for file_meta in expired:
        fid = file_meta["id"]
        logger.info("Expiring file %s (%s)", fid, file_meta.get("name"))
        await delete_file_data(fid)
        count += 1
    return count
