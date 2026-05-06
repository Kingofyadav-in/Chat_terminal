"""
Audio call handler.

Manages the full lifecycle of a 1-to-1 voice call:
  - invite  → sends CALL_INVITE with an optional ephemeral ECDH pubkey
  - accept  → derives the shared call key via X25519 + HKDF, starts audio
  - decline → rejects an incoming invite
  - end     → tears down an active call
  - relay   → forwards CALL_RELAY (RTP/audio) frames through the server

Audio streaming is delegated to client.audio.stream.AudioStream.  If that
module or its native dependencies (PyAudio, etc.) are unavailable the call
continues in text-only (relay-forwarding) mode with a logged warning.
"""

import asyncio
import base64
import logging
import os
import socket
from typing import Callable, Awaitable

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
)

from shared.protocol import MsgType, make_packet
from shared.utils import new_id, now
from client.db.calls import save_call, end_call

log = logging.getLogger("ct.client.call")

_HKDF_INFO = b"call-key"
_KEY_LEN   = 32


def _derive_call_key(
    my_ephemeral_private: bytes,
    peer_ephemeral_public: bytes,
    session_id: str,
) -> bytes:
    """Derive a symmetric call encryption key via X25519 ECDH + HKDF-SHA256.

    Args:
        my_ephemeral_private:  32-byte raw ephemeral X25519 private key.
        peer_ephemeral_public: 32-byte raw ephemeral X25519 public key of peer.
        session_id:            Unique call session ID used as HKDF salt.

    Returns:
        32-byte AES-256 key.
    """
    private_key  = X25519PrivateKey.from_private_bytes(my_ephemeral_private)
    peer_pub_key = X25519PublicKey.from_public_bytes(peer_ephemeral_public)
    shared       = private_key.exchange(peer_pub_key)

    hkdf = HKDF(
        algorithm=SHA256(),
        length=_KEY_LEN,
        salt=session_id.encode("utf-8"),
        info=_HKDF_INFO,
    )
    return hkdf.derive(shared)


