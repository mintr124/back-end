from app.utils.text_normalizer import normalize_text
from app.utils.checksum import sha256_bytes


def test_normalize_text():
    assert normalize_text("a   b\r\n\r\nc") == "a b\n\nc"


def test_checksum():
    assert sha256_bytes(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
