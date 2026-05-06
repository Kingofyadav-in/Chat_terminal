"""In-memory connection registry: username → websocket."""

_connections: dict = {}


def register(username: str, ws) -> None:
    _connections[username] = ws


def unregister(username: str) -> None:
    _connections.pop(username, None)


def get(username: str):
    return _connections.get(username)


def online_users() -> list[str]:
    return list(_connections.keys())


def is_online(username: str) -> bool:
    return username in _connections
