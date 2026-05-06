from shared.protocol import MsgType, make_packet, parse_packet


def test_protocol_roundtrip() -> None:
    raw = make_packet(MsgType.MESSAGE, to="bob", payload={"text": "hi"})
    packet = parse_packet(raw)
    assert packet["version"] == "1.0"
    assert packet["type"] == MsgType.MESSAGE
    assert packet["to"] == "bob"
