"""
End-to-end send/receive test — verifies that text messages flow correctly
through the full stack: WebSocket → server routing → recipient → decryption.

Each test spins up its own in-process WS server so there are no
event-loop / fixture-scope conflicts.

Covers:
  1. Plaintext send (no crypto_ctx / user skipped encryption)
  2. Encrypted send (E2E with X25519 keypair)
  3. MessageHandler.send() with crypto_ctx=None must NOT raise
  4. MessageHandler.send() with crypto_ctx but no pinned pubkey falls back to plaintext
"""

import asyncio
import base64
import os
import sys
import tempfile

import pytest
import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shared.protocol import MsgType, make_packet, parse_packet


# ── helpers ──────────────────────────────────────────────────────────────────

async def _recv_type(ws, want: MsgType, timeout: float = 5.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for {want}")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        pkt = parse_packet(raw)
        if pkt.get("type") in (want, want.value if hasattr(want, "value") else want):
            return pkt


async def _register(ws, username: str, password: str,
                    public_key: str | None = None) -> dict:
    kw: dict = dict(username=username, password=password)
    if public_key:
        kw["public_key"] = public_key
    await ws.send(make_packet(MsgType.AUTH_REGISTER, **kw))
    return await _recv_type(ws, MsgType.AUTH_RESPONSE)


# ── per-test server context manager ──────────────────────────────────────────

class _TestServer:
    """Spins up a real WS + DB server on *port* for the duration of the test."""

    def __init__(self, port: int):
        self._port = port
        self._tmp  = tempfile.mkdtemp()
        self._srv  = None
        self._old_db_env: str | None = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self._port}"

    async def __aenter__(self):
        db_path = os.path.join(self._tmp, "server.db")
        self._old_db_env = os.environ.get("CT_DB")
        os.environ["CT_DB"] = db_path

        # Reload config + schema so they pick up the new DB path
        import importlib
        import server.config
        importlib.reload(server.config)
        import server.db.schema as schema
        importlib.reload(schema)
        import server.db.users as users
        importlib.reload(users)
        import server.auth as auth_mod
        importlib.reload(auth_mod)
        import server.router as router_mod
        importlib.reload(router_mod)
        import server.offline as offline_mod
        importlib.reload(offline_mod)
        import server.ws_server as ws_mod
        importlib.reload(ws_mod)

        from server.db.schema import init_db
        await init_db()

        from server.ws_server import handle
        self._srv = await websockets.serve(handle, "127.0.0.1", self._port)
        return self

    async def __aexit__(self, *_):
        if self._srv:
            self._srv.close()
            await self._srv.wait_closed()
        if self._old_db_env is None:
            os.environ.pop("CT_DB", None)
        else:
            os.environ["CT_DB"] = self._old_db_env


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_plaintext_send_and_receive():
    """Alice sends plaintext (no E2E keys). Bob receives the correct text."""
    async with _TestServer(19780):
        async with websockets.connect("ws://127.0.0.1:19780") as alice_ws, \
                   websockets.connect("ws://127.0.0.1:19780") as bob_ws:

            r = await _register(alice_ws, "alice_pt", "pw1")
            assert r["success"], f"Alice register failed: {r}"

            r = await _register(bob_ws, "bob_pt", "pw2")
            assert r["success"], f"Bob register failed: {r}"

            mid = "test-plain-001"
            await alice_ws.send(make_packet(
                MsgType.MESSAGE,
                id=mid,
                **{"from": "alice_pt"},
                to="bob_pt",
                payload={"text": "hello bob plaintext"},
            ))

            delivery = await _recv_type(alice_ws, MsgType.DELIVERY)
            assert delivery["msg_id"] == mid
            assert delivery["status"] in ("delivered", "queued")
            print(f"\n[1] DELIVERY ack: status={delivery['status']}")

            msg = await _recv_type(bob_ws, MsgType.MESSAGE)
            assert msg["payload"]["text"] == "hello bob plaintext"
            assert msg["from"] == "alice_pt"
            print(f"[2] Bob received: {msg['payload']['text']!r}  ✓")


@pytest.mark.asyncio
async def test_encrypted_send_and_receive():
    """Alice encrypts with Bob's X25519 key. Bob decrypts to original plaintext."""
    from client.crypto.keygen import generate_keypair
    from client.crypto.encrypt import encrypt_message, decrypt_message

    alice_priv, alice_pub = generate_keypair()
    bob_priv,   bob_pub   = generate_keypair()
    alice_pub_b64 = base64.b64encode(alice_pub).decode()
    bob_pub_b64   = base64.b64encode(bob_pub).decode()

    async with _TestServer(19781):
        async with websockets.connect("ws://127.0.0.1:19781") as alice_ws, \
                   websockets.connect("ws://127.0.0.1:19781") as bob_ws:

            r = await _register(alice_ws, "alice_enc", "pw1", public_key=alice_pub_b64)
            assert r["success"]
            r = await _register(bob_ws, "bob_enc", "pw2", public_key=bob_pub_b64)
            assert r["success"]

            # Alice fetches Bob's public key from server
            await alice_ws.send(make_packet(MsgType.KEY_REQUEST, username="bob_enc"))
            key_resp = await _recv_type(alice_ws, MsgType.KEY_RESPONSE)
            assert key_resp["success"], f"Key fetch failed: {key_resp}"
            fetched_bob_pub = base64.b64decode(key_resp["public_key"])
            assert fetched_bob_pub == bob_pub
            print(f"\n[1] Alice fetched Bob's pubkey  ✓")

            # Encrypt and send
            plaintext = "hello bob encrypted!"
            payload = encrypt_message(plaintext, fetched_bob_pub)
            payload["encrypted"] = True
            mid = "test-enc-001"
            await alice_ws.send(make_packet(
                MsgType.MESSAGE,
                id=mid,
                **{"from": "alice_enc"},
                to="bob_enc",
                payload=payload,
            ))

            delivery = await _recv_type(alice_ws, MsgType.DELIVERY)
            assert delivery["msg_id"] == mid
            print(f"[2] Delivery ack: {delivery['status']}  ✓")

            msg = await _recv_type(bob_ws, MsgType.MESSAGE)
            assert "ephemeral_pubkey" in msg["payload"]
            decrypted = decrypt_message(msg["payload"], bob_priv)
            assert decrypted == plaintext
            print(f"[3] Bob decrypted: {decrypted!r}  ✓")


