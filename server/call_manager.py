"""Call session management and NAT signalling relay."""

import json
import logging
from typing import Any

from server.db.calls import create_call, end_call as db_end_call, update_call
from shared.utils import new_id, now

logger = logging.getLogger(__name__)


class CallManager:
    """Tracks active call sessions and routes signalling packets between peers.

    All sessions are stored in memory; the canonical persistence layer is the
    ``call_sessions`` SQLite table (written through ``server.db.calls``).
    """

    def __init__(self) -> None:
        # session_id → {caller, callee, status, caller_info, callee_info}
        self._sessions: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Session access
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> dict | None:
        """Return the in-memory session dict or None."""
        return self._sessions.get(session_id)

    # ------------------------------------------------------------------
    # Signalling helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _send(username: str, packet: dict, router_module: Any) -> bool:
        """Serialise *packet* and deliver it to *username* via WebSocket.

        Returns True on success, False if the user is offline or the send
        fails.
        """
        ws = router_module.get(username)
        if ws is None:
            return False
        try:
            await ws.send(json.dumps(packet))
            return True
        except Exception as exc:
            logger.warning("Failed to send to %s: %s", username, exc)
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def invite(
        self,
        session_id: str,
        caller: str,
        callee: str,
        caller_info: dict,
        router_module: Any,
        ws_client: Any,
    ) -> bool:
        """Initiate a call from *caller* to *callee*.

        Creates the session record in memory and in the DB, then forwards the
        CALL_INVITE packet to the callee.

        ``caller_info`` should contain at least: ``caller_ip``,
        ``caller_udp_port``, ``caller_pubkey``.

        Returns False if the callee is offline.
        """
        if router_module.get(callee) is None:
            return False

        ts = now()
        session = {
            "id": session_id,
            "caller": caller,
            "callee": callee,
            "status": "pending",
            "caller_info": caller_info,
            "callee_info": None,
            "created_at": ts,
        }
        self._sessions[session_id] = session

        await create_call(
            id=session_id,
            caller=caller,
            callee=callee,
            started_at=ts,
        )

        invite_pkt = {
            "version": "1.0",
            "type": "CALL_INVITE",
            "id": new_id(),
            "timestamp": ts,
            "session_id": session_id,
            "from": caller,
            "caller": caller,
            "to": callee,
            "caller_ip": caller_info.get("caller_ip"),
            "local_port": caller_info.get("caller_udp_port"),
            "caller_udp_port": caller_info.get("caller_udp_port"),
            "ephemeral_pubkey": caller_info.get("caller_pubkey"),
            "caller_pubkey": caller_info.get("caller_pubkey"),
        }
        await self._send(callee, invite_pkt, router_module)
        return True

    async def accept(
        self,
        session_id: str,
        callee_info: dict,
        router_module: Any,
    ) -> bool:
        """Accept a pending call.

        Updates the session status and forwards CALL_ACCEPT to the caller.

        ``callee_info`` should contain: ``callee_ip``, ``callee_udp_port``,
        ``callee_pubkey``.

        Returns False if the session is unknown or already ended.
        """
        session = self._sessions.get(session_id)
        if session is None or session["status"] not in ("pending",):
            return False

        session["status"] = "active"
        session["callee_info"] = callee_info

        await update_call(session_id, status="active")

        ts = now()
        accept_pkt = {
            "version": "1.0",
            "type": "CALL_ACCEPT",
            "id": new_id(),
            "timestamp": ts,
            "session_id": session_id,
            "from": session["callee"],
            "to": session["caller"],
            "callee_ip": callee_info.get("callee_ip"),
            "local_port": callee_info.get("callee_udp_port"),
            "callee_udp_port": callee_info.get("callee_udp_port"),
            "ephemeral_pubkey": callee_info.get("callee_pubkey"),
            "callee_pubkey": callee_info.get("callee_pubkey"),
        }
        await self._send(session["caller"], accept_pkt, router_module)
        return True

    async def decline(
        self,
        session_id: str,
        router_module: Any,
    ) -> None:
        """Decline a pending call.

        Forwards CALL_DECLINE to the caller and removes the session.
        """
        session = self._sessions.pop(session_id, None)
        if session is None:
            return

        await db_end_call(session_id, ended_at=now())

        ts = now()
        decline_pkt = {
            "version": "1.0",
            "type": "CALL_DECLINE",
            "id": new_id(),
            "timestamp": ts,
            "session_id": session_id,
        }
        await self._send(session["caller"], decline_pkt, router_module)

    async def end_call(
        self,
        session_id: str,
        from_user: str,
        router_module: Any,
    ) -> None:
        """End an active call.

        Notifies the other party with CALL_END and removes the session.
        """
        session = self._sessions.pop(session_id, None)
        if session is None:
            return

        await db_end_call(session_id, ended_at=now())

        # Determine the other party.
        other = (
            session["callee"]
            if from_user == session["caller"]
            else session["caller"]
        )

        ts = now()
        end_pkt = {
            "version": "1.0",
            "type": "CALL_END",
            "id": new_id(),
            "timestamp": ts,
            "session_id": session_id,
        }
        await self._send(other, end_pkt, router_module)

    async def relay_audio(
        self,
        session_id: str,
        from_user: str,
        data_b64: str,
        router_module: Any,
    ) -> None:
        """Forward a CALL_RELAY packet from *from_user* to the other party.

        Does nothing silently if the session is unknown or the recipient is
        offline (UDP-style best-effort delivery).
        """
        session = self._sessions.get(session_id)
        if session is None:
            return

        other = (
            session["callee"]
            if from_user == session["caller"]
            else session["caller"]
        )

        ts = now()
        relay_pkt = {
            "version": "1.0",
            "type": "CALL_RELAY",
            "id": new_id(),
            "timestamp": ts,
            "session_id": session_id,
            "from": from_user,
            "data": data_b64,
        }
        await self._send(other, relay_pkt, router_module)
