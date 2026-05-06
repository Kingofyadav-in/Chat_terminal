"""
X25519 keypair generation and Argon2id private key protection.
"""

import os
import base64
import struct

from argon2.low_level import hash_secret_raw, Type
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
)


# ---------------------------------------------------------------------------
# Argon2id parameters
# ---------------------------------------------------------------------------
_TIME_COST = 3
_MEMORY_COST = 65536  # 64 MiB
_PARALLELISM = 1
_HASH_LEN = 32

# Wire-format sizes
_SALT_LEN = 16
_NONCE_LEN = 12


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh X25519 keypair.

    Returns:
        (private_key_raw, public_key_raw) — each exactly 32 bytes.
    """
    private_key = X25519PrivateKey.generate()
    private_key_raw: bytes = private_key.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    public_key_raw: bytes = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    return private_key_raw, public_key_raw


def public_from_private(private_key: bytes) -> bytes:
    """Derive the 32-byte public key from raw private key bytes.

    Args:
        private_key: 32-byte raw X25519 private key.

    Returns:
        32-byte raw X25519 public key.
    """
    pk = X25519PrivateKey.from_private_bytes(private_key)
    return pk.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )


def _derive_key(password: str, salt: bytes) -> bytes:
    """Run Argon2id KDF to derive a 32-byte AES key from password + salt."""
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=_TIME_COST,
        memory_cost=_MEMORY_COST,
        parallelism=_PARALLELISM,
        hash_len=_HASH_LEN,
        type=Type.ID,
    )


def encrypt_private_key(private_key: bytes, password: str) -> str:
    """Encrypt a 32-byte private key with Argon2id + AES-256-GCM.

    Wire format (all concatenated, then base64-encoded):
        salt (16 bytes) || nonce (12 bytes) || ciphertext+tag (48 bytes)

    Args:
        private_key: 32-byte raw private key.
        password:    UTF-8 password string.

    Returns:
        Base64-encoded string containing salt + nonce + ciphertext+tag.
    """
    if len(private_key) != 32:
        raise ValueError(f"Expected 32-byte private key, got {len(private_key)}")

    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(password, salt)

    aesgcm = AESGCM(key)
    ciphertext_with_tag = aesgcm.encrypt(nonce, private_key, None)

    blob = salt + nonce + ciphertext_with_tag
    return base64.b64encode(blob).decode("ascii")


def decrypt_private_key(encrypted: str, password: str) -> bytes:
    """Decrypt a private key previously encrypted with :func:`encrypt_private_key`.

    Args:
        encrypted: Base64 string produced by :func:`encrypt_private_key`.
        password:  UTF-8 password string.

    Returns:
        32-byte raw private key.

    Raises:
        ValueError: If decryption or authentication fails (wrong password / tampered blob).
    """
    try:
        blob = base64.b64decode(encrypted)
    except Exception as exc:
        raise ValueError("Invalid base64 encoding for encrypted private key") from exc

    expected_len = _SALT_LEN + _NONCE_LEN + 32 + 16  # 76 bytes
    if len(blob) != expected_len:
        raise ValueError(
            f"Encrypted blob has unexpected length {len(blob)}, expected {expected_len}"
        )

    salt = blob[:_SALT_LEN]
    nonce = blob[_SALT_LEN : _SALT_LEN + _NONCE_LEN]
    ciphertext_with_tag = blob[_SALT_LEN + _NONCE_LEN :]

    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)

    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    except Exception as exc:
        raise ValueError("Decryption failed — wrong password or corrupted data") from exc

    return plaintext
