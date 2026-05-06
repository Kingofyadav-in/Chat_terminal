"""Asyncio TCP server for file chunk uploads.

Protocol (per connection):
  1. Client sends: <36-char UUID file_id>\\n for upload or DOWNLOAD <file_id>\\n
  2. For each chunk:
       [4B chunk_index big-endian uint32]
       [4B data_length big-endian uint32]
       [64B checksum as ASCII hex]
       [data_length bytes of chunk data]
  3. After each chunk server sends: b"ACK\\n"
  4. When all chunks have been received server sends: b"DONE\\n"
     and closes the connection.
"""

import asyncio
import json
import logging
import struct
from typing import Optional

import server.files as files_module
import server.router as router
from shared.protocol import make_packet
from shared.utils import new_id, now

logger = logging.getLogger(__name__)

# Number of bytes that make up the per-chunk fixed header:
#   4 (chunk_index) + 4 (data_length) + 64 (checksum hex)
_CHUNK_HEADER_SIZE = 72
_CHECKSUM_LEN = 64


class TCPServer:
    """Asyncio TCP server that accepts file chunk uploads."""

    def __init__(self) -> None:
        self._server: Optional[asyncio.AbstractServer] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, host: str, port: int) -> None:
        """Bind and start listening for TCP connections."""
        self._server = await asyncio.start_server(
            self.handle_client, host, port,
            reuse_address=True, reuse_port=True,
        )
        addr = self._server.sockets[0].getsockname()
        logger.info("TCP file server listening on %s:%d", addr[0], addr[1])
        # Do NOT call serve_forever() here so the caller can await other tasks.
        # The server will accept connections as long as the event loop runs.

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle one upload connection end-to-end."""
        peer = writer.get_extra_info("peername")
        logger.debug("TCP connection from %s", peer)
        try:
            await self._process_upload(reader, writer)
        except Exception as exc:
            logger.warning("Error handling TCP client %s: %s", peer, exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def stop(self) -> None:
        """Stop accepting new connections."""
        if self._server is not None:
            self._server.close()
            self._server = None
            logger.info("TCP file server stopped")

    # ------------------------------------------------------------------
    # Upload processing
    # ------------------------------------------------------------------

    async def _read_exactly(
        self, reader: asyncio.StreamReader, n: int
    ) -> bytes:
        """Read exactly *n* bytes; raises EOFError on premature close."""
        data = await reader.readexactly(n)
        return data

    async def _process_upload(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # Step 1: Read the upload file_id or DOWNLOAD command.
        id_line = await reader.readline()
        if not id_line:
            logger.warning("TCP client disconnected before sending file_id")
            return
        line = id_line.decode().strip()
        if line.startswith("DOWNLOAD "):
            await self._process_download(line.removeprefix("DOWNLOAD ").strip(), writer)
            return

        file_id = line
        if len(file_id) != 36:
            logger.warning("Invalid file_id received: %r", file_id)
            return

        file_meta = None
        for _ in range(50):
            file_meta = await files_module.get_file(file_id)
            if file_meta is not None:
                break
            await asyncio.sleep(0.1)
        if file_meta is None:
            logger.warning("Unknown file_id: %s", file_id)
            return

        total_chunks: int = file_meta["total_chunks"]
        from server.db.files import count_delivered, get_chunks

        existing = await get_chunks(file_id)
        existing_indices = sorted(chunk["chunk_index"] for chunk in existing)
        writer.write(("HAVE " + ",".join(str(i) for i in existing_indices) + "\n").encode())
        await writer.drain()

        # ----------------------------------------------------------------
        # Step 2: Receive chunks until all are in.
        # ----------------------------------------------------------------
        while await count_delivered(file_id) < total_chunks:
            # Read the fixed-size header.
            try:
                header = await self._read_exactly(reader, _CHUNK_HEADER_SIZE)
            except asyncio.IncompleteReadError:
                logger.warning(
                    "Connection closed mid-transfer for file %s after %d/%d chunks",
                    file_id,
                    await count_delivered(file_id),
                    total_chunks,
                )
                return

            chunk_index = struct.unpack_from(">I", header, 0)[0]
            data_length = struct.unpack_from(">I", header, 4)[0]
            checksum = header[8 : 8 + _CHECKSUM_LEN].decode("ascii").strip()

            # Read chunk data.
            try:
                data = await self._read_exactly(reader, data_length)
            except asyncio.IncompleteReadError:
                logger.warning(
                    "Incomplete chunk data for file %s chunk %d",
                    file_id,
                    chunk_index,
                )
                return

            # Persist chunk to disk + DB.
            await files_module.store_chunk(file_id, chunk_index, data, checksum)

            # Acknowledge this chunk.
            writer.write(b"ACK\n")
            await writer.drain()

        # ----------------------------------------------------------------
        # Step 3: All chunks received — notify sender and recipient.
        # ----------------------------------------------------------------
        writer.write(b"DONE\n")
        await writer.drain()

        logger.info(
            "File %s fully received (%d chunks)", file_id, total_chunks
        )

        # Notify the recipient over WebSocket.
        recipient = file_meta.get("recipient")
        if recipient:
            # Build the notification packet once — used for both online and
            # offline delivery paths.
            notify_pkt = make_packet(
                # make_packet accepts a plain string for the type field so we
                # avoid importing MsgType just for this new packet type.
                "FILE_DONE",  # type: ignore[arg-type]
                file_id=file_id,
                name=file_meta.get("name"),
                size=file_meta.get("size"),
                uploader=file_meta.get("uploader"),
                encrypted_key=file_meta.get("encrypted_key"),
            )
            ws = router.get(recipient)
            if ws is not None:
                try:
                    await ws.send(notify_pkt)
                except Exception as exc:
                    logger.warning(
                        "Failed to notify recipient %s: %s", recipient, exc
                    )
            else:
                # Recipient is offline; enqueue notification for later flush.
                import server.offline as offline  # lazy to avoid circular imports
                pkt_dict = json.loads(notify_pkt)
                await offline.enqueue(pkt_dict["id"], recipient, notify_pkt)

    async def _process_download(self, file_id: str, writer: asyncio.StreamWriter) -> None:
        """Stream all stored encrypted chunks for *file_id* to a downloader."""
        if len(file_id) != 36:
            logger.warning("Invalid download file_id received: %r", file_id)
            return

        file_meta = await files_module.get_file(file_id)
        if file_meta is None:
            logger.warning("Download requested for unknown file_id: %s", file_id)
            return

        from server.db.files import get_chunks

        chunks = await get_chunks(file_id)
        for chunk in chunks:
            chunk_index = chunk["chunk_index"]
            data = await files_module.get_chunk(file_id, chunk_index)
            if data is None:
                logger.warning("Missing chunk data for file %s chunk %d", file_id, chunk_index)
                return

            checksum = chunk["checksum"].encode("ascii")
            if len(checksum) != _CHECKSUM_LEN:
                logger.warning("Invalid checksum length for file %s chunk %d", file_id, chunk_index)
                return

            writer.write(struct.pack(">II", chunk_index, len(data)))
            writer.write(checksum)
            writer.write(data)
            await writer.drain()