def _get_free_udp_port() -> int:
    """Bind a UDP socket to port 0 and return the assigned ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CallHandler:
    """Manages voice call sessions over the ChatterTerminal server relay.

    Args:
        ws_client:            Connected WSClient instance.
        self_user:            Local username.
        crypto_ctx:           CryptoContext (provides long-term private_key).
                              Pass None to disable ECDH key exchange.
        audio_stream_factory: Optional callable that returns an AudioStream-
                              compatible object.  If None the handler tries to
                              import client.audio.stream.AudioStream.
        on_incoming_call:     Async callback(session_id, caller, caller_pubkey_b64).
        on_call_ended:        Async callback(session_id, reason).
    """

    def __init__(
        self,
        ws_client,
        self_user: str,
        crypto_ctx=None,
        audio_stream_factory=None,
        on_incoming_call: Callable[[str, str, str | None], Awaitable[None]] | None = None,
        on_call_ended: Callable[[str, str], Awaitable[None]] | None = None,
    ):
        self._ws                  = ws_client
        self.me                   = self_user
        self._ctx                 = crypto_ctx
        self._audio_factory       = audio_stream_factory
        self._on_incoming_call    = on_incoming_call
        self._on_call_ended       = on_call_ended

        self._active_session: str | None   = None
        self._call_start_ts: int           = 0
        self._muted: bool                  = False
        self._audio_stream                 = None
        self._local_port: int              = 0
        self._peer_ip: str                 = "127.0.0.1"
        self._peer_port: int               = 0
        self._ptt_enabled: bool            = False

        # Ephemeral keypair per call (caller side)
        self._ephemeral_private: bytes | None = None
        self._call_key: bytes | None          = None

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def active_session(self) -> str | None:
        """The session ID of the currently active call, or None."""
        return self._active_session

    @property
    def is_active(self) -> bool:
        """True when a call is in progress."""
        return self._active_session is not None

    # -----------------------------------------------------------------------
    # Outbound actions
    # -----------------------------------------------------------------------

    async def invite(self, to_user: str) -> str:
        """Initiate an outbound call.

        Generates an ephemeral X25519 keypair for the call key exchange,
        picks a free local UDP port, saves the call to the DB, and sends
        CALL_INVITE to the server.

        Args:
            to_user: Username of the intended callee.

        Returns:
            The new call session ID (UUID string).
        """
        session_id = new_id()
        ts         = now()

        # Generate ephemeral keypair for this call
        ephemeral_private_key = X25519PrivateKey.generate()
        self._ephemeral_private = ephemeral_private_key.private_bytes(
            encoding=Encoding.Raw,
            format=PrivateFormat.Raw,
            encryption_algorithm=NoEncryption(),
        )
        ephemeral_public_raw: bytes = ephemeral_private_key.public_key().public_bytes(
            encoding=Encoding.Raw,
            format=PublicFormat.Raw,
        )
        ephemeral_pubkey_b64 = base64.b64encode(ephemeral_public_raw).decode("ascii")

        local_port = _get_free_udp_port()

        packet_kwargs: dict = {
            "session_id": session_id,
            **{"from": self.me},
            "to": to_user,
            "local_port": local_port,
            "timestamp": ts,
        }

        packet_kwargs["ephemeral_pubkey"] = ephemeral_pubkey_b64

        await self._ws.send(make_packet(MsgType.CALL_INVITE, **packet_kwargs))

        self._active_session = session_id
        self._call_start_ts  = ts
        self._local_port     = local_port
        await save_call(session_id, self.me, to_user, ts, "ringing")

        log.info("Outbound call initiated: session=%s to=%s", session_id, to_user)
        return session_id

    async def accept(self, session_id: str, caller_pubkey_b64: str | None) -> None:
        """Accept an incoming call.

        If an ephemeral caller public key is provided the shared call key is
        derived via X25519 ECDH.  Then starts the audio stream.

        Args:
            session_id:      Call session ID from the CALL_INVITE packet.
            caller_pubkey_b64: Base64 ephemeral public key sent by the caller,
                               or None if the caller did not include one.
        """
        ts = now()

        # Generate our own ephemeral keypair for this call
        my_ephemeral = X25519PrivateKey.generate()
        my_ephemeral_private_raw: bytes = my_ephemeral.private_bytes(
            encoding=Encoding.Raw,
            format=PrivateFormat.Raw,
            encryption_algorithm=NoEncryption(),
        )
        my_ephemeral_public_raw: bytes = my_ephemeral.public_key().public_bytes(
            encoding=Encoding.Raw,
            format=PublicFormat.Raw,
        )
        my_ephemeral_pubkey_b64 = base64.b64encode(my_ephemeral_public_raw).decode("ascii")

        # Derive call key if we received the caller's ephemeral pubkey
        if caller_pubkey_b64:
            try:
                caller_pub_raw   = base64.b64decode(caller_pubkey_b64)
                self._call_key = _derive_call_key(
                    my_ephemeral_private_raw,
                    caller_pub_raw,
                    session_id,
                )
                self._ephemeral_private = my_ephemeral_private_raw
                log.debug("Call key derived for session=%s", session_id)
            except Exception as exc:
                log.warning("Could not derive call key for %s: %s", session_id, exc)

        local_port = _get_free_udp_port()

        packet_kwargs: dict = {
            "session_id": session_id,
            **{"from": self.me},
            "local_port": local_port,
            "timestamp": ts,
            "ephemeral_pubkey": my_ephemeral_pubkey_b64,
        }

        await self._ws.send(make_packet(MsgType.CALL_ACCEPT, **packet_kwargs))

        self._active_session = session_id
        self._call_start_ts  = ts
        self._local_port     = local_port

        await self._start_audio_stream(session_id)
        log.info("Call accepted: session=%s", session_id)

    async def decline(self, session_id: str) -> None:
        """Decline an incoming call invitation.

        Args:
            session_id: Call session ID from the CALL_INVITE packet.
        """
        await self._ws.send(
            make_packet(
                MsgType.CALL_DECLINE,
                session_id=session_id,
                **{"from": self.me},
                timestamp=now(),
            )
        )
        log.info("Call declined: session=%s", session_id)

    async def end(self, session_id: str) -> None:
        """Terminate an active call.

        Stops the audio stream, sends CALL_END to the server, and persists
        the final call record with duration.

        Args:
            session_id: The active call session ID.
        """
        await self._stop_audio_stream()

        ended_at = now()
        duration = max(0, ended_at - self._call_start_ts) if self._call_start_ts else 0

        await self._ws.send(
            make_packet(
                MsgType.CALL_END,
                session_id=session_id,
                **{"from": self.me},
                timestamp=ended_at,
            )
        )

        await end_call(
            session_id,
            ended_at=ended_at,
            duration=duration,
            status="completed",
            relay_used=True,
        )

        self._active_session    = None
        self._call_start_ts     = 0
        self._call_key          = None
        self._ephemeral_private = None
        log.info("Call ended: session=%s duration=%ds", session_id, duration)

    def mute(self, muted: bool) -> None:
        """Toggle the microphone mute state.

        Args:
            muted: True to mute, False to unmute.
        """
        self._muted = muted
        if self._audio_stream is not None:
            try:
                self._audio_stream.set_muted(muted)
            except AttributeError:
                pass
        log.debug("Mute set to %s", muted)

    def push_to_talk(self, enabled: bool) -> None:
        """Enable push-to-talk mode. When enabled, mic stays muted by default."""
        self._ptt_enabled = enabled
        self.mute(enabled)

    def set_talking(self, talking: bool) -> None:
        """Momentarily unmute while push-to-talk is enabled."""
        if self._ptt_enabled:
            self.mute(not talking)

    # -----------------------------------------------------------------------
    # Inbound packet dispatch
    # -----------------------------------------------------------------------

    async def handle_incoming(self, pkt: dict) -> None:
        """Dispatch an inbound call-related packet to the appropriate handler.

        Recognises CALL_INVITE, CALL_ACCEPT, CALL_DECLINE, CALL_END, and
        CALL_RELAY packet types.

        Args:
            pkt: Parsed packet dict from the server.
        """
        ptype = pkt.get("type")

        if ptype == MsgType.CALL_INVITE:
            await self._handle_invite(pkt)

        elif ptype == MsgType.CALL_ACCEPT:
            await self._handle_accept(pkt)

        elif ptype == MsgType.CALL_DECLINE:
            await self._handle_decline(pkt)

        elif ptype == MsgType.CALL_END:
            await self._handle_end(pkt)

        elif ptype == MsgType.CALL_RELAY:
            await self._handle_relay(pkt)

        else:
            log.debug("CallHandler ignoring packet type=%s", ptype)

    # -----------------------------------------------------------------------
    # Private inbound handlers
    # -----------------------------------------------------------------------

    async def _handle_invite(self, pkt: dict) -> None:
        caller     = pkt.get("from", "")
        session_id = pkt.get("session_id", new_id())
        caller_pub = pkt.get("ephemeral_pubkey")  # may be None
        ts         = pkt.get("timestamp") or now()
        self._peer_ip = pkt.get("caller_ip") or "127.0.0.1"
        self._peer_port = int(pkt.get("caller_udp_port") or pkt.get("local_port") or 0)

        await save_call(session_id, caller, self.me, ts, "ringing")

        if self._on_incoming_call:
            try:
                await self._on_incoming_call(session_id, caller, caller_pub)
            except Exception as exc:
                log.error("on_incoming_call callback raised: %s", exc)

    async def _handle_accept(self, pkt: dict) -> None:
        session_id     = pkt.get("session_id", "")
        callee_pub_b64 = pkt.get("ephemeral_pubkey")
        self._peer_ip = pkt.get("callee_ip") or "127.0.0.1"
        self._peer_port = int(pkt.get("callee_udp_port") or pkt.get("local_port") or 0)

        # Derive call key now that we have the callee's ephemeral pubkey
        if self._ephemeral_private and callee_pub_b64:
            try:
                callee_pub_raw = base64.b64decode(callee_pub_b64)
                self._call_key = _derive_call_key(
                    self._ephemeral_private,
                    callee_pub_raw,
                    session_id,
                )
                log.debug("Call key derived (caller side) for session=%s", session_id)
            except Exception as exc:
                log.warning("Could not derive call key on accept for %s: %s", session_id, exc)

        await self._start_audio_stream(session_id)
        log.info("Remote party accepted call: session=%s", session_id)

    async def _handle_decline(self, pkt: dict) -> None:
        session_id = pkt.get("session_id", "")
        ended_at   = now()
        duration   = 0

        await end_call(
            session_id,
            ended_at=ended_at,
            duration=duration,
            status="declined",
            relay_used=False,
        )

        if self._active_session == session_id:
            self._active_session    = None
            self._call_key          = None
            self._ephemeral_private = None

        if self._on_call_ended:
            try:
                await self._on_call_ended(session_id, "declined")
            except Exception as exc:
                log.error("on_call_ended callback raised: %s", exc)

    async def _handle_end(self, pkt: dict) -> None:
        session_id = pkt.get("session_id", "")
        ended_at   = now()
        duration   = max(0, ended_at - self._call_start_ts) if self._call_start_ts else 0

        await self._stop_audio_stream()

        await end_call(
            session_id,
            ended_at=ended_at,
            duration=duration,
            status="completed",
            relay_used=True,
        )

        if self._active_session == session_id:
            self._active_session    = None
            self._call_start_ts     = 0
            self._call_key          = None
            self._ephemeral_private = None

        if self._on_call_ended:
            try:
                await self._on_call_ended(session_id, "ended")
            except Exception as exc:
                log.error("on_call_ended callback raised: %s", exc)

    async def _handle_relay(self, pkt: dict) -> None:
        """Forward CALL_RELAY audio frames to the audio stream if active."""
        if self._audio_stream is None:
            return
        audio_data_b64 = pkt.get("data", "")
        if not audio_data_b64:
            return
        try:
            audio_bytes = base64.b64decode(audio_data_b64)
            if hasattr(self._audio_stream, "feed"):
                self._audio_stream.feed(audio_bytes)
        except Exception as exc:
            log.debug("Error feeding relay audio: %s", exc)

    # -----------------------------------------------------------------------
    # Audio stream lifecycle
    # -----------------------------------------------------------------------

    async def _start_audio_stream(self, session_id: str) -> None:
        """Try to start an AudioStream; log a warning and continue if unavailable."""
        if self._audio_factory is not None:
            try:
                self._audio_stream = self._audio_factory(
                    session_id=session_id,
                    call_key=self._call_key,
                )
                if hasattr(self._audio_stream, "start"):
                    await self._audio_stream.start()
                return
            except Exception as exc:
                log.warning("Audio stream factory failed: %s — text-only mode", exc)
                self._audio_stream = None
                return

        # Try to import the default AudioStream
        try:
            from client.audio.stream import AudioStream  # type: ignore[import]
            if self._call_key is None:
                self._call_key = os.urandom(32)
            self._audio_stream = AudioStream(
                local_port=self._local_port or _get_free_udp_port(),
                peer_ip=self._peer_ip,
                peer_port=self._peer_port,
                call_key=self._call_key,
            )
            if hasattr(self._audio_stream, "start"):
                await self._audio_stream.start(
                    mode="relay",
                    relay_ws=self._ws,
                    session_id=session_id,
                )
        except ImportError:
            log.warning(
                "client.audio.stream not available — call running in text-only mode"
            )
            self._audio_stream = None
        except Exception as exc:
            log.warning(
                "Could not start AudioStream: %s — text-only mode", exc
            )
            self._audio_stream = None

    async def _stop_audio_stream(self) -> None:
        """Stop the audio stream if one is running."""
        if self._audio_stream is not None:
            try:
                if hasattr(self._audio_stream, "stop"):
                    await self._audio_stream.stop()
            except Exception as exc:
                log.debug("Error stopping audio stream: %s", exc)
            finally:
                self._audio_stream = None
