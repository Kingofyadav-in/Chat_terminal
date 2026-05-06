"""
Main application: coordinates UI, WS client, auth, and messaging.
"""
import asyncio
import base64
import curses
import logging
from types import SimpleNamespace

from shared.protocol import MsgType, make_packet
from shared.utils import new_id, now
from client.config import SERVER_URL
from client.config import SERVER_TCP_HOST, SERVER_TCP_PORT
from client.db.schema import init_db
from client.db.contacts import (
    get_account, save_account, update_token, clear_session_token, add_contact,
    set_status, all_contacts, store_public_key, get_public_key, get_my_keys,
    set_verified,
)
from client.crypto.fingerprint import safety_number
from client.crypto.keygen import decrypt_private_key
from client.crypto.encrypt import decrypt_message
from client.db.messages import (
    get_history,
    get_room_history,
    mark_read,
    unread_count,
    save as save_message,
    edit_message,
    delete_message,
)
from client.db.rooms import all_rooms, save_room, delete_room, update_last_message
from client.db.files import get_file, get_recent_files
from client.connection.ws_client import WSClient
from client.handlers.auth import AuthHandler
from client.handlers.message import MessageHandler
from client.handlers.room import RoomHandler
from client.handlers.file import FileHandler
from client.handlers.call import CallHandler
from client.db.calls import get_history as get_call_history
from client.ui.screen import Screen
from client.ui import status_bar, sidebar, chat_view, input_bar, divider
from client.ui.auth_screen import show as show_auth, unlock as show_unlock
from client.ui import verify_screen

log = logging.getLogger("ct.app")

_TICK = 0.05   # UI poll interval in seconds


