"""
STUN NAT traversal (RFC 5389 simplified).

Provides an async helper to discover the public (external) IP address and
port of this machine by sending a STUN Binding Request to a well-known
STUN server, and a UDP reachability probe for peer-to-peer connectivity
checks.
"""

from __future__ import annotations

import asyncio
import os
import socket
import struct


# ---------------------------------------------------------------------------
# STUN constants (RFC 5389)
# ---------------------------------------------------------------------------
_STUN_BINDING_REQUEST: int = 0x0001
_STUN_MAGIC_COOKIE: int = 0x2112A442
_STUN_HEADER_LEN: int = 20

# XOR-MAPPED-ADDRESS attribute type
_ATTR_XOR_MAPPED_ADDRESS: int = 0x0020
# MAPPED-ADDRESS attribute type (RFC 3489 fallback)
_ATTR_MAPPED_ADDRESS: int = 0x0001

_STUN_TIMEOUT: float = 3.0
_UDP_PROBE_COUNT: int = 5
_UDP_PROBE_PAYLOAD: bytes = b"ChatterTerminal-probe"


# ---------------------------------------------------------------------------
# STUN DatagramProtocol
# ---------------------------------------------------------------------------

class _StunProtocol(asyncio.DatagramProtocol):
    """Minimal asyncio DatagramProtocol that captures the first STUN response."""

    def __init__(self, transaction_id: bytes) -> None:
        self._transaction_id = transaction_id
        self.response: asyncio.Future[bytes] = asyncio.get_event_loop().create_future()
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not self.response.done():
            self.response.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self.response.done():
            self.response.set_exception(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if not self.response.done():
            self.response.cancel()


# ---------------------------------------------------------------------------
# STUN packet helpers
# ---------------------------------------------------------------------------

def _build_binding_request(transaction_id: bytes) -> bytes:
    """Build a 20-byte STUN Binding Request packet.

    Args:
        transaction_id: 12-byte random transaction ID.

    Returns:
        20-byte STUN binding request bytes.
    """
    if len(transaction_id) != 12:
        raise ValueError("STUN transaction ID must be 12 bytes")

    # Type (2) | Length (2) | Magic Cookie (4) | Transaction ID (12)
    return struct.pack(
        "!HHI",
        _STUN_BINDING_REQUEST,  # message type
        0,                      # message length (no attributes)
        _STUN_MAGIC_COOKIE,     # magic cookie
    ) + transaction_id


def _parse_xor_mapped_address(data: bytes, transaction_id: bytes) -> tuple[str, int] | None:
    """Extract the XOR-MAPPED-ADDRESS (or MAPPED-ADDRESS fallback) from a STUN response.

    Args:
        data:           Raw STUN response bytes.
        transaction_id: 12-byte transaction ID from the request.

    Returns:
        (ip_str, port) if found, else None.
    """
    if len(data) < _STUN_HEADER_LEN:
        return None

    msg_type, msg_len, magic, tid = struct.unpack_from("!HHI12s", data, 0)

    # Verify magic cookie and transaction ID
    if magic != _STUN_MAGIC_COOKIE:
        return None
    if tid != transaction_id:
        return None

    offset = _STUN_HEADER_LEN
    end = _STUN_HEADER_LEN + msg_len

    while offset + 4 <= end and offset + 4 <= len(data):
        attr_type, attr_len = struct.unpack_from("!HH", data, offset)
        attr_value = data[offset + 4 : offset + 4 + attr_len]
        # Pad to 4-byte boundary
        padded_len = attr_len + (4 - attr_len % 4) % 4
        offset += 4 + padded_len

        if attr_type == _ATTR_XOR_MAPPED_ADDRESS:
            return _decode_xor_mapped_address(attr_value, magic, transaction_id)

        if attr_type == _ATTR_MAPPED_ADDRESS:
            return _decode_mapped_address(attr_value)

    return None


def _decode_xor_mapped_address(
    attr: bytes, magic: int, transaction_id: bytes
) -> tuple[str, int] | None:
    """Decode an XOR-MAPPED-ADDRESS attribute value."""
    if len(attr) < 8:
        return None

    _, family, xport = struct.unpack_from("!BBH", attr, 0)

    if family == 0x01:  # IPv4
        if len(attr) < 8:
            return None
        xip_bytes = attr[4:8]
        magic_bytes = struct.pack("!I", magic)
        ip_bytes = bytes(a ^ b for a, b in zip(xip_bytes, magic_bytes))
        ip_str = socket.inet_ntoa(ip_bytes)
        port = xport ^ (_STUN_MAGIC_COOKIE >> 16)
        return ip_str, port

    if family == 0x02:  # IPv6
        if len(attr) < 20:
            return None
        xip_bytes = attr[4:20]
        xor_mask = struct.pack("!I", magic) + transaction_id
        ip_bytes = bytes(a ^ b for a, b in zip(xip_bytes, xor_mask))
        ip_str = socket.inet_ntop(socket.AF_INET6, ip_bytes)
        port = xport ^ (_STUN_MAGIC_COOKIE >> 16)
        return ip_str, port

    return None


def _decode_mapped_address(attr: bytes) -> tuple[str, int] | None:
    """Decode a MAPPED-ADDRESS attribute value (RFC 3489 fallback)."""
    if len(attr) < 8:
        return None

    _, family, port = struct.unpack_from("!BBH", attr, 0)

    if family == 0x01:  # IPv4
        ip_str = socket.inet_ntoa(attr[4:8])
        return ip_str, port

    if family == 0x02 and len(attr) >= 20:  # IPv6
        ip_str = socket.inet_ntop(socket.AF_INET6, attr[4:20])
        return ip_str, port

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_public_address(
    stun_host: str = "stun.l.google.com",
    stun_port: int = 19302,
    local_port: int = 0,
) -> tuple[str, int] | None:
    """Discover the public (NAT-translated) IP address and port via STUN.

    Sends a STUN Binding Request to the given STUN server and parses the
    XOR-MAPPED-ADDRESS attribute from the response.

    Args:
        stun_host:   Hostname of the STUN server.
        stun_port:   UDP port of the STUN server (default 19302).
        local_port:  Local UDP port to bind (0 = OS assigns).

    Returns:
        (public_ip, public_port) tuple, or None on timeout / parse failure.
    """
    loop = asyncio.get_event_loop()
    transaction_id = os.urandom(12)
    request = _build_binding_request(transaction_id)

    try:
        # Resolve the STUN server hostname
        infos = await loop.getaddrinfo(
            stun_host,
            stun_port,
            family=socket.AF_INET,
            type=socket.SOCK_DGRAM,
        )
        if not infos:
            return None

        server_addr = infos[0][4]  # (host, port)

        protocol = _StunProtocol(transaction_id)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol,
            local_addr=("0.0.0.0", local_port),
            family=socket.AF_INET,
        )

        try:
            transport.sendto(request, server_addr)

            response_data = await asyncio.wait_for(
                asyncio.shield(protocol.response),
                timeout=_STUN_TIMEOUT,
            )
        finally:
            transport.close()

        return _parse_xor_mapped_address(response_data, transaction_id)

    except (asyncio.TimeoutError, OSError, asyncio.CancelledError):
        return None
    except Exception:
        return None


