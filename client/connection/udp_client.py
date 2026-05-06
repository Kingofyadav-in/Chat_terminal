import asyncio
import socket


async def open_udp_endpoint(local_port: int = 0):
    """Open a UDP datagram endpoint for audio/probe traffic."""
    loop = asyncio.get_running_loop()
    return await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol,
        local_addr=("0.0.0.0", local_port),
        family=socket.AF_INET,
    )
