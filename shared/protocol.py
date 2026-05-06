from enum import Enum
import json
import uuid
import time


class MsgType(str, Enum):
    AUTH_REGISTER  = "AUTH_REGISTER"
    AUTH_LOGIN     = "AUTH_LOGIN"
    AUTH_LOGOUT    = "AUTH_LOGOUT"
    AUTH_RESPONSE  = "AUTH_RESPONSE"

    KEY_REQUEST    = "KEY_REQUEST"
    KEY_RESPONSE   = "KEY_RESPONSE"

    ROOM_CREATE       = "ROOM_CREATE"
    ROOM_JOIN         = "ROOM_JOIN"
    ROOM_LEAVE        = "ROOM_LEAVE"
    ROOM_MEMBERS      = "ROOM_MEMBERS"
    ROOM_MEMBERS_LIST = "ROOM_MEMBERS_LIST"
    ROOM_INFO         = "ROOM_INFO"
    ROOM_MESSAGE      = "ROOM_MESSAGE"

    FILE_INIT         = "FILE_INIT"
    FILE_ACK          = "FILE_ACK"
    FILE_DONE         = "FILE_DONE"
    FILE_CANCEL       = "FILE_CANCEL"

    CALL_INVITE       = "CALL_INVITE"
    CALL_ACCEPT       = "CALL_ACCEPT"
    CALL_DECLINE      = "CALL_DECLINE"
    CALL_END          = "CALL_END"
    CALL_RELAY        = "CALL_RELAY"

    MESSAGE        = "MESSAGE"
    MESSAGE_EDIT   = "MESSAGE_EDIT"
    MESSAGE_DELETE = "MESSAGE_DELETE"
    DELIVERY       = "DELIVERY"
    READ           = "READ"
    TYPING         = "TYPING"

    SYNC_REQUEST   = "SYNC_REQUEST"
    SYNC_RESPONSE  = "SYNC_RESPONSE"

    ONLINE_LIST    = "ONLINE_LIST"
    STATUS         = "STATUS"

    PING           = "PING"
    PONG           = "PONG"
    ERROR          = "ERROR"


def make_packet(pkt_type: MsgType | str, **kwargs) -> str:
    pkt = {
        "version": "1.0",
        "type": pkt_type.value if isinstance(pkt_type, MsgType) else pkt_type,
        "id": str(uuid.uuid4()),
        "timestamp": int(time.time()),
    }
    pkt.update(kwargs)
    return json.dumps(pkt)


def parse_packet(data: str) -> dict:
    return json.loads(data)
