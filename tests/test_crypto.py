from client.crypto.audio_crypto import decrypt_packet, encrypt_packet
from client.crypto.encrypt import decrypt_message, encrypt_message
from client.crypto.file_crypto import decrypt_chunk, encrypt_chunk
from client.crypto.keygen import generate_keypair


def test_message_file_and_audio_crypto_roundtrip() -> None:
    recipient_private, recipient_public = generate_keypair()
    payload = encrypt_message("hello", recipient_public)
    assert decrypt_message(payload, recipient_private) == "hello"

    file_key = b"f" * 32
    ciphertext, nonce, tag = encrypt_chunk(b"chunk", file_key)
    assert decrypt_chunk(ciphertext, nonce, tag, file_key) == b"chunk"

    audio_key = b"a" * 32
    audio_nonce, audio_ciphertext = encrypt_packet(b"opus", audio_key, 1)
    assert decrypt_packet(audio_ciphertext, audio_nonce, audio_key) == b"opus"
