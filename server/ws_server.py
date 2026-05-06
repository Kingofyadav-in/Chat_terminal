import asyncio
import json
import logging

import websockets
from websockets.server import WebSocketServerProtocol

from shared.protocol import MsgType, make_packet, parse_packet
from shared.utils import now
from server import auth, router, offline, rooms, files
from server.call_manager import CallManager
from server.rate_limiter import RateLimiter

_call_manager = CallManager()
_rate_limiter = RateLimiter(rate=120, per=60.0)

log = logging.getLogger("ct.server")


async def _broadcast_status(username: str, status: str) -> None:
    pkt = make_packet(MsgType.STATUS, user=username, status=status)
    for uname, ws in list(router._connections.items()):
        if uname != username:
            try:
                await ws.send(pkt)
            except Exception as exc:
                log.debug("Status broadcast to %s failed: %s", uname, exc)
                router.unregister(uname)


async def _flush_offline(ws, username: str) -> None:
    queued = await offline.flush(username)
    if queued:
        await ws.send(make_packet(MsgType.SYNC_RESPONSE, messages=queued))
    await offline.clear(username)


async def _on_auth_success(ws, result: dict) -> str:
    username = result["username"]
    router.register(username, ws)
    await ws.send(make_packet(MsgType.AUTH_RESPONSE, **result))
    await _flush_offline(ws, username)
    await _broadcast_status(username, "online")
    log.info(f"[+] {username} connected")
    return username