@pytest.mark.asyncio
async def test_message_handler_send_no_crypto_ctx(tmp_path):
    """MessageHandler.send() with crypto_ctx=None must NOT raise — sends plaintext."""
    import importlib

    db_path = str(tmp_path / "client_alice.db")
    os.environ["CT_CLIENT_DB"] = db_path

    import client.config as ccfg
    importlib.reload(ccfg)
    import client.db.schema as cschema
    importlib.reload(cschema)
    import client.db.messages as cmsg_mod
    importlib.reload(cmsg_mod)
    import client.db.contacts as ccontacts_mod
    importlib.reload(ccontacts_mod)

    from client.db.schema import init_db as client_init_db
    await client_init_db()

    from client.connection.ws_client import WSClient
    from client.handlers.message import MessageHandler

    async with _TestServer(19782):
        # Pre-register bob on the server side
        async with websockets.connect("ws://127.0.0.1:19782") as bob_ws:
            r = await _register(bob_ws, "msg_h_bob", "pw")
            assert r["success"]

            received: list[dict] = []
            ws_client = WSClient(on_message=lambda p: received.append(p) or asyncio.sleep(0))
            ws_task = asyncio.create_task(ws_client.connect("ws://127.0.0.1:19782"))

            # Wait for connection
            for _ in range(50):
                if ws_client.connected:
                    break
                await asyncio.sleep(0.05)
            assert ws_client.connected, "WSClient did not connect"

            try:
                # Register alice over the WSClient
                await ws_client.send(make_packet(
                    MsgType.AUTH_REGISTER, username="msg_h_alice", password="pw"))
                await asyncio.sleep(0.3)

                handler = MessageHandler(
                    ws_client,
                    self_user="msg_h_alice",
                    crypto_ctx=None,        # <-- was raising before the fix
                    on_new_message=None,
                )

                # Must NOT raise
                mid = await handler.send("msg_h_bob", "plaintext no crypto")
                print(f"\n[1] send() returned mid={mid[:8]}  ✓")

                await asyncio.sleep(0.2)

                # Reload to pick up the correct DB path
                importlib.reload(cmsg_mod)
                history = await cmsg_mod.get_history("msg_h_bob", "msg_h_alice")
                assert any(m["id"] == mid for m in history), \
                    f"Sent message not in DB. History: {history}"
                row = next(m for m in history if m["id"] == mid)
                assert row["plaintext"] == "plaintext no crypto"
                assert row["from_user"] == "msg_h_alice"
                print(f"[2] Message in local DB: {row['plaintext']!r}  ✓")

            finally:
                ws_client.stop()
                ws_task.cancel()
                await asyncio.gather(ws_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_message_handler_send_ctx_no_pubkey_falls_back(tmp_path):
    """MessageHandler.send() with crypto_ctx but no pinned pubkey must NOT raise."""
    import importlib
    from types import SimpleNamespace

    db_path = str(tmp_path / "client_fallback.db")
    os.environ["CT_CLIENT_DB"] = db_path

    import client.config as ccfg
    importlib.reload(ccfg)
    import client.db.schema as cschema
    importlib.reload(cschema)
    import client.db.messages as cmsg_mod
    importlib.reload(cmsg_mod)

    from client.db.schema import init_db as client_init_db
    await client_init_db()

    from client.crypto.keygen import generate_keypair
    from client.connection.ws_client import WSClient
    from client.handlers.message import MessageHandler

    priv, pub = generate_keypair()
    ctx = SimpleNamespace(private_key=priv)

    async with _TestServer(19783):
        async with websockets.connect("ws://127.0.0.1:19783") as bob_ws:
            r = await _register(bob_ws, "fb_bob", "pw")
            assert r["success"]

            ws_client = WSClient(on_message=lambda p: None)
            ws_task = asyncio.create_task(ws_client.connect("ws://127.0.0.1:19783"))
            for _ in range(50):
                if ws_client.connected:
                    break
                await asyncio.sleep(0.05)
            assert ws_client.connected

            try:
                await ws_client.send(make_packet(
                    MsgType.AUTH_REGISTER, username="fb_alice", password="pw"))
                await asyncio.sleep(0.3)

                handler = MessageHandler(
                    ws_client,
                    self_user="fb_alice",
                    crypto_ctx=ctx,     # has ctx but fb_bob pubkey is NOT in local DB
                    on_new_message=None,
                )

                # Must NOT raise — falls back to plaintext
                mid = await handler.send("fb_bob", "fallback plaintext")
                print(f"\n[1] send() with ctx+no-pubkey returned mid={mid[:8]}  ✓")

                await asyncio.sleep(0.2)
                importlib.reload(cmsg_mod)
                history = await cmsg_mod.get_history("fb_bob", "fb_alice")
                assert any(m["id"] == mid for m in history), "Message not saved to DB"
                print(f"[2] Fallback message in DB  ✓")

            finally:
                ws_client.stop()
                ws_task.cancel()
                await asyncio.gather(ws_task, return_exceptions=True)
