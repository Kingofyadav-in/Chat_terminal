"""
End-to-end encrypted UDP audio stream.

Implements the full voice call pipeline:
  capture → encode → encrypt → send (UDP or relay WS)
  receive → decrypt → decode → play

Supports two transport modes:
  "p2p"   — direct UDP hole-punching between peers.
  "relay" — packets are forwarded over an existing WebSocket connection
            to the signalling server acting as a media relay.
"""

from __future__ import annotations

import asyncio
import socket
import struct
from typing import Optional

from client.audio.codec import AudioCodec
from client.audio.engine import AudioEngine
from client.audio.jitter_buffer import JitterBuffer
from client.crypto.audio_crypto import encrypt_packet, decrypt_packet


# Wire framing constants
# UDP payload: [4-byte seq LE] [12-byte nonce] [N-byte ciphertext+tag]
_SEQ_LEN = 4
_NONCE_LEN = 12
_HEADER_LEN = _SEQ_LEN + _NONCE_LEN  # 16 bytes

# Relay WebSocket message type prefix
_RELAY_MSG_TYPE = "CALL_RELAY"


class _UDPReceiveProtocol(asyncio.DatagramProtocol):
    """Minimal asyncio DatagramProtocol that feeds received datagrams into a queue."""

    def __init__(self, queue: asyncio.Queue[bytes]) -> None:
        self._queue = queue
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            pass  # Drop oldest packet rather than blocking

    def error_received(self, exc: Exception) -> None:
        pass  # Non-fatal — log in production

    def connection_lost(self, exc: Exception | None) -> None:
        pass