async def handle(ws: WebSocketServerProtocol) -> None:
    username: str | None = None
    peer = ws.remote_address[0] if ws.remote_address else "unknown"
    try:
        async for raw in ws:
            rate_key = username or peer
            if not _rate_limiter.allow(rate_key):
                await ws.send(make_packet(MsgType.ERROR, error="RATE_LIMITED"))
                continue

            try:
                pkt = parse_packet(raw)
            except Exception:
                await ws.send(make_packet(MsgType.ERROR, error="INVALID_PACKET"))
                continue

            ptype = pkt.get("type")

            # ── Auth ──────────────────────────────────────────────────────
            if ptype == MsgType.AUTH_REGISTER:
                uname = pkt.get("username", "").strip()
                pw = pkt.get("password", "")
                if not uname or not pw:
                    await ws.send(make_packet(MsgType.AUTH_RESPONSE,
                                              success=False, error="AUTH_FAILED"))
                    continue
                result = await auth.register(uname, pw, pkt.get("public_key"))
                if result["success"]:
                    username = await _on_auth_success(ws, result)
                else:
                    await ws.send(make_packet(MsgType.AUTH_RESPONSE, **result))

            elif ptype == MsgType.AUTH_LOGIN:
                if "token" in pkt:
                    session = await auth.validate_token(pkt["token"])
                    if session:
                        result = {"success": True,
                                  "token": pkt["token"],
                                  "username": session["username"]}
                    else:
                        result = {"success": False, "error": "INVALID_TOKEN"}
                else:
                    result = await auth.login(
                        pkt.get("username", ""), pkt.get("password", "")
                    )
                if result["success"]:
                    username = await _on_auth_success(ws, result)
                else:
                    await ws.send(make_packet(MsgType.AUTH_RESPONSE, **result))

            elif ptype == MsgType.AUTH_LOGOUT:
                if "token" in pkt:
                    from server.db.users import delete_session
                    await delete_session(pkt["token"])
                break

            # ── Require auth for everything below ─────────────────────────
            elif username is None:
                await ws.send(make_packet(MsgType.ERROR, error="UNAUTHORIZED"))

            # ── Public key lookup ─────────────────────────────────────────
            elif ptype == MsgType.KEY_REQUEST:
                target = pkt.get("username", "").strip()
                if not target:
                    await ws.send(make_packet(MsgType.KEY_RESPONSE,
                                              success=False, error="MISSING_USERNAME"))
                    continue

                from server.db.users import get_public_key

                public_key = await get_public_key(target)
                if public_key:
                    await ws.send(make_packet(MsgType.KEY_RESPONSE,
                                              success=True,
                                              username=target,
                                              public_key=public_key))
                else:
                    await ws.send(make_packet(MsgType.KEY_RESPONSE,
                                              success=False,
                                              username=target,
                                              error="KEY_NOT_FOUND"))

            # ── Message ───────────────────────────────────────────────────
            elif ptype == MsgType.MESSAGE:
                to_user = pkt.get("to")
                if not to_user:
                    continue
                pkt["from"] = username          # server stamps the sender
                raw_stamped = json.dumps(pkt)

                target_ws = router.get(to_user)
                if target_ws:
                    try:
                        await target_ws.send(raw_stamped)
                        status = "delivered"
                    except Exception:
                        await offline.enqueue(pkt["id"], to_user, raw_stamped)
                        status = "queued"
                else:
                    await offline.enqueue(pkt["id"], to_user, raw_stamped)
                    status = "queued"

                await ws.send(make_packet(MsgType.DELIVERY,
                                          msg_id=pkt["id"], status=status))

            elif ptype == MsgType.MESSAGE_EDIT:
                target = pkt.get("to")
                room_id = pkt.get("room_id")
                pkt["from"] = username
                raw_stamped = json.dumps(pkt)

                if room_id:
                    await rooms.route_room_message(room_id, pkt, username, router)
                elif target:
                    target_ws = router.get(target)
                    if target_ws:
                        await target_ws.send(raw_stamped)
                    else:
                        await offline.enqueue(f"{pkt.get('msg_id')}:{target}:edit",
                                              target, raw_stamped)

                await ws.send(make_packet(MsgType.DELIVERY,
                                          msg_id=pkt.get("msg_id"),
                                          status="edited"))

            elif ptype == MsgType.MESSAGE_DELETE:
                target = pkt.get("to")
                room_id = pkt.get("room_id")
                pkt["from"] = username
                raw_stamped = json.dumps(pkt)

                if room_id:
                    await rooms.route_room_message(room_id, pkt, username, router)
                elif target:
                    target_ws = router.get(target)
                    if target_ws:
                        await target_ws.send(raw_stamped)
                    else:
                        await offline.enqueue(f"{pkt.get('msg_id')}:{target}:delete",
                                              target, raw_stamped)

                await ws.send(make_packet(MsgType.DELIVERY,
                                          msg_id=pkt.get("msg_id"),
                                          status="deleted"))

            elif ptype == MsgType.READ:
                to_user = pkt.get("to")
                if to_user:
                    pkt["from"] = username
                    target_ws = router.get(to_user)
                    if target_ws:
                        try:
                            await target_ws.send(json.dumps(pkt))
                        except Exception:
                            pass

            # ── File transfer metadata ───────────────────────────────────
            elif ptype == MsgType.FILE_INIT:
                to_user = pkt.get("to", "").strip()
                file_id = pkt.get("file_id", pkt.get("id"))
                if not to_user or not file_id:
                    await ws.send(make_packet(MsgType.ERROR, error="INVALID_FILE_INIT"))
                    continue

                pkt["from"] = username
                pkt["file_id"] = file_id
                await files.register_file({
                    "id": file_id,
                    "name": pkt.get("name", "unknown"),
                    "size": int(pkt.get("size", 0)),
                    "total_chunks": int(pkt.get("total_chunks", 1)),
                    "uploader": username,
                    "recipient": to_user,
                    "encrypted_key": json.dumps(pkt.get("encrypted_key")),
                    "created_at": pkt.get("timestamp", now()),
                })

                raw_stamped = json.dumps(pkt)
                target_ws = router.get(to_user)
                if target_ws:
                    try:
                        await target_ws.send(raw_stamped)
                    except Exception:
                        await offline.enqueue(f"{file_id}:{to_user}:init",
                                              to_user, raw_stamped)
                else:
                    await offline.enqueue(f"{file_id}:{to_user}:init",
                                          to_user, raw_stamped)

                await ws.send(make_packet(MsgType.FILE_ACK, file_id=file_id))

            elif ptype in (MsgType.FILE_ACK, MsgType.FILE_DONE, MsgType.FILE_CANCEL):
                target = pkt.get("to")
                file_id = pkt.get("file_id", pkt.get("id"))
                pkt["from"] = username
                raw_stamped = json.dumps(pkt)
                if target:
                    target_ws = router.get(target)
                    if target_ws:
                        try:
                            await target_ws.send(raw_stamped)
                        except Exception:
                            await offline.enqueue(f"{file_id}:{target}:{ptype}",
                                                  target, raw_stamped)
                    else:
                        await offline.enqueue(f"{file_id}:{target}:{ptype}",
                                              target, raw_stamped)

            # ── Audio call signaling / relay ─────────────────────────────
            elif ptype == MsgType.CALL_INVITE:
                callee = pkt.get("to", "").strip()
                session_id = pkt.get("session_id", pkt.get("id"))
                if not callee or not session_id:
                    await ws.send(make_packet(MsgType.ERROR, error="INVALID_CALL_INVITE"))
                    continue
                ok = await _call_manager.invite(
                    session_id=session_id,
                    caller=username,
                    callee=callee,
                    caller_info={
                        "caller_ip": ws.remote_address[0] if ws.remote_address else "",
                        "caller_udp_port": pkt.get("local_port"),
                        "caller_pubkey": pkt.get("ephemeral_pubkey"),
                    },
                    router_module=router,
                    ws_client=ws,
                )
                if not ok:
                    await ws.send(make_packet(MsgType.CALL_DECLINE,
                                              session_id=session_id,
                                              reason="unavailable"))

            elif ptype == MsgType.CALL_ACCEPT:
                session_id = pkt.get("session_id", "")
                await _call_manager.accept(
                    session_id=session_id,
                    callee_info={
                        "callee_ip": ws.remote_address[0] if ws.remote_address else "",
                        "callee_udp_port": pkt.get("local_port"),
                        "callee_pubkey": pkt.get("ephemeral_pubkey"),
                    },
                    router_module=router,
                )

            elif ptype == MsgType.CALL_DECLINE:
                await _call_manager.decline(pkt.get("session_id", ""), router)

            elif ptype == MsgType.CALL_END:
                await _call_manager.end_call(pkt.get("session_id", ""), username, router)

            elif ptype == MsgType.CALL_RELAY:
                await _call_manager.relay_audio(
                    pkt.get("session_id", ""),
                    username,
                    pkt.get("data", ""),
                    router,
                )

            # ── Rooms / group chat ────────────────────────────────────────
            elif ptype == MsgType.ROOM_CREATE:
                name = pkt.get("name", "").strip()
                req_id = pkt.get("req_id")
                if not name:
                    await ws.send(make_packet(MsgType.ROOM_INFO,
                                              req_id=req_id,
                                              success=False,
                                              error="MISSING_NAME"))
                    continue
                room = await rooms.create_room(name, username)
                await ws.send(make_packet(MsgType.ROOM_INFO,
                                          req_id=req_id,
                                          success=True,
                                          room=room))

            elif ptype == MsgType.ROOM_JOIN:
                room_id = pkt.get("room_id", "").strip()
                req_id = pkt.get("req_id")
                success = await rooms.join_room(room_id, username)
                response = {"id": room_id}
                if success:
                    user_rooms = await rooms.get_user_rooms(username)
                    response = next(
                        (room for room in user_rooms if room["id"] == room_id),
                        response,
                    )
                await ws.send(make_packet(MsgType.ROOM_INFO,
                                          req_id=req_id,
                                          success=success,
                                          room=response))

            elif ptype == MsgType.ROOM_LEAVE:
                room_id = pkt.get("room_id", "").strip()
                if room_id:
                    await rooms.leave_room(room_id, username)
                    await ws.send(make_packet(MsgType.ROOM_INFO,
                                              success=True,
                                              room={"id": room_id},
                                              left=True))

            elif ptype == MsgType.ROOM_MEMBERS:
                room_id = pkt.get("room_id", "").strip()
                req_id = pkt.get("req_id")
                members = await rooms.get_members(room_id) if room_id else []
                await ws.send(make_packet(MsgType.ROOM_MEMBERS_LIST,
                                          req_id=req_id,
                                          room_id=room_id,
                                          members=members))

            elif ptype == MsgType.ROOM_MESSAGE:
                room_id = pkt.get("room_id", "").strip()
                if not room_id:
                    continue
                members = await rooms.get_members(room_id)
                if username not in members:
                    await ws.send(make_packet(MsgType.ERROR, error="NOT_IN_ROOM"))
                    continue

                pkt["from"] = username
                await rooms.route_room_message(room_id, pkt, username, router)
                await ws.send(make_packet(MsgType.DELIVERY,
                                          msg_id=pkt["id"],
                                          status="delivered"))

            # ── Typing indicator ──────────────────────────────────────────
            elif ptype == MsgType.TYPING:
                to_user = pkt.get("to")
                room_id = pkt.get("room_id")
                if room_id:
                    pkt["from"] = username
                    await rooms.route_room_message(room_id, pkt, username, router)
                elif to_user:
                    target_ws = router.get(to_user)
                    if target_ws:
                        pkt["from"] = username
                        try:
                            await target_ws.send(json.dumps(pkt))
                        except Exception:
                            pass

            # ── Sync missed messages ──────────────────────────────────────
            elif ptype == MsgType.SYNC_REQUEST:
                since = pkt.get("since", 0)
                queued = await offline.flush(username, since)
                await ws.send(make_packet(MsgType.SYNC_RESPONSE, messages=queued))
                await offline.clear(username)

            # ── Online list ───────────────────────────────────────────────
            elif ptype == MsgType.STATUS:
                await ws.send(make_packet(MsgType.ONLINE_LIST,
                                          users=router.online_users()))

            # ── Keepalive ─────────────────────────────────────────────────
            elif ptype == MsgType.PING:
                await ws.send(make_packet(MsgType.PONG))

    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        log.exception(f"Unhandled error for {username}: {e}")
    finally:
        if username:
            router.unregister(username)
            _rate_limiter.reset(username)
            await _broadcast_status(username, "offline")
            log.info(f"[-] {username} disconnected")
        _rate_limiter.reset(peer)
