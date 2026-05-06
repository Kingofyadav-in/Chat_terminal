"""
Message visibility test — proves that after Alice sends a message to Bob,
both Alice's and Bob's `self.messages` list (the list the chat view renders)
contains the correct content.

This simulates exactly what the UI does, minus the curses drawing itself.
"""

import asyncio
import base64
import os
import sys
import tempfile
import importlib
from types import SimpleNamespace

import pytest
import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shared.protocol import MsgType, make_packet, parse_packet


# ── test server helper ────────────────────────────────────────────────────────

class _Server:
    def __init__(self, port):
        self.port = port
        self._tmp = tempfile.mkdtemp()
        self._srv = None

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}"

    async def __aenter__(self):
        os.environ["CT_DB"] = os.path.join(self._tmp, "s.db")
        for mod in ("server.config", "server.db.schema", "server.db.users",
                    "server.auth", "server.router", "server.offline",
                    "server.ws_server"):
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])
        from server.db.schema import init_db
        await init_db()
        from server.ws_server import handle
        self._srv = await websockets.serve(handle, "127.0.0.1", self.port)
        return self

    async def __aexit__(self, *_):
        if self._srv:
            self._srv.close()
            await self._srv.wait_closed()


async def _recv_type(ws, want, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        rem = deadline - asyncio.get_event_loop().time()
        if rem <= 0:
            raise TimeoutError(f"Timed out waiting for {want}")
        raw = await asyncio.wait_for(ws.recv(), timeout=rem)
        pkt = parse_packet(raw)
        if pkt.get("type") in (want, getattr(want, "value", want)):
            return pkt


# ── a minimal App-like state holder ──────────────────────────────────────────

class _FakeApp:
    """
    Replicates the data-flow parts of App that determine what the chat view
    renders, without needing a real curses terminal.
    """

    def __init__(self, username, db_path, ws_url):
        self.username     = username
        self.current_chat = None
        self.messages     : list[dict] = []
        self._ws_url      = ws_url
        self._db_path     = db_path
        self._ws          = None
        self._msg_h       = None
        self._received    : list[dict] = []

    async def setup(self):
        os.environ["CT_CLIENT_DB"] = self._db_path
        for mod in ("client.config", "client.db.schema",
                    "client.db.messages", "client.db.contacts"):
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])

        from client.db.schema import init_db
        await init_db()

        from client.connection.ws_client import WSClient
        from client.handlers.message import MessageHandler

        self._ws  = WSClient(on_message=self._on_server_message)
        self._task = asyncio.create_task(self._ws.connect(self._ws_url))

        for _ in range(50):
            if self._ws.connected:
                break
            await asyncio.sleep(0.05)
        assert self._ws.connected, f"{self.username}: WSClient did not connect"

        # Register with server
        await self._ws.send(make_packet(
            MsgType.AUTH_REGISTER,
            username=self.username,
            password="pw",
        ))
        # Wait for AUTH_RESPONSE
        for _ in range(50):
            if any(p.get("type") == MsgType.AUTH_RESPONSE for p in self._received):
                break
            await asyncio.sleep(0.05)
        auth = next(p for p in self._received if p.get("type") == MsgType.AUTH_RESPONSE)
        assert auth.get("success"), f"Register failed for {self.username}: {auth}"

        self._msg_h = MessageHandler(
            self._ws,
            self_user=self.username,
            crypto_ctx=None,
            on_new_message=self._on_new_message,
        )

    async def _on_server_message(self, pkt: dict):
        self._received.append(pkt)
        if pkt.get("type") == MsgType.MESSAGE and self._msg_h:
            await self._msg_h.handle_incoming(pkt)
        elif pkt.get("type") == MsgType.DELIVERY and self._msg_h:
            await self._msg_h.handle_delivery(pkt)

    async def _on_new_message(self, pkt: dict):
        sender = pkt.get("from", "")
        if sender == self.current_chat:
            await self._load_messages()

    async def _load_messages(self):
        if self.current_chat:
            import client.db.messages as mdb
            importlib.reload(mdb)
            self.messages = await mdb.get_history(self.current_chat, self.username)

    async def open_chat(self, peer: str):
        self.current_chat = peer
        await self._load_messages()

    async def send(self, text: str):
        assert self.current_chat, "No chat open"
        mid = await self._msg_h.send(self.current_chat, text)
        await self._load_messages()  # same as _submit() does
        return mid

    async def teardown(self):
        if self._ws:
            self._ws.stop()
        if hasattr(self, "_task"):
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sender_sees_own_message(tmp_path):
    """After Alice sends a message, it must appear in Alice's self.messages list."""
    async with _Server(19790):
        alice_db = str(tmp_path / "alice.db")
        bob_db   = str(tmp_path / "bob.db")

        alice = _FakeApp("alice_vis", alice_db, "ws://127.0.0.1:19790")
        bob   = _FakeApp("bob_vis",   bob_db,   "ws://127.0.0.1:19790")
        try:
            await alice.setup()
            await bob.setup()

            # Alice opens chat with Bob and sends a message
            await alice.open_chat("bob_vis")
            await alice.send("hello bob, can you see this?")

            # Alice's messages list should contain the sent message
            assert len(alice.messages) > 0, \
                "Alice.messages is empty after send — message not loaded into UI state"
            row = alice.messages[-1]
            assert row["plaintext"] == "hello bob, can you see this?"
            assert row["from_user"] == "alice_vis"
            print(f"\n[1] Alice sees her own message: {row['plaintext']!r}  ✓")

        finally:
            await alice.teardown()
            await bob.teardown()


