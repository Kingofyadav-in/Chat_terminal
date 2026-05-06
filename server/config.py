import os

WS_HOST          = os.getenv("CT_HOST", "0.0.0.0")
WS_PORT          = int(os.getenv("CT_PORT", "8765"))
TCP_HOST         = os.getenv("CT_TCP_HOST", WS_HOST)
TCP_PORT         = int(os.getenv("CT_TCP_PORT", "8766"))
UDP_RELAY_HOST   = os.getenv("CT_UDP_RELAY_HOST", WS_HOST)
UDP_RELAY_PORT   = int(os.getenv("CT_UDP_RELAY_PORT", "8767"))
DB_PATH          = os.path.expanduser(
    os.getenv("CT_DB", "~/.chatterminal_server/server.db")
)
MAX_CONNECTIONS  = 1000
PING_INTERVAL    = 30
PING_TIMEOUT     = 10
