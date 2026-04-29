"""HUB session = 包装 ERP JWT 的 cookie 会话。

流程：
1. 用户在 HUB 登录页输 ERP 账号密码
2. HUB 调 ERP /auth/login → 拿 access_token + user
3. HUB 把 (jwt + user) 用 HUB_MASTER_KEY 加密成 cookie
4. 每次 admin 请求带 cookie → HUB 解出 jwt → 调 ERP /auth/me 验证（缓存 5 分钟）
5. ERP JWT 24h 内每次请求自动续期；过期 → cookie 失效 → 401
"""
from __future__ import annotations

import base64
import json
import logging
import time

from hub.adapters.downstream.erp4 import (
    ErpAdapterError,
    ErpPermissionError,
    ErpSystemError,
)
from hub.crypto import DecryptError, decrypt_secret, encrypt_secret

logger = logging.getLogger("hub.auth.erp_session")


class ErpSessionAuth:
    PURPOSE = "session_cookie"

    def __init__(self, erp_adapter, *, cache_ttl: int = 300):
        self.erp = erp_adapter
        self.cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, dict]] = {}  # cookie → (expires_at, user)

    def _encode_cookie(self, payload: dict) -> str:
        """加密 + base64：cookie 不可读不可改。"""
        ct = encrypt_secret(json.dumps(payload, ensure_ascii=False), purpose=self.PURPOSE)
        return base64.urlsafe_b64encode(ct).decode("ascii")

    def _decode_cookie(self, cookie: str) -> dict:
        ct = base64.urlsafe_b64decode(cookie.encode("ascii"))
        plain = decrypt_secret(ct, purpose=self.PURPOSE)
        return json.loads(plain)

    async def login(self, username: str, password: str) -> str:
        """登录成功返回 cookie 字符串；失败抛 ErpPermissionError。"""
        resp = await self.erp.login(username=username, password=password)
        return self._encode_cookie({
            "jwt": resp["access_token"],
            "user": resp.get("user", {}),
        })

    async def verify_cookie(self, cookie: str | None) -> dict | None:
        """校验 cookie 合法性。返回 ERP user dict（含 permissions）或 None。"""
        if not cookie:
            return None

        # 缓存命中
        cached = self._cache.get(cookie)
        if cached and time.time() < cached[0]:
            return cached[1]

        try:
            payload = self._decode_cookie(cookie)
        except (DecryptError, ValueError, json.JSONDecodeError, base64.binascii.Error):
            return None

        jwt = payload.get("jwt")
        if not jwt:
            return None

        try:
            user = await self.erp.get_me(jwt=jwt)
        except ErpPermissionError:
            return None  # JWT 过期/无效
        except (ErpSystemError, ErpAdapterError):
            logger.warning("ERP /auth/me 调用失败，session 暂时不可验证")
            return None

        self._cache[cookie] = (time.time() + self.cache_ttl, user)
        return user

    async def logout(self, cookie: str) -> None:
        """登出：调 ERP /auth/logout 让 JWT 失效 + 清本地缓存。"""
        try:
            payload = self._decode_cookie(cookie)
            jwt = payload.get("jwt")
            if jwt:
                await self.erp.logout(jwt=jwt)
        except Exception:
            # 解码 / 网络失败都要清本地 cache，不阻塞登出
            logger.exception("logout 调用 ERP 失败（仍清本地 cache）")
        self._cache.pop(cookie, None)
