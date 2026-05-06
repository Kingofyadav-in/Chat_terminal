import asyncio
import logging

import websockets

from shared.protocol import MsgType, make_packet, parse_packet
from client.config import SERVER_URL
from client.connection.reconnect import backoff_delay

log = logging.getLogger("ct.client.ws")


class WSClient:
    def __init__(self, on_message, on_connect=None, on_disconnect=None):
        self.on_message    = on_message
        self.on_connect    = on_connect
        self.on_disconnect = on_disconnect
        self.ws            = None
        self._running      = False
        self._send_q: asyncio.Queue = asyncio.Queue()
        self.connected     = False
        self.attempt       = 0

    async def connect(self, url: str = SERVER_URL) -> None:
        self._running = True
        delay_idx = 0
        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    self.ws        = ws
                    self.connected = True
                    self.attempt   = 0
                    delay_idx      = 0
                    if self.on_connect:
                        await self.on_connect()

                    sender = asyncio.create_task(self._sender())
                    try:
                        async for raw in ws:
                            try:
                                pkt = parse_packet(raw)
                                await self.on_message(pkt)
                            except Exception as e:
                                log.debug(f"msg parse error: {e}")
                    finally:
                        sender.cancel()

            except (
                websockets.exceptions.ConnectionClosed,
                OSError,
                ConnectionRefusedError,
            ):
                pass
            finally:
                self.ws        = None
                self.connected = False
                if self.on_disconnect:
                    await self.on_disconnect()

            if not self._running:
                break

            delay = backoff_delay(delay_idx)
            self.attempt  += 1
            delay_idx     += 1
            await asyncio.sleep(delay)

    async def _sender(self) -> None:
        while True:
            data = await self._send_q.get()
            if self.ws:
                try:
                    await self.ws.send(data)
                except Exception as e:
                    log.debug(f"send error: {e}")

    async def send(self, data: str) -> None:
        await self._send_q.put(data)

    async def send_pkt(self, ptype: MsgType, **kwargs) -> None:
        await self.send(make_packet(ptype, **kwargs))

    def stop(self) -> None:
        self._running = False
