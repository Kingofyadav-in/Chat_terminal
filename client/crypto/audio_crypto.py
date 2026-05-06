"""
Per-packet UDP audio encryption for real-time voice calls.

Nonces are derived deterministically from the packet sequence number to
avoid nonce reuse while still allowing the receiver to reconstruct the
nonce from the sequence number embedded in the UDP stream framing.
"""

import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_NONCE_LEN = 12
_TAG_LEN = 16

# HKDF parameters for call key derivation
_HKDF_INFO_AUDIO = b"audio-key"
_KEY_LEN = 32


def _seq_to_nonce(seq: int) -> bytes:
    """Derive a 12-byte nonce from a packet sequence number.

    The sequence number is packed as a little-endian 64-bit integer and
    zero-padded to 12 bytes.  This makes nonces unique per packet and
    allows the receiver to reconstruct them without transmitting the nonce
    explicitly in the UDP payload.

    Args:
        seq: Unsigned 64-bit packet sequence number.

    Returns:
        12-byte nonce.
    """
    # 8 bytes LE seq + 4 bytes zero padding = 12 bytes
    return struct.pack("<Q", seq & 0xFFFFFFFFFFFFFFFF) + b"\x00\x00\x00\x00"


def encrypt_packet(opus_frame: bytes, key: bytes, seq: int) -> tuple[bytes, bytes]:
    """Encrypt a single Opus audio frame for UDP transmission.

    The nonce is deterministically derived from the sequence number so
    the receiver can reconstruct it from packet framing without sending
    it over the wire (saving ~12 bytes per packet).

    Args:
        opus_frame: Raw Opus-encoded audio bytes.
        key:        32-byte AES-256 session key.
        seq:        Packet sequence number (must be unique per call).

    Returns:
        A tuple of (nonce, ciphertext_with_tag) where:
            nonce:             12-byte nonce (derived from seq).
            ciphertext_with_tag: Encrypted frame bytes with 16-byte GCM tag appended.

    Raises:
        ValueError: If key is not 32 bytes.
    """
    if len(key) != 32:
        raise ValueError(f"Expected 32-byte AES key, got {len(key)}")

    nonce = _seq_to_nonce(seq)
    aesgcm = AESGCM(key)
    ciphertext_with_tag: bytes = aesgcm.encrypt(nonce, opus_frame, None)

    return nonce, ciphertext_with_tag


def decrypt_packet(ciphertext_with_tag: bytes, nonce: bytes, key: bytes) -> bytes:
    """Decrypt and verify an encrypted Opus audio frame.

    Args:
        ciphertext_with_tag: Encrypted frame bytes with 16-byte GCM tag appended.
        nonce:               12-byte nonce (reconstructed from seq number by caller).
        key:                 32-byte AES-256 session key.

    Returns:
        Raw Opus-encoded audio bytes.

    Raises:
        ValueError: If authentication fails (packet tampered or wrong key).
    """
    if len(key) != 32:
        raise ValueError(f"Expected 32-byte AES key, got {len(key)}")
    if len(nonce) != _NONCE_LEN:
        raise ValueError(f"Expected {_NONCE_LEN}-byte nonce, got {len(nonce)}")

    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    except Exception as exc:
        raise ValueError(
            "Audio packet authentication failed — possible replay or tampering"
        ) from exc

    return plaintext


def derive_call_key(shared_secret: bytes, session_id: str) -> bytes:
    """Derive a 32-byte AES session key for a voice call.

    Uses HKDF-SHA256 with the call's session ID as the salt so each call
    produces an independent key even if the shared_secret is reused.

    Args:
        shared_secret: Raw shared secret bytes (e.g. from X25519 ECDH).
        session_id:    Unique call session identifier string.

    Returns:
        32-byte AES-256 call key.
    """
    hkdf = HKDF(
        algorithm=SHA256(),
        length=_KEY_LEN,
        salt=session_id.encode("utf-8"),
        info=_HKDF_INFO_AUDIO,
    )
    return hkdf.derive(shared_secret)
