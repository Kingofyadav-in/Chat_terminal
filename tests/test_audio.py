from client.audio.jitter_buffer import JitterBuffer


def test_jitter_buffer_reorders_packets() -> None:
    jitter = JitterBuffer(size=4)
    jitter.push(2, b"c")
    jitter.push(0, b"a")
    jitter.push(1, b"b")
    assert jitter.pop() == b"a"
    assert jitter.pop() == b"b"
    assert jitter.pop() == b"c"
