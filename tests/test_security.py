from hub.security import generate_token, hash_password, hash_token, verify_password


def test_tokens_are_random_and_hashed_deterministically():
    a, b = generate_token(), generate_token()
    assert a != b and len(a) >= 40
    assert hash_token(a) == hash_token(a)
    assert hash_token(a) != hash_token(b)
    assert len(hash_token(a)) == 64


def test_password_hash_roundtrip():
    stored = hash_password("secret-pass")
    assert stored.startswith("scrypt$")
    assert verify_password("secret-pass", stored)
    assert not verify_password("wrong", stored)


def test_verify_password_rejects_malformed_hash():
    assert not verify_password("x", "")
    assert not verify_password("x", "scrypt$broken")
    assert not verify_password("x", "md5$a$b")
