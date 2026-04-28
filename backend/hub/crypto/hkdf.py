"""HKDF 派生子密钥。

用途：HUB_MASTER_KEY 是单一根密钥；不同用途（业务 secret 加密 / task_payload 加密 /
bootstrap token 哈希等）应使用各自派生的子密钥，避免密钥多用。
"""
from __future__ import annotations
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


HUB_HKDF_SALT = b"hub-middleware-2026"


def derive_key(master: bytes, purpose: str, length: int = 32) -> bytes:
    """从 master 派生指定用途的子密钥。

    Args:
        master: HUB_MASTER_KEY 字节串（32 字节）
        purpose: 用途字符串，如 "config_secrets" / "task_payload"
        length: 派生密钥长度（字节）

    Returns: length 字节子密钥
    """
    if not isinstance(master, bytes) or len(master) != 32:
        raise ValueError("master 必须是 32 字节 bytes")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=HUB_HKDF_SALT,
        info=purpose.encode("utf-8"),
    )
    return hkdf.derive(master)
