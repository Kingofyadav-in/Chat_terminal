"""Room management: creation, membership, message routing (in-memory + DB)."""

import json
import logging
from typing import Any

import aiosqlite

from server.config import DB_PATH
from shared.utils import new_id, now

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _db() -> aiosqlite.Connection:
    """Open a connection with Row factory pre-set."""
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def create_room(name: str, created_by: str) -> dict:
    """Create a new room and add the creator as the first member.

    Returns a dict with ``id``, ``name``, and ``created_by``.
    """
    room_id = new_id()
    ts = now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO rooms (id, name, created_by, created_at) VALUES (?,?,?,?)",
            (room_id, name, created_by, ts),
        )
        await db.execute(
            "INSERT OR IGNORE INTO room_members (room_id, username, joined_at) VALUES (?,?,?)",
            (room_id, created_by, ts),
        )
        await db.commit()
    return {"id": room_id, "name": name, "created_by": created_by}


async def join_room(room_id: str, username: str) -> bool:
    """Add *username* to *room_id*.

    Returns True if the user was successfully added (or was already a member),
    False if the room does not exist.
    """
    if not await room_exists(room_id):
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO room_members (room_id, username, joined_at) VALUES (?,?,?)",
            (room_id, username, now()),
        )
        await db.commit()
    return True


async def leave_room(room_id: str, username: str) -> None:
    """Remove *username* from *room_id*."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM room_members WHERE room_id = ? AND username = ?",
            (room_id, username),
        )
        await db.commit()


async def get_members(room_id: str) -> list[str]:
    """Return the list of usernames that belong to *room_id*."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT username FROM room_members WHERE room_id = ?", (room_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [r["username"] for r in rows]


async def get_user_rooms(username: str) -> list[dict]:
    """Return all rooms the user is a member of.

    Each dict contains ``id``, ``name``, and ``created_by``.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT r.id, r.name, r.created_by
              FROM rooms r
              JOIN room_members rm ON rm.room_id = r.id
             WHERE rm.username = ?
            """,
            (username,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def room_exists(room_id: str) -> bool:
    """Return True if a room with *room_id* exists in the database."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM rooms WHERE id = ?", (room_id,)
        ) as cur:
            return await cur.fetchone() is not None


async def route_room_message(
    room_id: str,
    pkt: dict,
    sender: str,
    router_module: Any,
) -> None:
    """Broadcast *pkt* to all room members except *sender*.

    Online members receive the packet over WebSocket immediately.
    Offline members have the packet enqueued for later delivery.

    ``router_module`` must expose ``get(username) -> websocket | None``.
    The offline module is imported lazily to avoid circular imports.
    """
    import server.offline as offline  # lazy import to avoid circular deps

    members = await get_members(room_id)
    payload = json.dumps(pkt)

    for member in members:
        if member == sender:
            continue
        ws = router_module.get(member)
        if ws is not None:
            try:
                await ws.send(payload)
            except Exception as exc:
                logger.warning(
                    "Failed to deliver room message to %s: %s", member, exc
                )
                pkt_id = pkt.get("id", new_id())
                await offline.enqueue(f"{pkt_id}:{member}", member, payload)
        else:
            pkt_id = pkt.get("id", new_id())
            await offline.enqueue(f"{pkt_id}:{member}", member, payload)