async def try_direct_udp(
    local_sock: asyncio.DatagramTransport,
    peer_ip: str,
    peer_port: int,
    timeout: float = 3.0,
) -> bool:
    """Probe whether a UDP peer is directly reachable.

    Sends :data:`_UDP_PROBE_COUNT` probe packets and waits for any echo.
    Returns True if at least one packet is echoed back within *timeout*
    seconds.

    Note: This is a connectivity check only; the peer must be running a
    compatible probe listener (e.g. another ChatterTerminal instance in
    probe mode).

    Args:
        local_sock: An already-open asyncio DatagramTransport to send from.
        peer_ip:    IP address of the peer.
        peer_port:  UDP port of the peer.
        timeout:    Seconds to wait for any response.

    Returns:
        True if a response was received, False otherwise.
    """

    class _ProbeProtocol(asyncio.DatagramProtocol):
        def __init__(self) -> None:
            self.received = asyncio.get_event_loop().create_future()

        def datagram_received(self, data: bytes, addr: tuple) -> None:
            if not self.received.done():
                self.received.set_result(True)

        def error_received(self, exc: Exception) -> None:
            if not self.received.done():
                self.received.set_result(False)

        def connection_lost(self, exc: Exception | None) -> None:
            if not self.received.done():
                self.received.cancel()

    loop = asyncio.get_event_loop()
    probe_protocol = _ProbeProtocol()

    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: probe_protocol,
            family=socket.AF_INET,
        )
    except OSError:
        return False

    try:
        for _ in range(_UDP_PROBE_COUNT):
            try:
                transport.sendto(_UDP_PROBE_PAYLOAD, (peer_ip, peer_port))
            except OSError:
                break

        result = await asyncio.wait_for(
            asyncio.shield(probe_protocol.received),
            timeout=timeout,
        )
        return bool(result)
    except (asyncio.TimeoutError, asyncio.CancelledError, OSError):
        return False
    finally:
        transport.close()
