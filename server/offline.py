from shared.utils import now
from server.db.messages import enqueue as _enqueue, get_queued, clear_queue


async def enqueue(msg_id: str, to_user: str, packet: str) -> None:
    await _enqueue(msg_id, to_user, packet, now())


async def flush(to_user: str, since: int = 0) -> list[str]:
    rows = await get_queued(to_user, since)
    return [r["packet"] for r in rows]


async def clear(to_user: str) -> None:
    await clear_queue(to_user)
