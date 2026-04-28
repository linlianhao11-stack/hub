"""AES-256-GCM 加密原语。

存储格式：12 字节 nonce + 密文 + 16 字节 GCM 标签（一体存储到 bytea 字段）。
nonce 由系统随机生成，每次加密一个新的 nonce。
"""
from __future__ import annotations
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


NONCE_LENGTH = 12


class DecryptError(Exception):
    """解密失败（密钥错误 / 密文被篡改 / 数据损坏）。"""


def encrypt(key: bytes, plaintext: str | bytes, *, associated_data: bytes | None = None) -> bytes:
    """AES-256-GCM 加密。

    Args:
        key: 32 字节 AES-256 密钥
        plaintext: 待加密内容（str 自动 utf-8 编码）
        associated_data: 可选关联数据（不加密但参与认证）

    Returns:
        nonce(12B) + ciphertext + tag(16B) 的拼接字节串
    """
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("AES-256 key 必须是 32 字节 bytes")
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    nonce = secrets.token_bytes(NONCE_LENGTH)
    aead = AESGCM(key)
    ct = aead.encrypt(nonce, plaintext, associated_data)
    return nonce + ct


def decrypt(key: bytes, ciphertext: bytes, *, associated_data: bytes | None = None) -> str:
    """AES-256-GCM 解密。

    Returns: 原文 str（utf-8 解码后）

    Raises:
        DecryptError: 密钥错误 / 密文被篡改 / 数据格式错
    """
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("AES-256 key 必须是 32 字节 bytes")
    if len(ciphertext) < NONCE_LENGTH + 16:  # nonce + 至少 GCM tag
        raise DecryptError("密文长度不足")
    nonce, body = ciphertext[:NONCE_LENGTH], ciphertext[NONCE_LENGTH:]
    aead = AESGCM(key)
    try:
        plain = aead.decrypt(nonce, body, associated_data)
    except Exception as e:
        raise DecryptError(f"解密失败: {e.__class__.__name__}") from e
    return plain.decode("utf-8")
