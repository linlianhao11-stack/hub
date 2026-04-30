"""Plan 6 Task 7：合同/Excel 文件加密存储。

第一版：用 AES-GCM 加密后，把密文字节返给调用方写入 DB bytea 列；
后续可以换 S3 / OSS，只需换 put/get 实现。

注意：hub.crypto.aes_gcm.decrypt 内部做 utf-8 decode（为文本 secret 设计）；
二进制文件（docx/xlsx）不能走 str 路径，所以这里直接调底层 AESGCM 原语，
跳过 decode 步骤，保留原始 bytes。
"""
from __future__ import annotations

import logging
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hub.crypto.aes_gcm import DecryptError, NONCE_LENGTH
from hub.crypto.hkdf import derive_key
from hub.config import get_settings

logger = logging.getLogger("hub.agent.document.storage")

_PURPOSE = "document_files"


def _get_key() -> bytes:
    """派生 document_files 专用 32-byte AES-256 密钥。"""
    master = get_settings().master_key_bytes
    return derive_key(master, purpose=_PURPOSE)


def _encrypt_bytes(plaintext: bytes) -> bytes:
    """AES-GCM 加密，明文/密文均为 bytes（不做 str encode/decode）。"""
    key = _get_key()
    nonce = secrets.token_bytes(NONCE_LENGTH)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ct


def _decrypt_bytes(ciphertext: bytes) -> bytes:
    """AES-GCM 解密，返回原始 bytes。"""
    key = _get_key()
    if len(ciphertext) < NONCE_LENGTH + 16:
        raise DecryptError("密文长度不足")
    nonce, body = ciphertext[:NONCE_LENGTH], ciphertext[NONCE_LENGTH:]
    try:
        return AESGCM(key).decrypt(nonce, body, None)
    except Exception as e:
        raise DecryptError(f"解密失败: {e.__class__.__name__}") from e


class DocumentStorage:
    """加密文件存储抽象。"""

    async def put(self, content: bytes, *, encrypted: bool = True) -> bytes:
        """加密后返存储用 bytes（调用方写 contract_draft 等字段）。

        Args:
            content: 原始文件字节
            encrypted: True 时用 AES-GCM；False 时直接返（debug/test 用）

        Returns:
            可写入 bytea 列的加密 bytes
        """
        if not encrypted:
            return content
        return _encrypt_bytes(content)

    async def get(self, stored: bytes, *, encrypted: bool = True) -> bytes:
        """从存储取出，可选解密，返回原始 bytes。"""
        if not encrypted:
            return stored
        return _decrypt_bytes(stored)
