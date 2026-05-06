# ChatterTerminal Security

Core rule: the server routes sealed envelopes.

Implemented protections:

- X25519 identity keys generated on the client.
- Client private keys encrypted locally with Argon2id + AES-256-GCM.
- Server passwords stored with PBKDF2-HMAC-SHA256, 310,000 iterations.
- Direct messages use ephemeral X25519 ECDH + HKDF + AES-256-GCM.
- Contact keys are pinned in client SQLite.
- Safety numbers can be verified with `/verify @user`.
- File chunks use AES-256-GCM and SHA-256 checksums.
- File keys are wrapped to the recipient public key; the server stores only encrypted key envelopes.
- Audio frames use AES-256-GCM and session-bound call keys.
- Server-side rate limiting throttles abusive packet streams.

Important operational notes:

- Run behind TLS/WSS for untrusted networks.
- Treat first-use contact keys as TOFU until manually verified.
- Existing local accounts created before PBKDF2 remain login-compatible, but new registrations use PBKDF2.
