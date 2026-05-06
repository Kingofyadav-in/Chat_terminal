import base64
import hashlib
import hmac
import os

import bcrypt
from shared.utils import now, new_id
from shared.constants import SESSION_TTL
from server.db import users as udb

_PBKDF2_ALG = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 310_000
_SALT_LEN = 16


def _hash_password(password: str) -> str:
    salt = os.urandom(_SALT_LEN)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return "$".join(
        (
            _PBKDF2_ALG,
            str(_PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def _verify_password(password: str, stored: str) -> bool:
    if stored.startswith(f"{_PBKDF2_ALG}$"):
        try:
            _, iter_s, salt_b64, digest_b64 = stored.split("$", 3)
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                base64.b64decode(salt_b64),
                int(iter_s),
            )
            return hmac.compare_digest(digest, base64.b64decode(digest_b64))
        except Exception:
            return False

    # Backward compatibility for local databases created before PBKDF2.
    try:
        return bcrypt.checkpw(password.encode(), stored.encode())
    except Exception:
        return False


async def register(username: str, password: str, public_key: str | None = None) -> dict:
    if await udb.get_by_username(username):
        return {"success": False, "error": "USER_EXISTS"}

    hashed = _hash_password(password)
    uid = new_id()
    ts = now()
    await udb.create_user(uid, username, hashed, ts, public_key)

    token = new_id()
    await udb.create_session(token, uid, username, ts, ts + SESSION_TTL)
    return {"success": True, "token": token, "username": username}


async def login(username: str, password: str) -> dict:
    user = await udb.get_by_username(username)
    if not user:
        return {"success": False, "error": "AUTH_FAILED"}

    if not _verify_password(password, user["password"]):
        return {"success": False, "error": "AUTH_FAILED"}

    ts = now()
    token = new_id()
    await udb.create_session(token, user["id"], username, ts, ts + SESSION_TTL)
    await udb.update_last_seen(username, ts)
    return {"success": True, "token": token, "username": username}


async def validate_token(token: str) -> dict | None:
    return await udb.get_session(token)
