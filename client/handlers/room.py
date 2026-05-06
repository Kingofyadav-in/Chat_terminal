"""
Room (group chat) management handler.

Handles creating, joining, and leaving rooms, sending room messages, and
processing inbound room packets from the server.  Server responses to
create/join/members requests are synchronised via asyncio.Event objects
stored in _pending so callers can await them with a timeout.
"""

import asyncio
import logging
from typing import Callable, Awaitable

from shared.protocol import MsgType, make_packet
from shared.utils import new_id, now
from client.crypto.encrypt import encrypt_message
from client.db.contacts import get_public_key

log = logging.getLogger("ct.client.room")

_DEFAULT_TIMEOUT = 5.0  # seconds to wait for a server response


class RoomHandler:
    """Manages room lifecycle and messaging.

    Args:
        ws_client:       Connected WSClient instance.
        self_user:       Local username.
        crypto_ctx:      CryptoContext (reserved for future per-member
                         encryption; currently unused for room messages).
        on_room_message: Optional async callback fired for each inbound
                         ROOM_MESSAGE packet.
    """

    def __init__(
        self,
        ws_client,
        self_user: str,
        crypto_ctx=None,
        on_room_message: Callable[[dict], Awaitable[None]] | None = None,
    ):
        self._ws       = ws_client
        self.me        = self_user
        self._ctx      = crypto_ctx
        self._callback = on_room_message

        # Pending response waiters:
        #   key → (asyncio.Event, result_container)
        # result_container is a list so the event setter can push the value in.
        self._pending: dict[str, tuple[asyncio.Event, list]] = {}

    # -----------------------------------------------------------------------
    # Room lifecycle
    # -----------------------------------------------------------------------

    async def create(self, name: str) -> dict:
        """Create a new room and wait for the server's ROOM_INFO response.

        Args:
            name: Human-readable room name.

        Returns:
            The ROOM_INFO response dict from the server.

        Raises:
            asyncio.TimeoutError: If the server does not respond within 5 s.
        """
        req_id = new_id()
        event  = asyncio.Event()
        result: list[dict] = []
        self._pending[req_id] = (event, result)

        try:
            await self._ws.send(
                make_packet(
                    MsgType.ROOM_CREATE,
                    req_id=req_id,
                    name=name,
                    **{"from": self.me},
                )
            )
            await asyncio.wait_for(event.wait(), timeout=_DEFAULT_TIMEOUT)
            return result[0] if result else {}
        finally:
            self._pending.pop(req_id, None)

    async def join(self, room_id: str) -> bool:
        """Send a ROOM_JOIN request and wait for the server's acknowledgement.

        Args:
            room_id: The room UUID to join.

        Returns:
            True if the server confirmed the join, False on timeout or error.
        """
        req_id = new_id()
        event  = asyncio.Event()
        result: list[dict] = []
        self._pending[req_id] = (event, result)

        try:
            await self._ws.send(
                make_packet(
                    MsgType.ROOM_JOIN,
                    req_id=req_id,
                    room_id=room_id,
                    **{"from": self.me},
                )
            )
            try:
                await asyncio.wait_for(event.wait(), timeout=_DEFAULT_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning("Timeout waiting for ROOM_JOIN confirmation for %s", room_id)
                return False

            response = result[0] if result else {}
            return response.get("success", False)
        finally:
            self._pending.pop(req_id, None)

    async def leave(self, room_id: str) -> None:
        """Send a ROOM_LEAVE notification (fire-and-forget).

        Args:
            room_id: The room UUID to leave.
        """
        await self._ws.send(
            make_packet(
                MsgType.ROOM_LEAVE,
                room_id=room_id,
                **{"from": self.me},
            )
        )

    async def get_members(self, room_id: str) -> list[str]:
        """Request and return the current member list for a room.

        Args:
            room_id: The room UUID to query.

        Returns:
            List of usernames in the room.  Empty list on timeout.
        """
        req_id = new_id()
        event  = asyncio.Event()
        result: list[dict] = []
        self._pending[req_id] = (event, result)

        try:
            await self._ws.send(
                make_packet(
                    MsgType.ROOM_MEMBERS,
                    req_id=req_id,
                    room_id=room_id,
                    **{"from": self.me},
                )
            )
            try:
                await asyncio.wait_for(event.wait(), timeout=_DEFAULT_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning("Timeout waiting for ROOM_MEMBERS_LIST for %s", room_id)
                return []

            response = result[0] if result else {}
            return response.get("members", [])
        finally:
            self._pending.pop(req_id, None)

    # -----------------------------------------------------------------------
    # Messaging
    # -----------------------------------------------------------------------

    async def send_message(self, room_id: str, text: str) -> str:
        """Broadcast a text message to a room.

        Encrypts per-member when possible; falls back to plaintext otherwise.

        Args:
            room_id: The destination room UUID.
            text:    Message body.
        """
        msg_id = new_id()
        ts = now()

        if self._ctx is not None:
            import base64
            members = await self.get_members(room_id)
            payloads: dict[str, dict] = {}
            for member in members:
                if member == self.me:
                    continue
                pubkey_b64 = await get_public_key(member)
                if pubkey_b64:
                    enc = encrypt_message(text, base64.b64decode(pubkey_b64))
                    enc["encrypted"] = True
                    payloads[member] = enc
            # Send encrypted if we have at least one payload, else plaintext
            if payloads:
                await self._ws.send(
                    make_packet(
                        MsgType.ROOM_MESSAGE,
                        id=msg_id,
                        room_id=room_id,
                        **{"from": self.me},
                        payloads=payloads,
                        timestamp=ts,
                    )
                )
                return msg_id

        # Plaintext fallback (no crypto_ctx or no member keys available)
        await self._ws.send(
            make_packet(
                MsgType.ROOM_MESSAGE,
                id=msg_id,
                room_id=room_id,
                **{"from": self.me},
                payload={"text": text},
                timestamp=ts,
            )
        )
        return msg_id

    # -----------------------------------------------------------------------
    # Inbound packet handlers
    # -----------------------------------------------------------------------

    async def handle_incoming(self, pkt: dict) -> None:
        """Process an inbound ROOM_MESSAGE packet.

        Calls the on_room_message callback (if set) with the raw packet.

        Args:
            pkt: Parsed packet dict from the server.
        """
        if self._callback:
            try:
                await self._callback(pkt)
            except Exception as exc:
                log.error("on_room_message callback raised: %s", exc)

    def handle_room_info(self, pkt: dict) -> None:
        """Store a ROOM_INFO response and wake any waiting create() caller.

        The packet must contain a ``req_id`` field that matches a pending
        create() request.

        Args:
            pkt: ROOM_INFO packet dict from the server.
        """
        req_id = pkt.get("req_id", "")
        self._resolve_pending(req_id, pkt)

    def handle_members_list(self, pkt: dict) -> None:
        """Store a ROOM_MEMBERS_LIST response and wake any waiting caller.

        The packet must contain a ``req_id`` that matches a pending
        get_members() request.

        Args:
            pkt: ROOM_MEMBERS_LIST packet dict from the server.
        """
        req_id = pkt.get("req_id", "")
        self._resolve_pending(req_id, pkt)

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _resolve_pending(self, req_id: str, result: dict) -> None:
        """Push a result into the pending waiter bucket and signal the event.

        Args:
            req_id: Request correlation ID.
            result: Response dict to deliver to the waiter.
        """
        if req_id in self._pending:
            event, container = self._pending[req_id]
            container.append(result)
            event.set()
        else:
            log.debug("Received response for unknown req_id=%s", req_id)
