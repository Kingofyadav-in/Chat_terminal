<div align="center">

```
 ██████╗██╗  ██╗ █████╗ ████████╗████████╗███████╗██████╗ 
██╔════╝██║  ██║██╔══██╗╚══██╔══╝╚══██╔══╝██╔════╝██╔══██╗
██║     ███████║███████║   ██║      ██║   █████╗  ██████╔╝
██║     ██╔══██║██╔══██║   ██║      ██║   ██╔══╝  ██╔══██╗
╚██████╗██║  ██║██║  ██║   ██║      ██║   ███████╗██║  ██║
 ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝      ╚═╝   ╚══════╝╚═╝  ╚═╝
████████╗███████╗██████╗ ███╗   ███╗██╗███╗   ██╗ █████╗ ██╗
╚══██╔══╝██╔════╝██╔══██╗████╗ ████║██║████╗  ██║██╔══██╗██║
   ██║   █████╗  ██████╔╝██╔████╔██║██║██╔██╗ ██║███████║██║
   ██║   ██╔══╝  ██╔══██╗██║╚██╔╝██║██║██║╚██╗██║██╔══██║██║
   ██║   ███████╗██║  ██║██║ ╚═╝ ██║██║██║ ╚████║██║  ██║███████╗
   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝
```

**Self-hosted · End-to-end encrypted · Terminal-native chat**