class App:
    def __init__(self):
        self.screen        : Screen | None         = None
        self.ws            : WSClient | None        = None
        self.auth_h        : AuthHandler | None     = None
        self.msg_h         : MessageHandler | None  = None
        self.room_h        : RoomHandler | None     = None
        self.file_h        : FileHandler | None     = None
        self.call_h        : CallHandler | None     = None
        self.crypto_ctx    = None

        self.username      : str  = ""
        self.current_chat  : str | None = None
        self.messages      : list[dict] = []
        self.contacts      : list[dict] = []
        self.rooms         : list[dict] = []
        self.unread        : dict[str, int] = {}
        self.file_offers   : dict[str, dict] = {}
        self.file_progress : dict[str, tuple[int, int]] = {}
        self.pending_calls : dict[str, dict] = {}

        self.buf           : str  = ""
        self.cursor        : int  = 0
        self.conn_status   : str  = "offline"
        self.reconnect_att : int  = 0
        self.notice        : str  = ""
        self.notice_ts     : int  = 0
        self._dirty        : bool = True
        self._running      : bool = True

    # ── Entry point ────────────────────────────────────────────────────────

    async def run(self, stdscr) -> None:
        self.screen = Screen(stdscr)
        await init_db()

        # Try saved account first
        account = await get_account()
        creds   = None

        if account and account.get("session_token"):
            # Will reauth over WS after connecting
            self.username = account["username"]
            creds = {"token": account["session_token"], "username": self.username}
        else:
            creds = await show_auth(stdscr)
            if not creds:
                return

        # Build WS client before drawing main UI
        self.ws = WSClient(
            on_message    = self._on_server_message,
            on_connect    = self._on_connect,
            on_disconnect = self._on_disconnect,
        )
        self.auth_h = AuthHandler(self.ws)

        # Start networking + UI concurrently
        ws_task = asyncio.create_task(self.ws.connect(SERVER_URL))
        ui_task = asyncio.create_task(self._ui_loop(stdscr, creds))

        try:
            await asyncio.gather(ws_task, ui_task)
        except asyncio.CancelledError:
            pass
        finally:
            self.ws.stop()
            ws_task.cancel()

    # ── Connection callbacks ───────────────────────────────────────────────

    async def _on_connect(self) -> None:
        self.conn_status   = "connecting"
        self.reconnect_att = self.ws.attempt
        self._dirty        = True

    async def _on_disconnect(self) -> None:
        self.conn_status = "reconnecting"
        self._dirty      = True
        self._set_notice(f"Disconnected. Reconnecting (attempt {self.ws.attempt + 1})…")

    # ── Server message router ──────────────────────────────────────────────

    async def _on_server_message(self, pkt: dict) -> None:
        ptype = pkt.get("type")

        if ptype == MsgType.AUTH_RESPONSE:
            self.auth_h.handle(pkt)

        elif ptype == MsgType.MESSAGE:
            if self.msg_h:
                await self.msg_h.handle_incoming(pkt)

        elif ptype == MsgType.MESSAGE_EDIT:
            if self.msg_h:
                await self.msg_h.handle_edit(pkt)
            await self._load_messages()

        elif ptype == MsgType.MESSAGE_DELETE:
            if self.msg_h:
                await self.msg_h.handle_delete(pkt)
            await self._load_messages()

        elif ptype == MsgType.SYNC_RESPONSE:
            if self.msg_h:
                for raw in pkt.get("messages", []):
                    import json
                    try:
                        p = json.loads(raw)
                        await self._handle_synced_packet(p)
                    except Exception:
                        pass

        elif ptype == MsgType.STATUS:
            user   = pkt.get("user", "")
            status = pkt.get("status", "offline")
            await set_status(user, status)
            await self._refresh_contacts()

        elif ptype == MsgType.ONLINE_LIST:
            me = self.username  # snapshot — may be "" if auth not yet complete
            for u in pkt.get("users", []):
                if not u or u == me:
                    continue
                await add_contact(u)
                await set_status(u, "online")
                await self._request_public_key(u)
            # Remove self from contacts regardless (guards against stale DB rows)
            if me:
                from client.db.schema import DB_PATH
                import aiosqlite
                async with aiosqlite.connect(DB_PATH) as _db:
                    await _db.execute("DELETE FROM contacts WHERE username=?", (me,))
                    await _db.commit()
            await self._refresh_contacts()

        elif ptype == MsgType.KEY_RESPONSE:
            if pkt.get("success") and pkt.get("username") and pkt.get("public_key"):
                await store_public_key(pkt["username"], pkt["public_key"])
                await self._refresh_contacts()
            elif pkt.get("username"):
                self._set_notice(f"No public key for {pkt['username']}")

        elif ptype == MsgType.DELIVERY:
            if self.msg_h:
                await self.msg_h.handle_delivery(pkt)
            await self._load_messages()

        elif ptype == MsgType.READ:
            if self.msg_h:
                await self.msg_h.handle_read(pkt)
            await self._load_messages()

        elif ptype == MsgType.ROOM_INFO:
            if self.room_h:
                self.room_h.handle_room_info(pkt)
            room = pkt.get("room") or {}
            if pkt.get("success") and room.get("id"):
                if pkt.get("left"):
                    await delete_room(room["id"])
                    if self.current_chat == self._room_key(room["id"]):
                        self.current_chat = None
                        self.messages = []
                else:
                    await save_room(room["id"], room.get("name", room["id"]))
                await self._refresh_contacts()

        elif ptype == MsgType.ROOM_MEMBERS_LIST:
            if self.room_h:
                self.room_h.handle_members_list(pkt)

        elif ptype == MsgType.ROOM_MESSAGE:
            if self.room_h:
                await self.room_h.handle_incoming(pkt)

        elif ptype in (
            MsgType.FILE_INIT,
            MsgType.FILE_ACK,
            MsgType.FILE_DONE,
            MsgType.FILE_CANCEL,
        ):
            if self.file_h:
                await self.file_h.handle_incoming(pkt)
            if ptype == MsgType.FILE_DONE:
                file_id = pkt.get("file_id", "")
                self._set_notice(f"File ready: {file_id[:8]}  /file accept {file_id}")

        elif ptype in (
            MsgType.CALL_INVITE,
            MsgType.CALL_ACCEPT,
            MsgType.CALL_DECLINE,
            MsgType.CALL_END,
            MsgType.CALL_RELAY,
        ):
            if self.call_h:
                await self.call_h.handle_incoming(pkt)

        elif ptype == MsgType.TYPING:
            sender = pkt.get("from", "")
            room_key = self._room_key(pkt.get("room_id", ""))
            if sender == self.current_chat or room_key == self.current_chat:
                self._set_notice(f"{sender} is typing…")

        elif ptype == MsgType.PONG:
            pass

        elif ptype == MsgType.ERROR:
            self._set_notice(f"Server error: {pkt.get('error', '?')}")

        self._dirty = True

    # ── UI loop ────────────────────────────────────────────────────────────

    async def _ui_loop(self, stdscr, creds: dict) -> None:
        # Wait for WS to connect, then authenticate
        while not self.ws.connected:
            await asyncio.sleep(0.05)

        await self._do_auth(creds)

        # Fetch initial data
        await self._refresh_contacts()
        if self.current_chat:
            await self._load_messages()

        while self._running:
            # Handle terminal resize
            key = stdscr.getch()
            if key == curses.KEY_RESIZE:
                self.screen.resize()
                self._dirty = True
                await asyncio.sleep(_TICK)
                continue

            if key != -1:
                await self._handle_key(key)

            if self._dirty:
                self._render()
                self._dirty = False

            await asyncio.sleep(_TICK)

    # ── Auth ───────────────────────────────────────────────────────────────

    async def _do_auth(self, creds: dict) -> None:
        try:
            if "token" in creds:
                result = await self.auth_h.reauth(creds["token"])
                if not result.get("success"):
                    # token expired — show auth screen
                    new_creds = await show_auth(self.screen.stdscr)
                    if not new_creds:
                        self._running = False
                        return
                    await self._do_auth(new_creds)
                    return
                self.username = result["username"]
                encrypted_private_key, public_key_b64 = await get_my_keys()
                if encrypted_private_key and self.crypto_ctx is None:
                    password = await show_unlock(self.screen.stdscr, self.username)
                    if password is None:
                        # User pressed N/Q/ESC — they want a different account
                        new_creds = await show_auth(self.screen.stdscr)
                        if not new_creds:
                            self._running = False
                            return
                        await self._do_auth(new_creds)
                        return
                    elif password == "":
                        # User pressed S — skip encryption, continue plaintext
                        self._set_notice("E2E encryption skipped. Messages will be plaintext.")
                    else:
                        try:
                            private_key = decrypt_private_key(encrypted_private_key, password)
                            self.crypto_ctx = SimpleNamespace(private_key=private_key)
                        except ValueError:
                            self._set_notice("Wrong password — key locked. Press S to skip or retry.")
            elif creds["mode"] == "register":
                result = await self.auth_h.register(creds["username"], creds["password"])
                if not result.get("success"):
                    self._set_notice(f"Register failed: {result.get('error')}")
                    return
                self.username = result["username"]
                self.crypto_ctx = result.get("crypto_ctx")
            else:
                result = await self.auth_h.login(creds["username"], creds["password"])
                if not result.get("success"):
                    self._set_notice(f"Login failed: {result.get('error')}")
                    return
                self.username = result["username"]
                self.crypto_ctx = result.get("crypto_ctx")

            self.conn_status = "online"
            self.msg_h = MessageHandler(
                self.ws,
                self.username,
                crypto_ctx=self.crypto_ctx,
                on_new_message=self._on_new_message,
            )
            self.room_h = RoomHandler(
                self.ws,
                self.username,
                crypto_ctx=self.crypto_ctx,
                on_room_message=self._on_room_message,
            )
            self.file_h = FileHandler(
                self.ws,
                self.username,
                crypto_ctx=self.crypto_ctx,
                server_tcp_host=SERVER_TCP_HOST,
                server_tcp_port=SERVER_TCP_PORT,
                on_receive=self._on_file_offer,
                on_progress=self._on_file_progress,
            )
            self.call_h = CallHandler(
                self.ws,
                self.username,
                crypto_ctx=self.crypto_ctx,
                on_incoming_call=self._on_incoming_call,
                on_call_ended=self._on_call_ended,
            )
            # Ask server for online users
            await self.ws.send_pkt(MsgType.STATUS)
            self._set_notice(f"Logged in as {self.username}")
            self._dirty = True

        except asyncio.TimeoutError:
            self._set_notice("Auth timed out. Is the server running?")

    # ── Key handling ───────────────────────────────────────────────────────

    async def _handle_key(self, key: int) -> None:
        if key == 3:               # Ctrl-C → quit
            self._running = False
            return

        if key in (curses.KEY_ENTER, 10, 13):
            await self._submit()
            return

        if key in (curses.KEY_BACKSPACE, 127, 8):
            if self.cursor > 0:
                self.buf    = self.buf[: self.cursor - 1] + self.buf[self.cursor:]
                self.cursor -= 1
            self._dirty = True
            return

        if key == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
            self._dirty = True
            return

        if key == curses.KEY_RIGHT:
            self.cursor = min(len(self.buf), self.cursor + 1)
            self._dirty = True
            return

        if key == curses.KEY_HOME:
            self.cursor = 0
            self._dirty = True
            return

        if key == curses.KEY_END:
            self.cursor = len(self.buf)
            self._dirty = True
            return

        if 32 <= key <= 126:
            ch = chr(key)
            self.buf    = self.buf[: self.cursor] + ch + self.buf[self.cursor:]
            self.cursor += 1
            self._dirty  = True
            # Typing indicator
            if self.current_chat and self.msg_h:
                if self._is_room_chat(self.current_chat):
                    await self.ws.send_pkt(
                        MsgType.TYPING,
                        room_id=self._room_id_from_key(self.current_chat),
                        **{"from": self.username},
                    )
                else:
                    await self.ws.send_pkt(MsgType.TYPING, to=self.current_chat,
                                           **{"from": self.username})
            return

    # ── Submit / command parser ────────────────────────────────────────────

    async def _submit(self) -> None:
        text = self.buf.strip()
        self.buf    = ""
        self.cursor = 0
        self._dirty = True

        if not text:
            return

        # Commands
        if text.startswith("/"):
            await self._run_command(text)
            return

        # Plain message
        if not self.current_chat:
            self._set_notice("No chat open. Use /dm @username to start a chat.")
            return
        if not self.msg_h:
            self._set_notice("Not connected yet.")
            return

        if self._is_room_chat(self.current_chat):
            if not self.room_h:
                self._set_notice("Rooms are not connected yet.")
                return
            room_id = self._room_id_from_key(self.current_chat)
            try:
                msg_id = await self.room_h.send_message(room_id, text)
            except Exception as exc:
                self._set_notice(f"Room message not sent: {exc}")
                self.buf = text
                self.cursor = len(text)
                return
            await save_message(msg_id, self.username, room_id, text, now(), delivered=0)
            await update_last_message(room_id, text)
        else:
            try:
                await self.msg_h.send(self.current_chat, text)
            except Exception as exc:
                self._set_notice(f"Message not sent: {exc}")
                self.buf = text
                self.cursor = len(text)
                return
        await self._load_messages()

    async def _run_command(self, text: str) -> None:
        parts = text.split()
        cmd   = parts[0].lower()

        if cmd == "/dm":
            if len(parts) < 2:
                self._set_notice("Usage: /dm @username")
                return
            target = parts[1].lstrip("@")
            if target == self.username:
                self._set_notice("You cannot open a DM with yourself.")
                return
            await add_contact(target)
            await self._request_public_key(target)
            self.current_chat = target
            await self._load_messages()
            await mark_read(target, self.username)
            self.unread[target] = 0
            await self._refresh_contacts()
            self._set_notice(f"Opened chat with {target}")

        elif cmd == "/room":
            await self._room_command(parts)

        elif cmd == "/file":
            await self._file_command(parts, text)

        elif cmd == "/call":
            await self._call_command(parts)

        elif cmd == "/verify":
            await self._verify_command(parts)

        elif cmd == "/edit":
            if len(parts) < 3:
                self._set_notice("Usage: /edit <message_id> <new text>")
                return
            if not self.current_chat:
                self._set_notice("Open a chat first.")
                return
            msg_id = parts[1]
            new_text = text.split(None, 2)[2]
            if self._is_room_chat(self.current_chat):
                room_id = self._room_id_from_key(self.current_chat)
                await self.ws.send_pkt(
                    MsgType.MESSAGE_EDIT,
                    msg_id=msg_id,
                    room_id=room_id,
                    payload={"text": new_text},
                    **{"from": self.username},
                )
                await edit_message(msg_id, new_text)
            elif self.msg_h:
                try:
                    await self.msg_h.edit(msg_id, self.current_chat, new_text)
                except Exception as exc:
                    self._set_notice(f"Edit failed: {exc}")
                    return
            await self._load_messages()

        elif cmd == "/delete":
            if len(parts) < 2:
                self._set_notice("Usage: /delete <message_id>")
                return
            if not self.current_chat:
                self._set_notice("Open a chat first.")
                return
            msg_id = parts[1]
            if self._is_room_chat(self.current_chat):
                room_id = self._room_id_from_key(self.current_chat)
                await self.ws.send_pkt(
                    MsgType.MESSAGE_DELETE,
                    msg_id=msg_id,
                    room_id=room_id,
                    **{"from": self.username},
                )
                await delete_message(msg_id)
            elif self.msg_h:
                await self.msg_h.delete(msg_id, self.current_chat)
            await self._load_messages()

        elif cmd in ("/quit", "/exit", "/q"):
            self._running = False

        elif cmd == "/logout":
            account = await get_account()
            if self.ws and account and account.get("session_token"):
                try:
                    await self.ws.send_pkt(MsgType.AUTH_LOGOUT, token=account["session_token"])
                except Exception:
                    pass
            await clear_session_token()
            self._set_notice("Logged out. Restart the client to choose another account.")
            self._running = False

        elif cmd == "/online":
            await self.ws.send_pkt(MsgType.STATUS)
            self._set_notice("Fetching online users…")

        elif cmd == "/clear":
            self.messages = []
            self._dirty   = True

        elif cmd == "/help":
            self._set_notice(
                "/dm  /room  /file  /call  /verify @user  /edit  /delete  /logout"
            )

        else:
            self._set_notice(f"Unknown command: {cmd}   /help for list")

    # ── Message callback ───────────────────────────────────────────────────

    async def _on_new_message(self, pkt: dict) -> None:
        sender = pkt.get("from", "")
        await add_contact(sender)

        if sender == self.current_chat:
            await self._load_messages()
            await mark_read(sender, self.username)
            if self.ws:
                await self.ws.send_pkt(MsgType.READ, to=sender, msg_id=pkt.get("id"))
            self.unread[sender] = 0
        else:
            cnt = await unread_count(sender, self.username)
            self.unread[sender] = cnt

        await self._refresh_contacts()
        self._dirty = True

    async def _on_room_message(self, pkt: dict) -> None:
        sender = pkt.get("from", "")
        room_id = pkt.get("room_id", "")
        payload = pkt.get("payload", {})
        payloads = pkt.get("payloads", {})
        if payloads and self.username in payloads and self.crypto_ctx is not None:
            try:
                text = decrypt_message(payloads[self.username], self.crypto_ctx.private_key)
            except Exception:
                text = "[encrypted - decryption failed]"
        elif payloads:
            text = "[encrypted - locked]"
        else:
            text = payload.get("text", "")
        msg_id = pkt.get("id") or new_id()
        ts = pkt.get("timestamp") or now()

        if room_id:
            await save_message(msg_id, sender, room_id, text, ts, delivered=1)
            await update_last_message(room_id, text)

        room_key = self._room_key(room_id)
        if self.current_chat == room_key:
            await self._load_messages()
        else:
            self.unread[room_key] = self.unread.get(room_key, 0) + 1

        await self._refresh_contacts()
        self._dirty = True

    async def _handle_synced_packet(self, pkt: dict) -> None:
        ptype = pkt.get("type")
        if ptype == MsgType.MESSAGE and self.msg_h:
            await self.msg_h.handle_incoming(pkt)
        elif ptype == MsgType.ROOM_MESSAGE and self.room_h:
            await self.room_h.handle_incoming(pkt)
        elif ptype == MsgType.MESSAGE_EDIT and self.msg_h:
            await self.msg_h.handle_edit(pkt)
        elif ptype == MsgType.MESSAGE_DELETE and self.msg_h:
            await self.msg_h.handle_delete(pkt)
        elif ptype in (
            MsgType.FILE_INIT,
            MsgType.FILE_ACK,
            MsgType.FILE_DONE,
            MsgType.FILE_CANCEL,
        ) and self.file_h:
            await self.file_h.handle_incoming(pkt)
        elif ptype in (
            MsgType.CALL_INVITE,
            MsgType.CALL_ACCEPT,
            MsgType.CALL_DECLINE,
            MsgType.CALL_END,
            MsgType.CALL_RELAY,
        ) and self.call_h:
            await self.call_h.handle_incoming(pkt)

    # ── Data helpers ───────────────────────────────────────────────────────

    async def _load_messages(self) -> None:
        if self.current_chat:
            if self._is_room_chat(self.current_chat):
                self.messages = await get_room_history(
                    self._room_id_from_key(self.current_chat)
                )
            else:
                self.messages = await get_history(self.current_chat, self.username)
        self._dirty = True

    async def _refresh_contacts(self) -> None:
        self.rooms = await all_rooms()
        room_items = [
            {
                "username": self._room_key(room["id"]),
                "label": f"#{room['name']}",
                "type": "room",
                "status": "online",
            }
            for room in self.rooms
        ]
        me = self.username
        roster = [
            c for c in await all_contacts()
            if c.get("username") and c.get("username") != me
        ]
        self.contacts = room_items + roster
        for c in self.contacts:
            u = c["username"]
            if u not in self.unread:
                if c.get("type") == "room":
                    self.unread[u] = 0
                else:
                    self.unread[u] = await unread_count(u, self.username)
        self._dirty = True

    async def _request_public_key(self, username: str) -> None:
        if username and username != self.username and self.ws:
            await self.ws.send_pkt(MsgType.KEY_REQUEST, username=username)

    async def _room_command(self, parts: list[str]) -> None:
        if not self.room_h or len(parts) < 2:
            self._set_notice("Usage: /room create|join|open|leave|members")
            return

        action = parts[1].lower()
        if action == "create":
            if len(parts) < 3:
                self._set_notice("Usage: /room create <name>")
                return
            room_name = " ".join(parts[2:])
            result = await self.room_h.create(room_name)
            room = result.get("room", {})
            if result.get("success") and room.get("id"):
                await save_room(room["id"], room.get("name", room_name))
                self.current_chat = self._room_key(room["id"])
                await self._load_messages()
                self._set_notice(
                    f"Created #{room.get('name', room_name)} id={room['id']}"
                )
            else:
                self._set_notice(f"Room create failed: {result.get('error', '?')}")

        elif action == "join":
            if len(parts) < 3:
                self._set_notice("Usage: /room join <room_id>")
                return
            room_id = parts[2]
            success = await self.room_h.join(room_id)
            if success:
                self.current_chat = self._room_key(room_id)
                await self._load_messages()
                self._set_notice(f"Joined room {room_id}")
            else:
                self._set_notice(f"Room not found: {room_id}")

        elif action == "open":
            if len(parts) < 3:
                self._set_notice("Usage: /room open <room_id>")
                return
            self.current_chat = self._room_key(parts[2])
            await self._load_messages()
            self.unread[self.current_chat] = 0
            self._set_notice(f"Opened room {parts[2]}")

        elif action == "leave":
            room_id = (
                self._room_id_from_key(self.current_chat)
                if self.current_chat and self._is_room_chat(self.current_chat)
                else parts[2] if len(parts) >= 3 else ""
            )
            if not room_id:
                self._set_notice("Usage: /room leave <room_id>")
                return
            await self.room_h.leave(room_id)
            await delete_room(room_id)
            if self.current_chat == self._room_key(room_id):
                self.current_chat = None
                self.messages = []
            await self._refresh_contacts()
            self._set_notice(f"Left room {room_id}")

        elif action == "members":
            room_id = (
                self._room_id_from_key(self.current_chat)
                if self.current_chat and self._is_room_chat(self.current_chat)
                else parts[2] if len(parts) >= 3 else ""
            )
            if not room_id:
                self._set_notice("Usage: /room members <room_id>")
                return
            members = await self.room_h.get_members(room_id)
            self._set_notice("Members: " + ", ".join(members))

        else:
            self._set_notice("Usage: /room create|join|open|leave|members")

        await self._refresh_contacts()

    async def _file_command(self, parts: list[str], text: str) -> None:
        if not self.file_h or len(parts) < 2:
            self._set_notice("Usage: /file send|accept|resume|cancel|list")
            return

        action = parts[1].lower()
        if action == "send":
            if len(parts) < 4:
                self._set_notice("Usage: /file send @user <path>")
                return
            to_user = parts[2].lstrip("@")
            path = text.split(None, 3)[3]
            try:
                file_id = await self.file_h.send(to_user, path)
                self._set_notice(f"File sent: {file_id[:8]}")
            except Exception as exc:
                self._set_notice(f"File send failed: {exc}")

        elif action in ("accept", "download"):
            if len(parts) < 3:
                self._set_notice("Usage: /file accept <file_id>")
                return
            file_id = parts[2]
            meta = self.file_offers.get(file_id)
            if meta is None:
                record = await get_file(file_id)
                if record:
                    meta = {
                        "file_id": file_id,
                        "name": record["name"],
                        "size": record["size"],
                        "total_chunks": record["total_chunks"],
                        "from_user": record["from_user"],
                        "file_key": record.get("file_key", ""),
                    }
            if not meta:
                self._set_notice(f"Unknown file: {file_id[:8]}")
                return
            try:
                dest = await self.file_h.receive(file_id, meta)
                self._set_notice(f"Saved file: {dest}")
            except Exception as exc:
                self._set_notice(f"Download failed: {exc}")

        elif action == "resume":
            if len(parts) < 3:
                self._set_notice("Usage: /file resume <file_id>")
                return
            try:
                await self.file_h.resume_send(parts[2])
                self._set_notice(f"Resumed file: {parts[2][:8]}")
            except Exception as exc:
                self._set_notice(f"Resume failed: {exc}")

        elif action == "cancel":
            if len(parts) < 3:
                self._set_notice("Usage: /file cancel <file_id>")
                return
            await self.file_h.cancel(parts[2])
            self._set_notice(f"Cancelled file: {parts[2][:8]}")

        elif action == "list":
            files = await get_recent_files()
            if not files:
                self._set_notice("No file transfers.")
                return
            labels = []
            for item in files[:3]:
                state = "done" if item.get("complete") == 1 else "pending"
                labels.append(f"{item['id'][:8]} {item['direction']} {state} {item['name']}")
            self._set_notice(" | ".join(labels))

        else:
            self._set_notice("Usage: /file send|accept|resume|cancel|list")

    async def _call_command(self, parts: list[str]) -> None:
        if not self.call_h or len(parts) < 2:
            self._set_notice("Usage: /call start|accept|decline|end|mute|unmute|ptt|talk|history")
            return

        action = parts[1].lower()
        if action in ("start", "invite"):
            if len(parts) < 3:
                self._set_notice("Usage: /call start @user")
                return
            target = parts[2].lstrip("@")
            try:
                session_id = await self.call_h.invite(target)
                self._set_notice(f"Calling {target}: {session_id[:8]}")
            except Exception as exc:
                self._set_notice(f"Call failed: {exc}")

        elif action == "accept":
            session_id = parts[2] if len(parts) >= 3 else next(iter(self.pending_calls), "")
            call = self.pending_calls.get(session_id)
            if not session_id or not call:
                self._set_notice("No pending call.")
                return
            await self.call_h.accept(session_id, call.get("caller_pubkey"))
            self.pending_calls.pop(session_id, None)
            self._set_notice(f"Call accepted: {session_id[:8]}")

        elif action == "decline":
            session_id = parts[2] if len(parts) >= 3 else next(iter(self.pending_calls), "")
            if not session_id:
                self._set_notice("No pending call.")
                return
            await self.call_h.decline(session_id)
            self.pending_calls.pop(session_id, None)
            self._set_notice(f"Call declined: {session_id[:8]}")

        elif action == "end":
            session_id = parts[2] if len(parts) >= 3 else self.call_h.active_session
            if not session_id:
                self._set_notice("No active call.")
                return
            await self.call_h.end(session_id)
            self._set_notice(f"Call ended: {session_id[:8]}")

        elif action == "mute":
            self.call_h.mute(True)
            self._set_notice("Call muted")

        elif action == "unmute":
            self.call_h.mute(False)
            self._set_notice("Call unmuted")

        elif action == "ptt":
            enabled = not (len(parts) >= 3 and parts[2].lower() in ("off", "0", "false"))
            self.call_h.push_to_talk(enabled)
            self._set_notice("Push-to-talk on" if enabled else "Push-to-talk off")

        elif action == "talk":
            talking = not (len(parts) >= 3 and parts[2].lower() in ("off", "0", "false"))
            self.call_h.set_talking(talking)
            self._set_notice("Talking" if talking else "Push-to-talk muted")

        elif action == "history":
            rows = await get_call_history(self.username)
            if not rows:
                self._set_notice("No call history.")
                return
            labels = []
            for row in rows[:3]:
                peer = row["to_user"] if row["from_user"] == self.username else row["from_user"]
                labels.append(f"{row['id'][:8]} {peer} {row.get('status')}")
            self._set_notice(" | ".join(labels))

        else:
            self._set_notice("Usage: /call start|accept|decline|end|mute|unmute|ptt|talk|history")

    async def _verify_command(self, parts: list[str]) -> None:
        if len(parts) < 2:
            self._set_notice("Usage: /verify @username")
            return
        username = parts[1].lstrip("@")
        encrypted_private_key, my_pub_b64 = await get_my_keys()
        their_pub_b64 = await get_public_key(username)
        if not my_pub_b64:
            self._set_notice("No local public key found. Register/login with keys first.")
            return
        if not their_pub_b64:
            await self._request_public_key(username)
            self._set_notice(f"Fetching public key for {username}. Try /verify again.")
            return

        try:
            number = safety_number(
                base64.b64decode(my_pub_b64),
                base64.b64decode(their_pub_b64),
            )
        except Exception as exc:
            self._set_notice(f"Could not compute safety number: {exc}")
            return

        if not self.screen:
            self._set_notice(number)
            return

        confirmed = await verify_screen.show(self.screen.stdscr, username, number)
        if confirmed:
            await set_verified(username, True)
            await self._refresh_contacts()
            self._set_notice(f"{username} verified")
        else:
            self._set_notice("Verification cancelled")

    async def _on_file_offer(self, file_meta: dict) -> None:
        file_id = file_meta["file_id"]
        self.file_offers[file_id] = file_meta
        self._set_notice(
            f"File offer {file_id[:8]} from {file_meta['from_user']}: {file_meta['name']}"
        )

    async def _on_file_progress(self, file_id: str, done: int, total: int) -> None:
        self.file_progress[file_id] = (done, total)
        pct = int((done / total) * 100) if total else 100
        bar_width = 10
        filled = min(bar_width, int((pct / 100) * bar_width))
        bar = "#" * filled + "-" * (bar_width - filled)
        self._set_notice(f"File {file_id[:8]} [{bar}] {pct}%")

    async def _on_incoming_call(
        self,
        session_id: str,
        caller: str,
        caller_pubkey_b64: str | None,
    ) -> None:
        self.pending_calls[session_id] = {
            "caller": caller,
            "caller_pubkey": caller_pubkey_b64,
        }
        self._set_notice(f"Incoming call from {caller}: /call accept {session_id}")

    async def _on_call_ended(self, session_id: str, reason: str) -> None:
        self.pending_calls.pop(session_id, None)
        self._set_notice(f"Call {session_id[:8]} {reason}")

    def _room_key(self, room_id: str) -> str:
        return f"room:{room_id}"

    def _is_room_chat(self, chat_id: str | None) -> bool:
        return bool(chat_id and chat_id.startswith("room:"))

    def _room_id_from_key(self, chat_id: str) -> str:
        return chat_id.removeprefix("room:")

    def _set_notice(self, msg: str) -> None:
        self.notice    = msg
        self.notice_ts = now()
        self._dirty    = True

    # ── Render ─────────────────────────────────────────────────────────────

    def _render(self) -> None:
        if not self.screen:
            return

        # Clear expired notice after 4 seconds
        if self.notice and now() - self.notice_ts > 4:
            self.notice = ""

        total_unread = sum(self.unread.values())

        # Status bar
        display_status = self.conn_status
        if display_status == "reconnecting":
            display_status = f"reconnecting (#{self.reconnect_att})"
        status_bar.render(
            self.screen.header_win,
            self.username or "…",
            display_status,
            self.current_chat,
            total_unread,
        )

        # Sidebar
        sidebar.render(
            self.screen.sidebar_win,
            self.contacts,
            self.current_chat,
            self.unread,
        )

        # Divider
        divider.render(self.screen.div_win)

        # Chat
        chat_view.render(
            self.screen.chat_win,
            self.messages,
            self.username,
            self.current_chat,
        )

        # Input
        hint = self.notice if self.notice else ""
        input_bar.render(
            self.screen.input_win,
            self.buf,
            self.cursor,
            hint,
        )

        self.screen.noutrefresh_all()
        self.screen.doupdate()
