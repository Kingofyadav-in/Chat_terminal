import asyncio


async def open_tcp_connection(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open the TCP relay connection used by file transfers."""
    return await asyncio.open_connection(host, port)
