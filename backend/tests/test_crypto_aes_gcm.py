import pytest
import secrets


def test_aes_gcm_round_trip():
    """加密后能解密回原文。"""
    from hub.crypto.aes_gcm import encrypt, decrypt
    key = secrets.token_bytes(32)
    plaintext = "hello 世界 🚀"
    ciphertext = encrypt(key, plaintext)
    assert ciphertext != plaintext.encode()
    assert decrypt(key, ciphertext) == plaintext


def test_aes_gcm_tampered_ciphertext_rejected():
    """密文被篡改时解密抛异常（GCM auth tag 校验）。"""
    from hub.crypto.aes_gcm import encrypt, decrypt, DecryptError
    key = secrets.token_bytes(32)
    ciphertext = encrypt(key, "secret")
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]
    with pytest.raises(DecryptError):
        decrypt(key, tampered)


def test_aes_gcm_wrong_key_rejected():
    """用错 key 解密应失败。"""
    from hub.crypto.aes_gcm import encrypt, decrypt, DecryptError
    key1 = secrets.token_bytes(32)
    key2 = secrets.token_bytes(32)
    ciphertext = encrypt(key1, "secret")
    with pytest.raises(DecryptError):
        decrypt(key2, ciphertext)


def test_aes_gcm_unique_nonce_per_call():
    """每次加密 nonce 都不同（防 nonce 重用）。"""
    from hub.crypto.aes_gcm import encrypt
    key = secrets.token_bytes(32)
    c1 = encrypt(key, "same plaintext")
    c2 = encrypt(key, "same plaintext")
    assert c1 != c2  # nonce 不同 → 密文不同


def test_aes_gcm_key_length_validated():
    """key 必须 32 字节（AES-256）。"""
    from hub.crypto.aes_gcm import encrypt
    with pytest.raises(ValueError):
        encrypt(b"too_short", "x")
    with pytest.raises(ValueError):
        encrypt(b"x" * 16, "x")  # AES-128 也拒绝（统一 256）
