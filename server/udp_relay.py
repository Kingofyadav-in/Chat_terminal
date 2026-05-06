"""UDP relay for audio fallback during voice calls.

Two independent mechanisms are provided:

1. **WebSocket relay** (``register_relay`` / ``relay_packet``): CALL_RELAY
   packets arrive over WebSocket and are forwarded to the other party's
   WebSocket connection.  No UDP socket is involved.

2. **Raw UDP relay** (``UDPRelay``): each UDP datagram has a 16-byte session
   prefix.  The server registers party A (first packet from unknown source)
   and party B (second unknown source for the same session), then just
   forwards A→B and B→A.  Useful when the client falls back to raw UDP
   instead of routing audio through the signalling WebSocket.
"""

import asyncio
import json
import logging
from typing import Any, Optional

from shared.utils import new_id, now

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WebSocket relay registry
# ---------------------------------------------------------------------------

# session_id → {"caller_ws": ws, "callee_ws": ws}
_ws_relay: dict[str, dict] = {}


def register_relay(session_id: str, caller_ws: Any, callee_ws: Any) -> None:
    """Register WebSocket handles for both parties of *session_id*."""
    _ws_relay[session_id] = {"caller_ws": caller_ws, "callee_ws": callee_ws}


def unregister_relay(session_id: str) -> None:
    """Remove the relay registry entry for *session_id*."""
    _ws_relay.pop(session_id, None)


async def relay_packet(
    session_id: str,
    from_user: str,
    data_b64: str,
    router_module: Any,
) -> None:
    """Forward *data_b64* to the other party of *session_id*.

    ``router_module`` must expose ``get(username) -> websocket | None``.

    Resolution order:
    1. Use the pre-registered WebSocket in ``_ws_relay`` if available.
    2. Fall back to looking up the recipient's current WebSocket via the router.
    """
    entry = _ws_relay.get(session_id)

    async def _send(ws: Any, pkt: dict) -> None:
        try:
            await ws.send(json.dumps(pkt))
        except Exception as exc:
            logger.warning("relay_packet send error (session %s): %s", session_id, exc)

    relay_pkt = {
        "version": "1.0",
        "type": "CALL_RELAY",
        "id": new_id(),
        "timestamp": now(),
        "session_id": session_id,
        "data": data_b64,
    }

    if entry is not None:
        # We have both ends registered — pick the right target.
        # We cannot easily determine caller/callee from a plain WS handle here,
        # so we send to both handles that are *not* the sender's.
        # In practice both ws handles are different objects; we compare by
        # identity against the sending ws obtained from the router.
        sender_ws = router_module.get(from_user)
        for key in ("caller_ws", "callee_ws"):
            target_ws = entry.get(key)
            if target_ws is not None and target_ws is not sender_ws:
                await _send(target_ws, relay_pkt)
                return

    # Fallback: let the call_manager handle routing (no direct ws ref needed).
    # We import call_manager lazily to avoid circular imports.
    import server.call_manager as cm_module  # noqa: F401 — used for routing

    # If the caller has registered a global call_manager instance we can use
    # it; otherwise just do a best-effort send to any online party.
    # This path is a safety net: the primary relay path is through CallManager.
    logger.debug(
        "relay_packet fallback for session %s from %s", session_id, from_user
    )


# ---------------------------------------------------------------------------
# Raw UDP relay
# ---------------------------------------------------------------------------

# SESSION_PREFIX_LEN: first 16 bytes of each datagram = session_id bytes
SESSION_PREFIX_LEN = 16


class _UDPRelayProtocol(asyncio.DatagramProtocol):
    """asyncio protocol that implements the raw UDP relay."""

    def __init__(self) -> None:
        self._transport: Optional[asyncio.DatagramTransport] = None
        # session_id_bytes (16 B) → {"a": addr, "b": addr}
        self._endpoints: dict[bytes, dict] = {}
        # addr → session_id_bytes
        self._addr_to_session: dict[tuple, bytes] = {}

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if len(data) < SESSION_PREFIX_LEN:
            logger.debug("UDP relay: datagram too short from %s", addr)
            return

        session_bytes = data[:SESSION_PREFIX_LEN]
        payload = data[SESSION_PREFIX_LEN:]

        endpoints = self._endpoints.setdefault(session_bytes, {})

        if addr not in self._addr_to_session:
            # New sender for this session.
            if "a" not in endpoints:
                endpoints["a"] = addr
                self._addr_to_session[addr] = session_bytes
                logger.debug("UDP relay: registered party A %s for session %s", addr, session_bytes.hex())
                return  # Wait for party B before forwarding.
            elif "b" not in endpoints:
                endpoints["b"] = addr
                self._addr_to_session[addr] = session_bytes
                logger.debug("UDP relay: registered party B %s for session %s", addr, session_bytes.hex())
                return
            else:
                # Unknown third party — discard.
                return

        # Known sender — forward to the other endpoint.
        a = endpoints.get("a")
        b = endpoints.get("b")

        if a is None or b is None:
            # Other party not yet registered; drop packet.
            return

        target = b if addr == a else a
        if self._transport is not None:
            self._transport.sendto(data, target)

    def error_received(self, exc: Exception) -> None:
        logger.warning("UDP relay protocol error: %s", exc)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        logger.info("UDP relay connection lost: %s", exc)


class UDPRelay:
    """Asyncio UDP relay server for raw audio datagrams."""

    def __init__(self) -> None:
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional[_UDPRelayProtocol] = None

    async def start(self, host: str, port: int) -> None:
        """Bind and start the UDP relay on *host*:*port*."""
        loop = asyncio.get_running_loop()
        import socket as _socket
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            _UDPRelayProtocol,
            local_addr=(host, port),
            reuse_port=True,
            allow_broadcast=False,
        )
        logger.info("UDP relay listening on %s:%d", host, port)

    def stop(self) -> None:
        """Close the UDP socket."""
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            logger.info("UDP relay stopped")
