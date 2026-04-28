"""加密入口：encrypt_secret / decrypt_secret 高阶 API。

使用方式：
    >>> from hub.crypto import encrypt_secret, decrypt_secret
    >>> ct = encrypt_secret("钉钉 AppSecret xxx", purpose="config_secrets")
    >>> decrypt_secret(ct, purpose="config_secrets")
    '钉钉 AppSecret xxx'
"""
from __future__ import annotations

from functools import lru_cache

from hub.config import get_settings
from hub.crypto.aes_gcm import DecryptError, decrypt, encrypt
from hub.crypto.hkdf import derive_key


@lru_cache(maxsize=8)
def _purpose_key(purpose: str) -> bytes:
    """缓存每个 purpose 的派生密钥（启动后只派生一次）。"""
    master = get_settings().master_key_bytes
    return derive_key(master, purpose=purpose)


def encrypt_secret(plaintext: str | bytes, *, purpose: str) -> bytes:
    """业务 secret 加密入库。"""
    return encrypt(_purpose_key(purpose), plaintext)


def decrypt_secret(ciphertext: bytes, *, purpose: str) -> str:
    """业务 secret 取出解密。"""
    return decrypt(_purpose_key(purpose), ciphertext)


__all__ = ["encrypt_secret", "decrypt_secret", "DecryptError"]
