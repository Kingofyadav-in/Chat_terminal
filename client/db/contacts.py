"""
Local SQLite operations for contacts and account management.

Handles contact roster, online status, and cryptographic key storage
for the local account and all known contacts.
"""

import base64

import aiosqlite
from client.config import DB_PATH
from client.crypto.fingerprint import pubkey_fingerprint
from shared.utils import now


# ---------------------------------------------------------------------------
# Account helpers
# ---------------------------------------------------------------------------

async def get_account() -> dict | None:
    """Return the single local account row, or None if not set up yet."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM account LIMIT 1") as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def save_account(
    uid: str,
    username: str,
    server_url: str,
    token: str,
    private_key: str | None = None,
    public_key: str | None = None,
) -> None:
    """Persist (or replace) the local account record.

    Args:
        uid:        Unique account ID (UUID).
        username:   Chosen username.
        server_url: WebSocket server URL.
        token:      Initial session token.
        private_key: Optional encrypted private key.
        public_key:  Optional base64-encoded public key.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM account")
        await db.execute(
            "INSERT INTO account"
            " (id, username, private_key, public_key, server_url, session_token)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (uid, username, private_key, public_key, server_url, token),
        )
        await db.commit()


async def update_token(token: str) -> None:
    """Replace the stored session token after a successful re-auth.

    Args:
        token: New session token from the server.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE account SET session_token = ?", (token,))
        await db.commit()


async def clear_session_token() -> None:
    """Remove the saved session token so the client stops auto-login."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE account SET session_token = NULL")
        await db.commit()


# ---------------------------------------------------------------------------
# Contact roster
# ---------------------------------------------------------------------------

async def add_contact(username: str, public_key: str | None = None) -> None:
    """Add a contact if not already present (no-op on duplicate).

    Args:
        username:   Contact's username.
        public_key: Optional base64-encoded X25519 public key.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO contacts (username, public_key, added_at)"
            " VALUES (?, ?, ?)",
            (username, public_key, now()),
        )
        await db.commit()


async def set_status(username: str, status: str) -> None:
    """Update the online/offline status for a contact.

    Args:
        username: Contact's username.
        status:   New status string, e.g. 'online' or 'offline'.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE contacts SET status = ? WHERE username = ?",
            (status, username),
        )
        await db.commit()


async def all_contacts() -> list[dict]:
    """Return all contacts ordered by status (online first) then name.

    Returns:
        List of contact row dicts.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM contacts ORDER BY status DESC, username ASC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Contact public key management
# ---------------------------------------------------------------------------

async def store_public_key(username: str, public_key_b64: str) -> None:
    """Store or update a contact's X25519 public key.

    Inserts the contact row if it doesn't exist yet (e.g. a message arrived
    before the contact was explicitly added).

    Args:
        username:      Contact's username.
        public_key_b64: Base64-encoded 32-byte X25519 public key.
    """
    fingerprint = None
    try:
        fingerprint = pubkey_fingerprint(base64.b64decode(public_key_b64))
    except Exception:
        pass

    async with aiosqlite.connect(DB_PATH) as db:
        # Ensure the row exists before updating
        await db.execute(
            "INSERT OR IGNORE INTO contacts (username, added_at) VALUES (?, ?)",
            (username, now()),
        )
        await db.execute(
            "UPDATE contacts SET public_key = ?, fingerprint = ? WHERE username = ?",
            (public_key_b64, fingerprint, username),
        )
        await db.commit()


async def get_public_key(username: str) -> str | None:
    """Retrieve the stored base64 public key for a contact.

    Args:
        username: Contact's username.

    Returns:
        Base64-encoded public key string, or None if not stored.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT public_key FROM contacts WHERE username = ?",
            (username,),
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return row[0]  # may be None if column is NULL


async def set_verified(username: str, verified: bool = True) -> None:
    """Mark a contact's safety number as verified or unverified."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE contacts SET verified = ? WHERE username = ?",
            (1 if verified else 0, username),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Own key management (stored in the account row)
# ---------------------------------------------------------------------------

async def store_my_keys(
    private_key_encrypted: str, public_key_b64: str
) -> None:
    """Persist the local user's encrypted private key and public key.

    The private key is stored in its Argon2id + AES-256-GCM encrypted form
    (as produced by client.crypto.keygen.encrypt_private_key) so the raw
    key is never written to disk.

    Args:
        private_key_encrypted: Base64 ciphertext from encrypt_private_key().
        public_key_b64:        Base64-encoded 32-byte X25519 public key.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE account SET private_key = ?, public_key = ?",
            (private_key_encrypted, public_key_b64),
        )
        await db.commit()


async def get_my_keys() -> tuple[str | None, str | None]:
    """Return the local user's stored key material.

    Returns:
        A 2-tuple (encrypted_private_key, public_key_b64).
        Either value may be None if keys have not been generated yet.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT private_key, public_key FROM account LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None, None
            return row[0], row[1]