class AudioStream:
    """Encrypted, real-time UDP audio stream.

    Args:
        local_port:  Local UDP port to bind for receiving audio.
        peer_ip:     IP address of the remote peer.
        peer_port:   UDP port of the remote peer.
        call_key:    32-byte AES-256 session key for this call.
    """

    def __init__(
        self,
        local_port: int,
        peer_ip: str,
        peer_port: int,
        call_key: bytes,
    ) -> None:
        self._local_port = local_port
        self._peer_ip = peer_ip
        self._peer_port = peer_port
        self._call_key = call_key

        self._engine = AudioEngine()
        self._codec = AudioCodec()
        self._jitter = JitterBuffer(size=8)

        # Send sequence counter
        self._send_seq: int = 0

        # asyncio primitives (created in start())
        self._recv_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=128)
        self._relay_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=128)
        self._udp_transport: asyncio.DatagramTransport | None = None

        # Background tasks
        self._send_task: asyncio.Task | None = None
        self._recv_task: asyncio.Task | None = None

        self._muted: bool = False
        self._active: bool = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """True while the stream is running."""
        return self._active

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(
        self,
        mode: str = "p2p",
        relay_ws=None,
        session_id: str = "",
    ) -> None:
        """Start the audio stream.

        Launches capture+send and receive+play tasks concurrently.

        Args:
            mode:       "p2p" for direct UDP or "relay" to forward via relay_ws.
            relay_ws:   An open WebSocket connection used in relay mode.
                        Must have a ``send(message)`` coroutine.
            session_id: Call session identifier (used for relay framing).

        Raises:
            RuntimeError: If required audio or codec dependencies are unavailable.
        """
        if not self._engine.available:
            raise RuntimeError(
                "PyAudio is not available. Install it with: pip install pyaudio\n"
                "Voice calls require PyAudio and PortAudio to be installed."
            )

        loop = asyncio.get_event_loop()

        # Open UDP socket for receiving (and sending in p2p mode)
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _UDPReceiveProtocol(self._recv_queue),
                local_addr=("0.0.0.0", self._local_port),
                family=socket.AF_INET,
            )
            self._udp_transport = transport
        except OSError as exc:
            raise RuntimeError(f"Failed to open UDP socket on port {self._local_port}: {exc}") from exc

        self._active = True
        self._jitter.reset()
        self._send_seq = 0

        # Start capture → encode → encrypt → send
        async def _capture_callback(pcm: bytes) -> None:
            if self._muted:
                return
            await self._send_frame(pcm, mode, relay_ws, session_id)

        try:
            await self._engine.start_capture(_capture_callback)
            await self._engine.start_playback()
        except RuntimeError:
            self._active = False
            if self._udp_transport:
                self._udp_transport.close()
            raise

        # Start receive → decrypt → decode → play
        self._recv_task = asyncio.ensure_future(
            self._recv_loop(mode, relay_ws, session_id)
        )

    async def stop(self) -> None:
        """Stop the audio stream and release all resources."""
        self._active = False

        if self._send_task is not None:
            self._send_task.cancel()
            try:
                await self._send_task
            except (asyncio.CancelledError, Exception):
                pass
            self._send_task = None

        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
            self._recv_task = None

        self._engine.stop()

        if self._udp_transport is not None:
            self._udp_transport.close()
            self._udp_transport = None

        self._jitter.reset()

    def mute(self, muted: bool) -> None:
        """Toggle microphone mute.

        Args:
            muted: True to mute (stop sending), False to unmute.
        """
        self._muted = muted

    def set_muted(self, muted: bool) -> None:
        """Compatibility alias used by CallHandler."""
        self.mute(muted)

    def feed(self, payload: bytes) -> None:
        """Feed one relayed encrypted audio payload from the WebSocket router."""
        try:
            self._relay_queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass

    # ------------------------------------------------------------------
    # Internal send pipeline
    # ------------------------------------------------------------------

    async def _send_frame(
        self,
        pcm: bytes,
        mode: str,
        relay_ws,
        session_id: str,
    ) -> None:
        """Encode, encrypt, and transmit one audio frame."""
        try:
            encoded = self._codec.encode(pcm)
            nonce, ciphertext_with_tag = encrypt_packet(
                encoded, self._call_key, self._send_seq
            )

            # Build wire payload: [4-byte seq LE][12-byte nonce][ciphertext+tag]
            seq_bytes = struct.pack("<I", self._send_seq & 0xFFFFFFFF)
            payload = seq_bytes + nonce + ciphertext_with_tag

            self._send_seq = (self._send_seq + 1) & 0xFFFFFFFF

            if mode == "relay" and relay_ws is not None:
                # Relay mode: wrap in a JSON-ish message over WebSocket
                import base64
                import json

                relay_msg = json.dumps({
                    "type": _RELAY_MSG_TYPE,
                    "session_id": session_id,
                    "data": base64.b64encode(payload).decode("ascii"),
                })
                await relay_ws.send(relay_msg)
            else:
                # P2P mode: direct UDP
                if self._udp_transport is not None:
                    self._udp_transport.sendto(
                        payload, (self._peer_ip, self._peer_port)
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Log-worthy in production but must not crash the pipeline

    # ------------------------------------------------------------------
    # Internal receive pipeline
    # ------------------------------------------------------------------

    async def _recv_loop(
        self,
        mode: str,
        relay_ws,
        session_id: str,
    ) -> None:
        """Receive, decrypt, decode, and play incoming audio frames."""
        while self._active:
            try:
                if mode == "relay" and relay_ws is not None:
                    payload = await self._recv_relay(relay_ws)
                else:
                    payload = await asyncio.wait_for(
                        self._recv_queue.get(), timeout=1.0
                    )

                if payload is None or len(payload) < _HEADER_LEN:
                    continue

                seq = struct.unpack_from("<I", payload, 0)[0]
                nonce = payload[_SEQ_LEN : _SEQ_LEN + _NONCE_LEN]
                ciphertext_with_tag = payload[_HEADER_LEN:]

                try:
                    encoded = decrypt_packet(ciphertext_with_tag, nonce, self._call_key)
                except ValueError:
                    continue  # Drop unauthenticated packet silently

                self._jitter.push(seq, encoded)

                # Drain all in-order frames
                while True:
                    frame = self._jitter.pop()
                    if frame is None:
                        break
                    pcm = self._codec.decode(frame)
                    self._engine.play_frame(pcm)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                continue

    async def _recv_relay(self, relay_ws) -> bytes | None:
        """Receive one audio payload previously fed by CallHandler."""
        try:
            return await asyncio.wait_for(self._relay_queue.get(), timeout=1.0)
        except (asyncio.TimeoutError, Exception):
            return None
