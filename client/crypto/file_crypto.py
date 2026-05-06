"""
Per-chunk file encryption using AES-256-GCM.

Each chunk is encrypted independently with a fresh random nonce,
allowing parallel encryption/decryption and random chunk access.
"""

import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LEN = 12
_TAG_LEN = 16


def generate_file_key() -> bytes:
    """Generate a random 32-byte AES-256 file encryption key.

    Returns:
        32 bytes of cryptographically random data.
    """
    return os.urandom(32)


def encrypt_chunk(data: bytes, key: bytes) -> tuple[bytes, bytes, bytes]:
    """Encrypt a chunk of file data with AES-256-GCM.

    Args:
        data: Plaintext chunk bytes.
        key:  32-byte AES-256 key.

    Returns:
        A tuple of (ciphertext, nonce, tag) where:
            ciphertext: Encrypted bytes (same length as plaintext).
            nonce:      12-byte random nonce.
            tag:        16-byte GCM authentication tag.

    Raises:
        ValueError: If key is not 32 bytes.
    """
    if len(key) != 32:
        raise ValueError(f"Expected 32-byte AES key, got {len(key)}")

    nonce = os.urandom(_NONCE_LEN)
    aesgcm = AESGCM(key)
    ciphertext_with_tag: bytes = aesgcm.encrypt(nonce, data, None)

    # AESGCM appends 16-byte tag — split it off
    ciphertext = ciphertext_with_tag[:-_TAG_LEN]
    tag = ciphertext_with_tag[-_TAG_LEN:]

    return ciphertext, nonce, tag


def decrypt_chunk(ciphertext: bytes, nonce: bytes, tag: bytes, key: bytes) -> bytes:
    """Decrypt and authenticate a file chunk encrypted with :func:`encrypt_chunk`.

    Args:
        ciphertext: Encrypted chunk bytes.
        nonce:      12-byte nonce used during encryption.
        tag:        16-byte GCM authentication tag.
        key:        32-byte AES-256 key.

    Returns:
        Plaintext bytes.

    Raises:
        ValueError: If authentication fails (tampered data) or key/nonce invalid.
    """
    if len(key) != 32:
        raise ValueError(f"Expected 32-byte AES key, got {len(key)}")
    if len(nonce) != _NONCE_LEN:
        raise ValueError(f"Expected {_NONCE_LEN}-byte nonce, got {len(nonce)}")
    if len(tag) != _TAG_LEN:
        raise ValueError(f"Expected {_TAG_LEN}-byte tag, got {len(tag)}")

    aesgcm = AESGCM(key)
    ciphertext_with_tag = ciphertext + tag

    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    except Exception as exc:
        raise ValueError(
            "Chunk authentication failed — data may be corrupted or tampered"
        ) from exc

    return plaintext


def chunk_checksum(data: bytes) -> str:
    """Compute the SHA-256 hex digest of a plaintext chunk.

    Used to verify chunk integrity before encryption or after decryption,
    independently of the AES-GCM authentication tag.

    Args:
        data: Plaintext chunk bytes.

    Returns:
        64-character lowercase hex string of the SHA-256 digest.
    """
    return hashlib.sha256(data).hexdigest()
