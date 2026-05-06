import asyncio
import base64
import logging
from types import SimpleNamespace

from shared.protocol import MsgType, make_packet
from shared.utils import new_id
from client.db.contacts import get_account, save_account, update_token, get_my_keys
from client.config import SERVER_URL
from client.crypto.keygen import (
    decrypt_private_key,
    encrypt_private_key,
    generate_keypair,
)

log = logging.getLogger("ct.client.auth")


class AuthHandler:
    def __init__(self, ws_client):
        self._ws     = ws_client
        self._event  = asyncio.Event()
        self._result: dict | None = None

    def handle(self, pkt: dict) -> None:
        self._result = pkt
        self._event.set()

    async def _wait(self) -> dict:
        self._event.clear()
        await asyncio.wait_for(self._event.wait(), timeout=10.0)
        return self._result  # type: ignore[return-value]

    async def register(self, username: str, password: str) -> dict:
        private_key, public_key = generate_keypair()
        public_key_b64 = base64.b64encode(public_key).decode("ascii")
        encrypted_private_key = encrypt_private_key(private_key, password)
        await self._ws.send(
            make_packet(
                MsgType.AUTH_REGISTER,
                username=username,
                password=password,
                public_key=public_key_b64,
            )
        )
        result = await self._wait()
        if result.get("success"):
            await save_account(
                new_id(),
                username,
                SERVER_URL,
                result["token"],
                private_key=encrypted_private_key,
                public_key=public_key_b64,
            )
            result["crypto_ctx"] = SimpleNamespace(private_key=private_key)
        return result

    async def login(self, username: str, password: str) -> dict:
        await self._ws.send(
            make_packet(MsgType.AUTH_LOGIN, username=username, password=password)
        )
        result = await self._wait()
        if result.get("success"):
            account = await get_account()
            encrypted_private_key, public_key_b64 = await get_my_keys()
            if account and account.get("username") == username and encrypted_private_key:
                try:
                    private_key = decrypt_private_key(encrypted_private_key, password)
                    result["crypto_ctx"] = SimpleNamespace(private_key=private_key)
                except ValueError:
                    log.warning("Stored private key could not be unlocked for %s", username)
            else:
                encrypted_private_key = None
                public_key_b64 = None

            await save_account(
                new_id(),
                username,
                SERVER_URL,
                result["token"],
                private_key=encrypted_private_key,
                public_key=public_key_b64,
            )
        return result

    async def reauth(self, token: str) -> dict:
        await self._ws.send(make_packet(MsgType.AUTH_LOGIN, token=token))
        result = await self._wait()
        if result.get("success"):
            await update_token(result["token"])
        return result
