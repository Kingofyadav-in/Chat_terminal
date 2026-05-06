"""
AES-256-GCM message encryption using ephemeral X25519 ECDH + HKDF-SHA256.

Each message uses a fresh ephemeral keypair for forward secrecy.
"""

import base64
import os

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
)

# HKDF parameters
_HKDF_SALT = b"ChatterTerminal-v1"
_HKDF_INFO = b"message-key"
_KEY_LEN = 32
_NONCE_LEN = 12
_TAG_LEN = 16


def _ecdh_and_derive(private_key_raw: bytes, peer_public_key_raw: bytes) -> bytes:
    """Perform X25519 ECDH and derive a symmetric key via HKDF-SHA256.

    Args:
        private_key_raw:    32-byte raw X25519 private key.
        peer_public_key_raw: 32-byte raw X25519 public key of the peer.

    Returns:
        32-byte derived AES key.
    """
    private_key = X25519PrivateKey.from_private_bytes(private_key_raw)
    peer_public_key = X25519PublicKey.from_public_bytes(peer_public_key_raw)
    shared_secret: bytes = private_key.exchange(peer_public_key)

    hkdf = HKDF(
        algorithm=SHA256(),
        length=_KEY_LEN,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    return hkdf.derive(shared_secret)


def encrypt_message(plaintext: str, recipient_public_key: bytes) -> dict:
    """Encrypt a plaintext message for a recipient using ephemeral ECDH + AES-256-GCM.

    A fresh ephemeral X25519 keypair is generated per message to provide
    forward secrecy — compromise of the recipient's long-term private key
    does not expose past messages.

    Args:
        plaintext:             The message text to encrypt.
        recipient_public_key:  32-byte raw X25519 public key of the recipient.

    Returns:
        A dict with the following base64-encoded fields:
            ephemeral_pubkey: Ephemeral sender public key (32 bytes, b64).
            ciphertext:       Encrypted data without the GCM tag (b64).
            nonce:            12-byte AES-GCM nonce (b64).
            auth_tag:         16-byte GCM authentication tag (b64).
    """
    # Generate ephemeral keypair
    ephemeral_private = X25519PrivateKey.generate()
    ephemeral_private_raw: bytes = ephemeral_private.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    ephemeral_public_raw: bytes = ephemeral_private.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )

    # Derive symmetric key via ECDH + HKDF
    symmetric_key = _ecdh_and_derive(ephemeral_private_raw, recipient_public_key)

    # AES-256-GCM encryption
    nonce = os.urandom(_NONCE_LEN)
    aesgcm = AESGCM(symmetric_key)
    ciphertext_with_tag: bytes = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    # AESGCM appends 16-byte tag at the end — split manually
    ciphertext = ciphertext_with_tag[:-_TAG_LEN]
    auth_tag = ciphertext_with_tag[-_TAG_LEN:]

    return {
        "ephemeral_pubkey": base64.b64encode(ephemeral_public_raw).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "auth_tag": base64.b64encode(auth_tag).decode("ascii"),
    }


def decrypt_message(payload: dict, my_private_key: bytes) -> str:
    """Decrypt a message payload using our private key.

    Reconstructs the shared secret via ECDH with the sender's ephemeral
    public key, re-derives the symmetric key via HKDF, and decrypts.

    Args:
        payload:        Dict as produced by :func:`encrypt_message`.
        my_private_key: 32-byte raw X25519 private key of the recipient.

    Returns:
        Decrypted plaintext string.

    Raises:
        ValueError: If authentication fails or the payload is malformed.
        KeyError:   If required fields are missing from payload.
    """
    try:
        ephemeral_pubkey_raw = base64.b64decode(payload["ephemeral_pubkey"])
        ciphertext = base64.b64decode(payload["ciphertext"])
        nonce = base64.b64decode(payload["nonce"])
        auth_tag = base64.b64decode(payload["auth_tag"])
    except (KeyError, ValueError, Exception) as exc:
        raise ValueError(f"Malformed message payload: {exc}") from exc

    if len(nonce) != _NONCE_LEN:
        raise ValueError(f"Invalid nonce length: expected {_NONCE_LEN}, got {len(nonce)}")
    if len(auth_tag) != _TAG_LEN:
        raise ValueError(
            f"Invalid auth_tag length: expected {_TAG_LEN}, got {len(auth_tag)}"
        )

    # Derive the same symmetric key
    symmetric_key = _ecdh_and_derive(my_private_key, ephemeral_pubkey_raw)

    # Reassemble ciphertext_with_tag for AESGCM
    ciphertext_with_tag = ciphertext + auth_tag

    aesgcm = AESGCM(symmetric_key)
    try:
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    except Exception as exc:
        raise ValueError(
            "Message authentication failed — possible tampering or wrong key"
        ) from exc

    return plaintext_bytes.decode("utf-8")
