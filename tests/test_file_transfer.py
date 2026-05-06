import base64

from client.crypto.encrypt import decrypt_message, encrypt_message
from client.crypto.keygen import generate_keypair


def test_file_key_envelope_is_recipient_only() -> None:
    recipient_private, recipient_public = generate_keypair()
    file_key_b64 = base64.b64encode(b"k" * 32).decode("ascii")
    envelope = encrypt_message(file_key_b64, recipient_public)
    assert "ciphertext" in envelope
    assert "ephemeral_pubkey" in envelope
    assert decrypt_message(envelope, recipient_private) == file_key_b64