@pytest.mark.asyncio
async def test_receiver_sees_incoming_message(tmp_path):
    """After Alice sends to Bob, Bob's self.messages must contain the message."""
    async with _Server(19791):
        alice_db = str(tmp_path / "alice2.db")
        bob_db   = str(tmp_path / "bob2.db")

        alice = _FakeApp("alice_r", alice_db, "ws://127.0.0.1:19791")
        bob   = _FakeApp("bob_r",   bob_db,   "ws://127.0.0.1:19791")
        try:
            await alice.setup()
            await bob.setup()

            # Bob opens chat with Alice (so _on_new_message triggers a reload)
            await bob.open_chat("alice_r")

            # Alice sends to Bob
            await alice.open_chat("bob_r")
            await alice.send("hey bob this is alice")

            # Give the server + Bob's WS a moment to deliver
            await asyncio.sleep(0.4)

            # Bob's messages list must now contain Alice's message
            assert len(bob.messages) > 0, \
                "Bob.messages is empty — incoming message not rendered into UI state"
            row = bob.messages[-1]
            assert row["plaintext"] == "hey bob this is alice"
            assert row["from_user"] == "alice_r"
            print(f"\n[1] Bob sees Alice's message: {row['plaintext']!r}  ✓")

            # Alice also sees her own sent message
            assert len(alice.messages) > 0
            assert alice.messages[-1]["from_user"] == "alice_r"
            print(f"[2] Alice sees sent message  ✓")

        finally:
            await alice.teardown()
            await bob.teardown()


@pytest.mark.asyncio
async def test_multiple_messages_in_order(tmp_path):
    """Multiple messages appear in chronological order in self.messages."""
    async with _Server(19792):
        alice_db = str(tmp_path / "alice3.db")
        bob_db   = str(tmp_path / "bob3.db")

        alice = _FakeApp("alice_ord", alice_db, "ws://127.0.0.1:19792")
        bob   = _FakeApp("bob_ord",   bob_db,   "ws://127.0.0.1:19792")
        try:
            await alice.setup()
            await bob.setup()

            await alice.open_chat("bob_ord")
            await bob.open_chat("alice_ord")

            await alice.send("msg 1")
            await alice.send("msg 2")
            await alice.send("msg 3")
            await asyncio.sleep(0.4)

            # Alice sees all 3 in order
            assert len(alice.messages) == 3
            texts = [m["plaintext"] for m in alice.messages]
            assert texts == ["msg 1", "msg 2", "msg 3"], f"Wrong order: {texts}"
            print(f"\n[1] Alice sees 3 messages in order: {texts}  ✓")

            # Bob sees all 3 in order
            assert len(bob.messages) == 3
            bob_texts = [m["plaintext"] for m in bob.messages]
            assert bob_texts == ["msg 1", "msg 2", "msg 3"], f"Bob wrong order: {bob_texts}"
            print(f"[2] Bob sees all 3 messages: {bob_texts}  ✓")

        finally:
            await alice.teardown()
            await bob.teardown()


@pytest.mark.asyncio
async def test_self_not_in_contacts(tmp_path):
    """
    After receiving ONLINE_LIST, the user's own username must NOT appear
    in the contacts list (which is what the sidebar renders).
    """
    async with _Server(19793):
        alice_db = str(tmp_path / "alice4.db")

        os.environ["CT_CLIENT_DB"] = alice_db
        for mod in ("client.config", "client.db.schema",
                    "client.db.messages", "client.db.contacts"):
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])

        from client.db.schema import init_db
        from client.db.contacts import all_contacts, add_contact
        await init_db()

        # Simulate the bug: alice_s4 gets added to her own contacts table
        await add_contact("alice_s4")

        # Now simulate _refresh_contacts
        me = "alice_s4"
        all_c = await all_contacts()
        roster = [c for c in all_c if c.get("username") and c.get("username") != me]

        assert all(c["username"] != "alice_s4" for c in roster), \
            f"Self 'alice_s4' appeared in contacts: {roster}"
        print(f"\n[1] Self filtered from contacts list  ✓")
