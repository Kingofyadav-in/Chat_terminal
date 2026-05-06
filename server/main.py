import asyncio
import argparse
import logging
import sys

import websockets

from server.config import WS_HOST, WS_PORT, TCP_HOST, TCP_PORT, UDP_RELAY_HOST, UDP_RELAY_PORT
from server.db.schema import init_db, clear_server_state
from server.ws_server import handle
from server.tcp_server import TCPServer
from server.files import cleanup_expired
from server.udp_relay import UDPRelay

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ct.server")


async def _run_server() -> None:
    await init_db()

    tcp_server = TCPServer()
    try:
        await tcp_server.start(TCP_HOST, TCP_PORT)
    except OSError as e:
        log.warning("TCP server could not bind %s:%d (%s) — file transfer disabled", TCP_HOST, TCP_PORT, e)
        tcp_server = None

    udp_relay = UDPRelay()
    try:
        await udp_relay.start(UDP_RELAY_HOST, UDP_RELAY_PORT)
    except OSError as e:
        log.warning("UDP relay could not bind %s:%d (%s) — audio relay disabled", UDP_RELAY_HOST, UDP_RELAY_PORT, e)
        udp_relay = None

    async def cleanup_loop() -> None:
        while True:
            deleted = await cleanup_expired()
            if deleted:
                log.info("Expired %d file transfer(s)", deleted)
            await asyncio.sleep(3600)

    cleanup_task = asyncio.create_task(cleanup_loop())
    log.info(f"ChatterTerminal server  ws://{WS_HOST}:{WS_PORT}")
    try:
        async with websockets.serve(handle, WS_HOST, WS_PORT):
            log.info("Ready. Press Ctrl-C to stop.")
            await asyncio.Future()
    finally:
        cleanup_task.cancel()
        if tcp_server:
            tcp_server.stop()
        if udp_relay:
            udp_relay.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="ct-server")
    parser.add_argument(
        "--reset-server",
        action="store_true",
        help="Clear all server-side users, sessions, rooms, files, and calls, then exit.",
    )
    args = parser.parse_args()

    if args.reset_server:
        asyncio.run(clear_server_state())
        log.info("Server state cleared.")
        return

    try:
        asyncio.run(_run_server())
    except KeyboardInterrupt:
        log.info("Server stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
