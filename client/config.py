import os

SERVER_URL      = os.getenv("CT_SERVER", "ws://localhost:8765")
SERVER_TCP_HOST = os.getenv("CT_TCP_HOST", "127.0.0.1")
SERVER_TCP_PORT = int(os.getenv("CT_TCP_PORT", "8766"))

_CLIENT_DB = os.getenv("CT_CLIENT_DB")
_CLIENT_PROFILE = os.getenv("CT_CLIENT_PROFILE")
if _CLIENT_DB:
    DB_PATH = os.path.expanduser(_CLIENT_DB)
elif _CLIENT_PROFILE:
    DB_PATH = os.path.expanduser(
        f"~/.chatterminal/profiles/{_CLIENT_PROFILE}/chatterminal.db"
    )
else:
    DB_PATH = os.path.expanduser("~/.chatterminal/chatterminal.db")

DOWNLOADS_DIR   = os.path.expanduser("~/.chatterminal/downloads")
KEYS_DIR        = os.path.expanduser("~/.chatterminal/keys")

# Reconnect delays in seconds: 2 4 8 16 32 60 60 60 ...
RECONNECT_DELAYS = [2, 4, 8, 16, 32, 60]
