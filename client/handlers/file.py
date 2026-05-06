"""
File transfer handler.

Files are split into 512 KB chunks.  Each chunk is independently encrypted
with AES-256-GCM using a per-file symmetric key.  The file key itself is
wrapped for the recipient using their pinned X25519 public key.  Chunk data
is streamed over a direct TCP connection to the server relay on a separate
port.

TCP upload protocol (per chunk after the initial handshake):
    4 bytes  — chunk_index  (big-endian uint32)
    4 bytes  — data_len     (big-endian uint32) — length of the payload below
   32 bytes  — checksum     (ASCII hex SHA-256 of *plaintext* chunk)
  N bytes    — encrypted chunk payload (ciphertext + nonce + tag, packed)

The server acknowledges each chunk with b"ACK\\n".

TCP download protocol:
    Send "DOWNLOAD {file_id}\\n" to the server; it then streams chunks in the
    same 4+4+32+N format.

Encrypted chunk wire layout (the N-byte payload above):
   12 bytes  — AES-GCM nonce
   16 bytes  — AES-GCM authentication tag
  remainder  — ciphertext
"""

import asyncio
import base64
import logging
import os
import struct
from typing import Callable, Awaitable

from shared.protocol import MsgType, make_packet
from shared.utils import new_id, now
from client.config import DOWNLOADS_DIR
from client.crypto.file_crypto import (
    encrypt_chunk,
    decrypt_chunk,
    generate_file_key,
    chunk_checksum,
)
from client.crypto.encrypt import decrypt_message, encrypt_message
from client.db.contacts import get_public_key
from client.db.files import save_file, update_file, mark_complete, get_file

log = logging.getLogger("ct.client.file")

CHUNK_SIZE = 512 * 1024  # 512 KB

_NONCE_LEN    = 12
_TAG_LEN      = 16
_HDR_STRUCT   = struct.Struct(">II")   # chunk_index, data_len
_CHECKSUM_LEN = 64                     # hex SHA-256


