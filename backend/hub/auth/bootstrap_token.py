"""Bootstrap Token：HUB 首次启动一次性 token 防抢跑。

哈希用 sha256 + 32 字节 salt（而非 bcrypt）：
- token 是 secrets.token_urlsafe(32) 生成的高熵随机串（≥256 bit），抗暴破 + 抗字典已经够
- 不需要 bcrypt 的 cost-factor，sha256 + salt 简单、快、无 72 字节限制
- 生产场景：HUB 一次性 token 不存在"反复尝试"的攻击面（一次性 + 30 分钟 TTL + verify_and_consume 原子消费）
"""
from __future__ import annotations
import hashlib
import hmac
import secrets
from datetime import datetime, timezone, timedelta
from hub.config import get_settings
from hub.models import BootstrapToken


def _hash_token(plaintext: str, salt: bytes) -> str:
    """sha256(salt || plaintext) → hex；存储格式 "{salt_hex}:{hash_hex}"。"""
    h = hashlib.sha256()
    h.update(salt)
    h.update(plaintext.encode("utf-8"))
    return f"{salt.hex()}:{h.hexdigest()}"


def _verify_hash(plaintext: str, stored: str) -> bool:
    """常数时间比较，避免 timing attack。"""
    try:
        salt_hex, expected_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    actual = _hash_token(plaintext, salt).split(":", 1)[1]
    return hmac.compare_digest(actual, expected_hex)


async def generate_token(ttl_seconds: int = 1800) -> str:
    """生成（或采用 .env 显式指定）一次性 token。

    Returns: 明文 token（运维一次性使用）
    """
    settings = get_settings()
    plaintext = settings.setup_token or secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    salt = secrets.token_bytes(32)
    token_hash = _hash_token(plaintext, salt)
    await BootstrapToken.create(token_hash=token_hash, expires_at=expires_at)
    return plaintext


async def verify_token(plaintext: str) -> bool:
    """校验 token 合法性。

    Returns:
        True: 合法且未使用未过期
        False: 不存在 / 已使用 / 已过期 / 哈希不匹配
    """
    if not plaintext or len(plaintext) < 8:
        return False
    candidates = await BootstrapToken.filter(
        used_at__isnull=True,
        expires_at__gt=datetime.now(timezone.utc),
    ).order_by("-created_at").limit(20)

    for candidate in candidates:
        if _verify_hash(plaintext, candidate.token_hash):
            return True
    return False


async def mark_used(plaintext: str) -> None:
    """初始化完成后标记 token 已使用（非原子，调用方负责互斥；
    并发场景请用 verify_and_consume_token）。"""
    candidates = await BootstrapToken.filter(used_at__isnull=True)
    for candidate in candidates:
        if _verify_hash(plaintext, candidate.token_hash):
            candidate.used_at = datetime.now(timezone.utc)
            await candidate.save()
            return


async def verify_and_consume_token(plaintext: str) -> bool:
    """**原子**校验 + 消费 token：通过的同时立即标记 used。

    并发场景下两个请求同时拿到同一 token：两边 _verify_hash 都返回 True，但
    `UPDATE ... WHERE used_at IS NULL` 只有先到的那一行影响 1 行，后到的影响 0 行。
    Returns:
        True: 校验通过且本次成功消费（赢家）
        False: 不存在 / 已使用 / 已过期 / 哈希不匹配 / 并发输家
    """
    if not plaintext or len(plaintext) < 8:
        return False
    candidates = await BootstrapToken.filter(
        used_at__isnull=True,
        expires_at__gt=datetime.now(timezone.utc),
    ).order_by("-created_at").limit(20)

    for candidate in candidates:
        if not _verify_hash(plaintext, candidate.token_hash):
            continue
        # 哈希命中 → 用 UPDATE WHERE used_at IS NULL 原子标记
        rows = await BootstrapToken.filter(
            id=candidate.id, used_at__isnull=True,
        ).update(used_at=datetime.now(timezone.utc))
        return rows > 0  # 1 = 我赢了，0 = 并发别人先消费了
    return False
