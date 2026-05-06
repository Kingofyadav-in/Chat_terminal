import time

from client.audio.jitter_buffer import JitterBuffer
from client.crypto.audio_crypto import decrypt_packet, encrypt_packet
from client.crypto.file_crypto import chunk_checksum, decrypt_chunk, encrypt_chunk
from server.rate_limiter import RateLimiter
from shared.protocol import MsgType, make_packet, parse_packet


def _assert_under(seconds: float, fn) -> None:
    started = time.perf_counter()
    fn()
    elapsed = time.perf_counter() - started
    assert elapsed < seconds


def test_protocol_packet_roundtrip_perf() -> None:
    def run() -> None:
        for i in range(5000):
            raw = make_packet(MsgType.MESSAGE, to="bob", payload={"text": str(i)})
            pkt = parse_packet(raw)
            assert pkt["type"] == MsgType.MESSAGE

    _assert_under(1.0, run)


def test_rate_limiter_perf() -> None:
    limiter = RateLimiter(rate=100000, per=60.0)

    def run() -> None:
        for i in range(50000):
            assert limiter.allow(f"user-{i % 100}")

    _assert_under(1.0, run)


def test_file_chunk_crypto_perf() -> None:
    key = b"k" * 32
    chunk = b"x" * (512 * 1024)

    def run() -> None:
        for _ in range(20):
            ciphertext, nonce, tag = encrypt_chunk(chunk, key)
            assert chunk_checksum(decrypt_chunk(ciphertext, nonce, tag, key))

    _assert_under(2.0, run)


def test_audio_crypto_and_jitter_perf() -> None:
    key = b"a" * 32
    frames = []

    def run() -> None:
        for seq in range(1000):
            nonce, ciphertext = encrypt_packet(b"frame", key, seq)
            frames.append((seq, decrypt_packet(ciphertext, nonce, key)))

        jitter = JitterBuffer(size=16)
        for seq, frame in reversed(frames):
            jitter.push(seq, frame)
        assert jitter.pop() is not None

    _assert_under(1.0, run)