> *Server knows nothing. Clients own everything.*

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Crypto](https://img.shields.io/badge/Crypto-X25519%20%2B%20AES--256--GCM-orange?style=flat-square&logo=gnuprivacyguard&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-22c55e?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-lightgrey?style=flat-square)
![Zero Cloud](https://img.shields.io/badge/Cloud-Zero%20Dependencies-blueviolet?style=flat-square)

</div>

---

**ChatterTerminal** is a self-hosted chat system that runs in the terminal. It includes encrypted direct messages, rooms, encrypted file transfer, and voice-call signaling/relay support. Clients own their local keys and encrypt sensitive payloads before sending them; the server handles auth, routing, queuing, rooms, and relays.

No browser. No Electron. No third-party cloud service in the default path. One Python server process on infrastructure you control.

---

## Terminal Preview

```
╔══════════════════════════════════════════════════════════════════════════╗
║  ChatterTerminal v1.0  ▸  you (online)  ▸  🔒 E2E encrypted            ║
╠══════════════╦═══════════════════════════════════════════════════════════╣
║  Contacts    ║  [DM: alice]                                              ║
║  ─────────── ║  ──────────────────────────────────────────────────────   ║
║  alice   ●   ║  alice  09:14  did you get the files I sent?             ║
║  bob     ●   ║  you    09:15  yeah, transfer completed ✓                ║
║  charlie ○   ║  alice  09:15  let's do a quick call                     ║
║              ║  you    09:16  sure — /call start @alice                 ║
║  Rooms       ║                                                           ║
║  ─────────── ║  ╔════ Outgoing call ══════════════════════════════╗     ║
║  #general  3 ║  ║  Calling alice...                 [End: Ctrl+E] ║     ║
║  #dev      5 ║  ║  🔐 Audio encrypted · Opus codec · 48 kHz       ║     ║
║              ║  ╚════════════════════════════════════════════════╝     ║
╠══════════════╩═══════════════════════════════════════════════════════════╣
║  > _                                               [/help for commands]  ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## Features

- **End-to-end encrypted DMs** — X25519 key exchange + HKDF-SHA256 + AES-256-GCM
- **Group rooms** — multi-user channels with join/leave, membership lists, and persistent history
- **Encrypted file transfer** — chunked AES-256-GCM over TCP; SHA-256 integrity checks; pause, resume, and TTL cleanup
- **Audio call support** — WebSocket signaling, UDP relay, jitter buffer, mute, and push-to-talk controls
- **Offline queue** — messages queued server-side and synced in order on reconnect
- **Delivery receipts + typing indicators** — sent / delivered / read status
- **Safety numbers** — Signal-style fingerprint verification for out-of-band contact authentication
- **TOFU-style key caching** — verify contacts with safety numbers before trusting identity-sensitive conversations
- **Pure curses UI** — no browser, no Electron, no GUI toolkit
- **Zero cloud** — one Python process, SQLite storage, no external services required

---

## Security Model

ChatterTerminal uses a **server-blind** architecture. Every sensitive payload is encrypted on the client before it leaves the machine. The server routes, queues, and relays — it never decrypts.

| Layer | Primitive | Detail |
|---|---|---|
| Message key exchange | X25519 ECDH | Ephemeral sender keypair per message → forward secrecy |
| Key derivation | HKDF-SHA256 | Separate send/receive subkeys from the shared secret |
| Message encryption | AES-256-GCM | 96-bit random nonce; 128-bit authentication tag |
| File chunks | AES-256-GCM | Unique nonce per chunk + SHA-256 checksum |
| Audio packets | AES-256-GCM | Session key from ECDH+HKDF at call start |
| Private key at rest | Argon2id → AES-256-GCM | 64 MiB / t=3 / p=1; encrypted blob in local SQLite |
| Server passwords | PBKDF2-HMAC-SHA256 | 310,000 iterations |
| Contact verification | Safety numbers (TOFU) | Fingerprint mismatch warns and blocks delivery |

**Threat model:** A fully compromised server cannot read message content, decrypt stored files, or silently impersonate users to contacts who have verified safety numbers.

Important limitations:

- Room messages can fall back to plaintext when member public keys are not available.
- First-use keys should be verified with `/verify @user`; otherwise initial trust depends on the key first returned by the server.
- The built-in WebSocket server does not terminate TLS. Use a reverse proxy and `wss://` for untrusted networks.

For details, see [docs/SECURITY.md](docs/SECURITY.md), [docs/PROTOCOL.md](docs/PROTOCOL.md), [docs/AUDIO.md](docs/AUDIO.md), and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Quick Start

```bash
# Clone or download this repository first, then:
cd ChatterTerminal
chmod +x setup.sh
./setup.sh
```

**Terminal 1 — run the server:**

```bash
. .venv/bin/activate
ct-server
```

**Terminal 2 — connect a client:**

```bash
. .venv/bin/activate
ct-client
```

Register a username and start chatting. That's it.

Testing multiple users on one machine is easiest with profiles:

```bash
ct-client --profile alice
ct-client --profile bob
```

---

## Installation

**Requirements:** Python 3.11+, PortAudio (for audio calls only)

```bash
# Ubuntu / Debian
sudo apt install portaudio19-dev python3-dev python3-venv

# macOS
brew install portaudio

# Then run the setup script
chmod +x setup.sh
./setup.sh
```

The setup script creates a virtualenv, installs all dependencies, and creates required directories under `~/.chatterminal/` and `~/.chatterminal_server/`.

> **No audio?** Everything works without PyAudio — calls will fail gracefully but messaging, file transfer, and rooms are unaffected. Install `pyaudio` and `opuslib` separately when your system has PortAudio.

### Manual install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

### CLI options

```bash
ct-client --profile alice       # use ~/.chatterminal/profiles/alice/chatterminal.db
ct-client --db /tmp/alice.db    # override the client SQLite database path
ct-client --reset-local         # clear local client state before starting

ct-server --reset-server        # clear server-side users, sessions, rooms, files, and calls
```

---

## Configuration

All configuration is via environment variables. No config files.

### Server

| Variable | Default | Description |
|---|---|---|
| `CT_HOST` | `0.0.0.0` | WebSocket bind address |
| `CT_PORT` | `8765` | WebSocket port |
| `CT_TCP_HOST` | ← `CT_HOST` | File relay bind address |
| `CT_TCP_PORT` | `8766` | File relay port |
| `CT_UDP_RELAY_HOST` | ← `CT_HOST` | Audio relay bind address |
| `CT_UDP_RELAY_PORT` | `8767` | Audio relay port |
| `CT_DB` | `~/.chatterminal_server/server.db` | Server SQLite database |

### Client

| Variable | Default | Description |
|---|---|---|
| `CT_SERVER` | `ws://localhost:8765` | Server WebSocket URL |
| `CT_TCP_HOST` | `127.0.0.1` | File relay host |
| `CT_TCP_PORT` | `8766` | File relay port |
| `CT_CLIENT_DB` | `~/.chatterminal/chatterminal.db` | Client SQLite database |
| `CT_CLIENT_PROFILE` | unset | Named profile under `~/.chatterminal/profiles/` |

**Connecting to a remote server:**

```bash
CT_SERVER=ws://chat.yourdomain.com:8765 \
CT_TCP_HOST=chat.yourdomain.com \
ct-client
```

---

## Commands

Type any message and press **Enter** to send in the active conversation. Everything else is a slash command.

### Direct Messages

```
/dm @alice              Open (or switch to) a DM thread with alice
/verify @alice          Display safety number for out-of-band verification
```

### Rooms

```
/room create <name>         Create a new room
/room join <room_id>        Join by ID
/room open <room_id>        Switch active view to this room
/room members [room_id]     List current members
/room leave [room_id]       Leave the room
```

### File Transfer

```
/file send @alice /path/to/file    Send an AES-256-GCM encrypted file
/file accept <file_id>             Accept an incoming transfer
/file download <file_id>           Alias for /file accept
/file resume <file_id>             Resume an interrupted transfer
/file cancel <file_id>             Cancel a transfer
/file list                         Show all transfers and their status
```

Open a DM with the recipient before sending a file so the client can fetch and cache their public key:

```
/dm @alice
/file send @alice ./notes.txt
```

### Audio Calls

```
/call start @alice          Initiate an encrypted call
/call invite @alice         Alias for /call start
/call accept [session_id]   Accept an incoming call
/call decline [session_id]  Decline
/call end [session_id]      Hang up
/call mute                  Mute microphone
/call unmute                Unmute microphone
/call ptt on                Enable push-to-talk mode
/call talk on               Transmit (push-to-talk)
/call history               Show call log
```

### Client Commands

```
/online                     Request online-user status
/clear                      Clear the current in-memory message view
/logout                     Log out and stop the client
/quit | /exit | /q          Exit the client
/help                       Show the short command list
```

---

## Architecture

```
┌─────────────────────── Client A ───────────────────────┐
│  curses UI → handlers → crypto layer → connection pool │
│  X25519 identity key  (Argon2id-encrypted in SQLite)   │
└──────────────┬──────────────────┬──────────────────────┘
               │  WebSocket :8765 │  TCP :8766 / UDP :8767
               │  (msgs + signal) │  (files + audio relay)
┌──────────────▼──────────────────▼──────────────────────┐
│                  ChatterTerminal Server                  │
│   ws_server · tcp_server · udp_relay · router           │
│   auth · rooms · offline queue · call_manager           │
│   rate_limiter · SQLite (ciphertext only)               │
└──────────────┬──────────────────┬──────────────────────┘
               │                  │
┌──────────────▼──────────────────▼──────────────────────┐
│  curses UI → handlers → crypto layer → connection pool  │
└─────────────────────── Client B ───────────────────────┘

  :8765  WebSocket — messaging, signaling, key distribution
  :8766  TCP       — encrypted file chunk relay
  :8767  UDP       — encrypted audio relay fallback
```

---

## Project Structure

```
ChatterTerminal/
├── server/
│   ├── main.py           Asyncio entry point
│   ├── ws_server.py      WebSocket connection handler
│   ├── tcp_server.py     TCP file relay
│   ├── udp_relay.py      UDP audio relay
│   ├── router.py         Message dispatch
│   ├── auth.py           Registration / login / PBKDF2
│   ├── rooms.py          Room CRUD + membership
│   ├── offline.py        Offline queue + sync
│   ├── files.py          File session lifecycle
│   ├── call_manager.py   Call signaling state machine
│   ├── rate_limiter.py   Per-connection rate limiting
│   └── db/               schema · users · messages · files · calls
│
├── client/
│   ├── main.py           CLI entry point (click)
│   ├── app.py            Application orchestrator
│   ├── crypto/           keygen · encrypt · file_crypto · audio_crypto · fingerprint
│   ├── connection/       ws_client · tcp_client · udp_client · reconnect
│   ├── audio/            engine · codec · stream · jitter_buffer · nat
│   ├── ui/               screen · chat_view · sidebar · input_bar · call_overlay · notif
│   ├── handlers/         message · file · room · auth · call
│   └── db/               schema · messages · contacts · files · calls · rooms
│
├── shared/
│   ├── protocol.py       Packet types + wire format (JSON)
│   ├── constants.py      Shared constants
│   └── utils.py          Utilities
│
├── tests/
│   ├── test_crypto.py          X25519 · AES-GCM · Argon2id
│   ├── test_protocol.py        Packet encoding / decoding
│   ├── test_file_transfer.py   Chunked transfer + resume
│   ├── test_audio.py           Jitter buffer · codec fallback
│   ├── test_auth.py            Registration · login · PBKDF2
│   ├── test_send_recv.py       End-to-end send/receive roundtrip
│   └── test_performance.py     Rate limiting · throughput
│
├── setup.sh              One-shot install script
├── requirements.txt      Pinned dependencies
└── pyproject.toml        Package metadata
```

---

## Testing

```bash
. .venv/bin/activate
pytest
```

The suite covers: X25519 + AES-GCM crypto, Argon2id key protection, protocol encoding/decoding, chunked file transfer with resume, jitter buffer behaviour, server auth flows, rate limiting, and full send/receive roundtrips.

```bash
pytest -v                  # verbose output
pytest tests/test_crypto.py  # single module
```

---

## Self-Hosting

ChatterTerminal is a single Python process — no Docker required, though it works fine in a container.

**systemd unit** (`/etc/systemd/system/chatterminal.service`):

```ini
[Unit]
Description=ChatterTerminal Server
After=network.target

[Service]
User=chatterminal
WorkingDirectory=/opt/ChatterTerminal
Environment=CT_HOST=0.0.0.0
Environment=CT_PORT=8765
ExecStart=/opt/ChatterTerminal/.venv/bin/ct-server
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now chatterminal
```

**TLS via reverse proxy** (nginx example):

```nginx
location /chat {
    proxy_pass http://127.0.0.1:8765;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

Then clients connect with `CT_SERVER=wss://chat.yourdomain.com/chat`.

---

## Reset

Wipe the local client database and keys (server data is unaffected):

```bash
ct-client --reset-local
```

Wipe server-side users, sessions, rooms, files, and calls:

```bash
ct-server --reset-server
```

## Data Locations

Client:

- `~/.chatterminal/chatterminal.db`
- `~/.chatterminal/profiles/<name>/chatterminal.db`
- `~/.chatterminal/downloads/`

Server:

- `~/.chatterminal_server/server.db`
- `~/.chatterminal_server/files/`

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `websockets` | 12.0 | Async WebSocket transport |
| `cryptography` | ≥ 42.0 | X25519, AES-256-GCM, HKDF |
| `argon2-cffi` | ≥ 23.1 | Argon2id private key encryption |
| `bcrypt` | ≥ 4.1 | Server-side password hashing |
| `aiosqlite` | ≥ 0.20 | Async SQLite |
| `click` | ≥ 8.1 | CLI entry points |
| `pyaudio` | optional | Audio capture / playback |
| `opuslib` | optional | Opus codec for calls |
| `pytest` | 8.1 | Test runner |
| `pytest-asyncio` | 0.23 | Async test support |

---

## License

MIT — use it, fork it, self-host it. Add a `LICENSE` file before publishing if you want package hosts and GitHub to detect the license automatically.

---

<div align="center">
<sub>Built for people who believe their conversations belong to them.</sub>
</div>
