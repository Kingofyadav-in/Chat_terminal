# ChatterTerminal Protocol

All WebSocket packets are JSON:

```json
{
  "version": "1.0",
  "type": "MESSAGE",
  "id": "uuid",
  "timestamp": 1715000000
}
```

Packet families:

- Auth: `AUTH_REGISTER`, `AUTH_LOGIN`, `AUTH_LOGOUT`, `AUTH_RESPONSE`
- Keys: `KEY_REQUEST`, `KEY_RESPONSE`
- Messaging: `MESSAGE`, `MESSAGE_EDIT`, `MESSAGE_DELETE`, `DELIVERY`, `READ`, `TYPING`
- Sync: `SYNC_REQUEST`, `SYNC_RESPONSE`
- Rooms: `ROOM_CREATE`, `ROOM_JOIN`, `ROOM_LEAVE`, `ROOM_MEMBERS`, `ROOM_MEMBERS_LIST`, `ROOM_INFO`, `ROOM_MESSAGE`
- Files: `FILE_INIT`, `FILE_ACK`, `FILE_DONE`, `FILE_CANCEL`
- Calls: `CALL_INVITE`, `CALL_ACCEPT`, `CALL_DECLINE`, `CALL_END`, `CALL_RELAY`
- Presence/system: `ONLINE_LIST`, `STATUS`, `PING`, `PONG`, `ERROR`

File TCP framing:

```text
UPLOAD:
  <file_id>\n
  server: HAVE <comma-separated chunk indexes>\n
  repeated:
    uint32 chunk_index
    uint32 data_length
    64 bytes SHA-256 plaintext checksum hex
    encrypted payload: nonce(12) || tag(16) || ciphertext

DOWNLOAD:
  DOWNLOAD <file_id>\n
  repeated server chunks using the same chunk frame
```

Audio relay packets use `CALL_RELAY` with base64 audio payloads. Raw audio payloads are encrypted before relay.