class FileHandler:
    """Manages outbound file sends and inbound file receives.

    Args:
        ws_client:        Connected WSClient instance.
        self_user:        Local username.
        crypto_ctx:       CryptoContext with the local private key; required
                          to decrypt inbound file-key envelopes.
        server_tcp_host:  Hostname/IP of the server TCP relay.
        server_tcp_port:  Port of the server TCP relay (default 8766).
        on_receive:       Async callback(file_meta: dict) fired when a
                          FILE_INIT packet arrives (offers an inbound file).
        on_progress:      Async callback(file_id, sent_chunks, total_chunks)
                          fired after each successfully uploaded chunk.
    """

    def __init__(
        self,
        ws_client,
        self_user: str,
        crypto_ctx=None,
        server_tcp_host: str = "127.0.0.1",
        server_tcp_port: int = 8766,
        on_receive: Callable[[dict], Awaitable[None]] | None = None,
        on_progress: Callable[[str, int, int], Awaitable[None]] | None = None,
    ):
        self._ws          = ws_client
        self.me           = self_user
        self._ctx         = crypto_ctx
        self._tcp_host    = server_tcp_host
        self._tcp_port    = server_tcp_port
        self._on_receive  = on_receive
        self._on_progress = on_progress

        # In-memory state for active transfers: file_id → metadata dict
        self._active: dict[str, dict] = {}

    # -----------------------------------------------------------------------
    # Send
    # -----------------------------------------------------------------------

    async def send(self, to_user: str, filepath: str) -> str:
        """Send a file to another user.

        Reads the file from disk, splits it into CHUNK_SIZE chunks, encrypts
        each independently, and streams them over a TCP connection to the
        server relay.

        Args:
            to_user:  Recipient's username.
            filepath: Absolute (or resolvable) path to the file to send.

        Returns:
            The file transfer ID (UUID string).

        Raises:
            FileNotFoundError: If ``filepath`` does not exist.
            OSError: On read or network errors.
        """
        filepath = os.path.abspath(os.path.expanduser(filepath))
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        file_id   = new_id()
        filename  = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)
        ts        = now()

        with open(filepath, "rb") as fh:
            raw = fh.read()

        # Split into chunks
        chunks: list[bytes] = []
        for offset in range(0, len(raw), CHUNK_SIZE):
            chunks.append(raw[offset : offset + CHUNK_SIZE])
        if not chunks:
            chunks = [b""]

        total_chunks = len(chunks)

        # Generate per-file AES key
        file_key     = generate_file_key()
        file_key_b64 = base64.b64encode(file_key).decode("ascii")
        recipient_pubkey_b64 = await get_public_key(to_user)
        if not recipient_pubkey_b64:
            raise ValueError(
                f"Cannot send file: no public key cached for {to_user}. "
                f"Open a DM with /dm @{to_user} first so the key is fetched, then retry."
            )
        encrypted_key = encrypt_message(
            file_key_b64,
            base64.b64decode(recipient_pubkey_b64),
        )

        # Persist the outbound transfer record
        await save_file(
            id=file_id,
            name=filename,
            size=file_size,
            total_chunks=total_chunks,
            from_user=self.me,
            to_user=to_user,
            local_path=filepath,
            timestamp=ts,
            direction="send",
            file_key=file_key_b64,
        )
        self._active[file_id] = {
            "file_key": file_key,
            "total_chunks": total_chunks,
        }

        # Notify the peer via WebSocket
        await self._ws.send(
            make_packet(
                MsgType.FILE_INIT,
                file_id=file_id,
                **{"from": self.me},
                to=to_user,
                name=filename,
                size=file_size,
                total_chunks=total_chunks,
                encrypted_key=encrypted_key,
                timestamp=ts,
            )
        )

        await self._upload_chunks(file_id, chunks, file_key, total_chunks)

        await self._ws.send(
            make_packet(
                MsgType.FILE_DONE,
                file_id=file_id,
                **{"from": self.me},
                to=to_user,
                timestamp=now(),
            )
        )
        await update_file(file_id, complete=1)
        self._active.pop(file_id, None)

        log.info("File sent: id=%s name=%s to=%s chunks=%d", file_id, filename, to_user, total_chunks)
        return file_id

    async def resume_send(self, file_id: str) -> None:
        """Resume a saved outbound transfer by file ID."""
        record = await get_file(file_id)
        if not record or record.get("direction") != "send":
            raise FileNotFoundError(f"No outbound transfer found for {file_id}")

        filepath = os.path.abspath(os.path.expanduser(record["local_path"]))
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        file_key_b64 = record.get("file_key")
        if not file_key_b64:
            raise ValueError(f"No file key stored for transfer {file_id}")

        with open(filepath, "rb") as fh:
            raw = fh.read()

        chunks = [raw[offset : offset + CHUNK_SIZE] for offset in range(0, len(raw), CHUNK_SIZE)]
        if not chunks:
            chunks = [b""]

        await self._upload_chunks(
            file_id,
            chunks,
            base64.b64decode(file_key_b64),
            int(record["total_chunks"]),
        )
        await update_file(file_id, complete=1)

    # -----------------------------------------------------------------------
    # Receive
    # -----------------------------------------------------------------------

    async def receive(self, file_id: str, file_meta: dict) -> str:
        """Download and reassemble an inbound file.

        Connects to the server TCP relay, downloads each encrypted chunk,
        decrypts and verifies it, reassembles the original file, and writes
        it to the downloads directory.

        Args:
            file_id:   File transfer ID from the FILE_INIT packet.
            file_meta: Metadata dict from the FILE_INIT packet, must include
                       keys: name, size, total_chunks, from_user, file_key.

        Returns:
            Absolute path to the saved file.

        Raises:
            ValueError: If a chunk fails authentication (tampered data).
            OSError: On network or filesystem errors.
        """
        filename     = file_meta["name"]
        total_chunks = int(file_meta["total_chunks"])
        from_user    = file_meta["from_user"]
        file_key_b64 = file_meta.get("file_key", "")
        if not file_key_b64:
            record = await get_file(file_id)
            file_key_b64 = record.get("file_key", "") if record else ""
        file_key     = base64.b64decode(file_key_b64)

        # Prepare destination path
        dest_dir = os.path.join(DOWNLOADS_DIR, from_user)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, filename)

        chunks: dict[int, bytes] = {}  # chunk_index → plaintext

        reader, writer = await asyncio.open_connection(self._tcp_host, self._tcp_port)
        try:
            # Handshake
            writer.write(f"DOWNLOAD {file_id}\n".encode())
            await writer.drain()

            for _ in range(total_chunks):
                # Read header: chunk_index (4) + data_len (4)
                hdr_raw = await reader.readexactly(_HDR_STRUCT.size)
                chunk_index, data_len = _HDR_STRUCT.unpack(hdr_raw)

                # Read 64-byte ASCII checksum
                checksum_raw = await reader.readexactly(_CHECKSUM_LEN)
                expected_checksum = checksum_raw.decode("ascii")

                # Read encrypted payload
                payload = await reader.readexactly(data_len)

                nonce      = payload[:_NONCE_LEN]
                tag        = payload[_NONCE_LEN : _NONCE_LEN + _TAG_LEN]
                ciphertext = payload[_NONCE_LEN + _TAG_LEN :]

                plaintext = decrypt_chunk(ciphertext, nonce, tag, file_key)

                # Verify plaintext integrity
                actual_checksum = chunk_checksum(plaintext)
                if actual_checksum != expected_checksum:
                    raise ValueError(
                        f"Checksum mismatch for chunk {chunk_index} of file {file_id}"
                    )

                chunks[chunk_index] = plaintext
                await update_file(file_id, received_chunks=len(chunks))
                if self._on_progress:
                    await self._on_progress(file_id, len(chunks), total_chunks)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        # Reassemble in order
        reassembled = b"".join(chunks[i] for i in sorted(chunks))

        with open(dest_path, "wb") as fh:
            fh.write(reassembled)

        await mark_complete(file_id, dest_path)
        log.info("File received: id=%s saved=%s", file_id, dest_path)
        return dest_path

    # -----------------------------------------------------------------------
    # Cancel
    # -----------------------------------------------------------------------

    async def cancel(self, file_id: str) -> None:
        """Cancel an in-progress or pending file transfer.

        Sends FILE_CANCEL to the server and updates the local DB.

        Args:
            file_id: The file transfer ID to cancel.
        """
        await self._ws.send(
            make_packet(
                MsgType.FILE_CANCEL,
                file_id=file_id,
                **{"from": self.me},
                timestamp=now(),
            )
        )
        try:
            await update_file(file_id, complete=-1)  # -1 = cancelled
        except Exception:
            pass
        self._active.pop(file_id, None)
        log.info("File transfer cancelled: id=%s", file_id)

    # -----------------------------------------------------------------------
    # Inbound packet handler
    # -----------------------------------------------------------------------

    async def handle_incoming(self, pkt: dict) -> None:
        """Dispatch an inbound file-related packet.

        Handles FILE_INIT (new inbound offer), FILE_DONE (sender finished),
        FILE_ACK (server confirmation), and FILE_CANCEL (transfer aborted).

        Args:
            pkt: Parsed packet dict from the server.
        """
        ptype = pkt.get("type")

        if ptype == MsgType.FILE_INIT:
            await self._handle_file_init(pkt)

        elif ptype == MsgType.FILE_DONE:
            file_id   = pkt.get("file_id", "")
            from_user = pkt.get("from", "")
            log.info("FILE_DONE received: id=%s from=%s", file_id, from_user)

        elif ptype == MsgType.FILE_ACK:
            file_id = pkt.get("file_id", "")
            log.debug("FILE_ACK received: id=%s", file_id)

        elif ptype == MsgType.FILE_CANCEL:
            file_id   = pkt.get("file_id", "")
            from_user = pkt.get("from", "")
            log.info("FILE_CANCEL received: id=%s from=%s", file_id, from_user)
            try:
                await update_file(file_id, complete=-1)
            except Exception:
                pass
            self._active.pop(file_id, None)

        else:
            log.debug("FileHandler ignoring packet type=%s", ptype)

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _handle_file_init(self, pkt: dict) -> None:
        """Process an inbound FILE_INIT: persist metadata and fire callback."""
        file_id      = pkt.get("file_id", new_id())
        from_user    = pkt.get("from", "")
        filename     = pkt.get("name", "unknown")
        size         = int(pkt.get("size", 0))
        total_chunks = int(pkt.get("total_chunks", 1))
        file_key_b64 = pkt.get("file_key", "")
        encrypted_key = pkt.get("encrypted_key")
        ts           = pkt.get("timestamp") or now()
        if not file_key_b64 and encrypted_key:
            if self._ctx is None:
                raise ValueError("Cannot decrypt file key without unlocked private key")
            file_key_b64 = decrypt_message(encrypted_key, self._ctx.private_key)

        # Persist in DB so we can resume incomplete receives
        await save_file(
            id=file_id,
            name=filename,
            size=size,
            total_chunks=total_chunks,
            from_user=from_user,
            to_user=self.me,
            local_path="",
            timestamp=ts,
            direction="recv",
            file_key=file_key_b64,
        )

        # Stash key for the receive path
        self._active[file_id] = {
            "file_key": base64.b64decode(file_key_b64) if file_key_b64 else b"",
            "total_chunks": total_chunks,
            "from_user": from_user,
            "name": filename,
        }

        # Inform the UI / application layer
        if self._on_receive:
            file_meta = {
                "file_id": file_id,
                "name": filename,
                "size": size,
                "total_chunks": total_chunks,
                "from_user": from_user,
                "file_key": file_key_b64,
                "encrypted_key": encrypted_key,
                "timestamp": ts,
            }
            try:
                await self._on_receive(file_meta)
            except Exception as exc:
                log.error("on_receive callback raised: %s", exc)

        # Send ACK to confirm we received the metadata
        await self._ws.send(
            make_packet(
                MsgType.FILE_ACK,
                file_id=file_id,
                to=from_user,
                **{"from": self.me},
                timestamp=now(),
            )
        )

    async def _upload_chunks(
        self,
        file_id: str,
        chunks: list[bytes],
        file_key: bytes,
        total_chunks: int,
        max_attempts: int = 3,
    ) -> None:
        """Upload encrypted chunks, reconnecting and skipping server-held chunks."""
        sent = 0
        attempt = 0
        while sent < total_chunks and attempt < max_attempts:
            attempt += 1
            reader, writer = await asyncio.open_connection(self._tcp_host, self._tcp_port)
            try:
                writer.write(f"{file_id}\n".encode())
                await writer.drain()

                have_line = await reader.readline()
                have: set[int] = set()
                if have_line.startswith(b"HAVE "):
                    payload = have_line.decode().rstrip("\n").removeprefix("HAVE ").strip()
                    if payload:
                        have = {int(idx) for idx in payload.split(",") if idx}
                sent = len(have)

                for idx, chunk_plaintext in enumerate(chunks):
                    if idx in have:
                        if self._on_progress:
                            await self._on_progress(file_id, sent, total_chunks)
                        continue

                    checksum = chunk_checksum(chunk_plaintext)
                    ciphertext, nonce, tag = encrypt_chunk(chunk_plaintext, file_key)
                    payload = nonce + tag + ciphertext

                    writer.write(_HDR_STRUCT.pack(idx, len(payload)))
                    writer.write(checksum.encode("ascii"))
                    writer.write(payload)
                    await writer.drain()

                    ack = await reader.readline()
                    if ack.strip() not in (b"ACK", b"DONE"):
                        raise OSError(f"Unexpected ACK for chunk {idx}: {ack!r}")

                    sent += 1
                    await update_file(file_id, received_chunks=sent)
                    if self._on_progress:
                        await self._on_progress(file_id, sent, total_chunks)

                    if ack.strip() == b"DONE":
                        return

                done = await reader.readline()
                if done.strip() in (b"", b"DONE"):
                    return
            except (OSError, asyncio.IncompleteReadError):
                if attempt >= max_attempts:
                    raise
                await asyncio.sleep(float(attempt))
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
