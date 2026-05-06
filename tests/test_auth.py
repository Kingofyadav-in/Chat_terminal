from server.auth import _hash_password, _verify_password


def test_pbkdf2_password_hash_roundtrip() -> None:
    stored = _hash_password("correct horse")
    assert stored.startswith("pbkdf2_sha256$")
    assert _verify_password("correct horse", stored)
    assert not _verify_password("wrong", stored)
