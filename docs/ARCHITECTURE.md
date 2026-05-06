# ChatterTerminal Architecture

ChatterTerminal is split into a curses client, a dumb routing server, and shared packet definitions.

The server owns availability only: WebSocket routing, offline queues, room membership, file chunk storage, call signaling, and relay fallback. It does not receive direct-message plaintext, file keys, or audio keys.

The client owns trust and data: local private keys, pinned contact keys, decrypted message history, file-key unwrap, audio call keys, and safety-number verification.

Services:

- WebSocket `8765`: auth, messages, rooms, files metadata, call signaling, receipts, sync.
- TCP `8766`: encrypted file chunk upload/download.
- UDP `8767`: encrypted audio relay fallback.

Local persistence:

- Client SQLite: account keys, contacts, messages, rooms, files, calls.
- Server SQLite: users, public keys, sessions, offline queue, room memberships, file metadata/chunks, call sessions.
