"""Erp4Adapter：调 ERP-4 HTTP API 的客户端。

强约束（spec §11）：
- 业务接口（act_as_user scope）调用必须带 X-Acting-As-User-Id；缺失抛 RuntimeError
- 系统接口（system_calls scope）调用不带 X-Acting-As-User-Id
- 错误分类：401/403 → ErpPermissionError；404 → ErpNotFoundError；5xx → ErpSystemError

ERP /products + /customers 接口的搜索参数名是 `keyword`（不是 `q`），见
docs/integration/2026-04-27-fuzzy-search-audit.md（ERP 仓库）。

熔断器（Plan 4）：5/30s 失败 → 60s 开 → half-open；只统计 ErpSystemError，
4xx 业务错不计入避免污染熔断。历史价查询 3s 超时（spec §13.4）。
"""
from __future__ import annotations

import httpx

from hub.circuit_breaker import CircuitBreaker


class ErpAdapterError(Exception):
    """ERP adapter 通用错误。"""


class ErpPermissionError(ErpAdapterError):
    """401/403：用户无权限或 ApiKey 无效。"""


class ErpNotFoundError(ErpAdapterError):
    """404：资源不存在。"""


class ErpSystemError(ErpAdapterError):
    """5xx / 网络错误。"""


class Erp4Adapter:
    """DownstreamAdapter Protocol 的具体实现。"""

    downstream_type = "erp"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._timeout = timeout
        # transport 注入便于测试 mock
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, transport=transport,
        )
        # 熔断器：只统计 ErpSystemError，4xx 业务错不计入
        self._breaker = CircuitBreaker(
            threshold=5, window_seconds=30, open_seconds=60,
            countable_exceptions=(ErpSystemError,),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------- 系统级接口（system_calls scope） -------------

    async def login(self, username: str, password: str) -> dict:
        """调 ERP /auth/login（不需要 ApiKey，因为还没用户态）。"""
        try:
            r = await self._client.post(
                "/api/v1/auth/login", json={"username": username, "password": password},
            )
            if r.status_code == 401:
                raise ErpPermissionError("用户名或密码错误")
            self._raise_for_status(r)
            return r.json()
        except httpx.RequestError as e:
            raise ErpSystemError(f"网络错误: {e}") from e

    async def get_me(self, jwt: str) -> dict:
        """调 ERP /auth/me 用 JWT 拿当前用户信息。"""
        try:
            r = await self._client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            self._raise_for_status(r)
            return r.json()
        except httpx.RequestError as e:
            raise ErpSystemError(f"网络错误: {e}") from e

    async def logout(self, jwt: str) -> None:
        """调 ERP /auth/logout 让 JWT 失效（ERP 端递增 token_version）。

        HUB logout 必须调它——否则用户登出后旧 cookie 内的 JWT 仍有效，
        任何拿到旧 cookie 的请求仍能 /auth/me 通过。
        """
        try:
            r = await self._client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            # 401/204 都视为成功（JWT 已失效）
            if r.status_code not in (200, 204, 401):
                self._raise_for_status(r)
        except httpx.RequestError:
            # 网络失败不阻塞 logout 流程（HUB 仍清本地 cache）
            pass

    async def health_check(self) -> bool:
        """健康检查：调一个不需要鉴权的 ERP endpoint。"""
        try:
            r = await self._client.get("/api/v1/meta/health", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    async def generate_binding_code(self, erp_username: str, dingtalk_userid: str) -> dict:
        """系统接口：让 ERP 生成绑定码。"""
        return await self._system_post(
            "/api/v1/internal/binding-codes/generate",
            {"erp_username": erp_username, "dingtalk_userid": dingtalk_userid},
        )

    async def user_exists(self, username: str) -> bool:
        body = await self._system_post("/api/v1/internal/users/exists", {"username": username})
        return body.get("exists", False)

    async def get_user_active_state(self, user_id: int) -> dict:
        return await self._system_get(f"/api/v1/internal/users/{user_id}/active-state")

    # ------------- 业务接口（act_as_user scope） -------------

    async def search_products(self, query: str, *, acting_as_user_id: int | None) -> dict:
        return await self._act_as_get(
            "/api/v1/products", acting_as_user_id, params={"keyword": query},
        )

    async def search_customers(self, query: str, *, acting_as_user_id: int | None) -> dict:
        return await self._act_as_get(
            "/api/v1/customers", acting_as_user_id, params={"keyword": query},
        )

    async def get_product(self, product_id: int, *, acting_as_user_id: int | None) -> dict:
        """精确反查（不依赖 keyword 模糊搜索；PricingStrategy fallback 用）。"""
        return await self._act_as_get(
            f"/api/v1/products/{product_id}", acting_as_user_id,
        )

    async def get_product_customer_prices(
        self, product_id: int, customer_id: int, limit: int = 5,
        *, acting_as_user_id: int | None,
    ) -> dict:
        """历史价查询：3s 超时（spec §13.4），超时抛 ErpSystemError 由上游降级处理。"""
        if acting_as_user_id is None:
            raise RuntimeError("acting_as_user_id 必填")

        async def _do():
            try:
                r = await self._client.get(
                    f"/api/v1/products/{product_id}/customer-prices",
                    headers=self._act_as_headers(acting_as_user_id),
                    params={"customer_id": customer_id, "limit": limit},
                    timeout=3.0,
                )
                self._raise_for_status(r)
                return r.json()
            except httpx.TimeoutException as e:
                raise ErpSystemError("历史价查询超时（3s）") from e
            except httpx.RequestError as e:
                raise ErpSystemError(f"网络错误: {e}") from e

        return await self._breaker.call(_do)

    # ------------- 私有 HTTP 方法 -------------

    def _system_headers(self) -> dict:
        return {"X-API-Key": self.api_key}

    def _act_as_headers(self, acting_as: int) -> dict:
        return {"X-API-Key": self.api_key, "X-Acting-As-User-Id": str(acting_as)}

    async def _system_get(self, path: str, params: dict | None = None) -> dict:
        async def _do():
            try:
                r = await self._client.get(path, headers=self._system_headers(), params=params)
                self._raise_for_status(r)
                return r.json()
            except httpx.RequestError as e:
                raise ErpSystemError(f"网络错误: {e}") from e
        return await self._breaker.call(_do)

    async def _system_post(self, path: str, json: dict) -> dict:
        async def _do():
            try:
                r = await self._client.post(path, headers=self._system_headers(), json=json)
                self._raise_for_status(r)
                return r.json()
            except httpx.RequestError as e:
                raise ErpSystemError(f"网络错误: {e}") from e
        return await self._breaker.call(_do)

    async def _act_as_get(
        self, path: str, acting_as_user_id: int | None, params: dict | None = None,
    ) -> dict:
        if acting_as_user_id is None:
            raise RuntimeError(
                "Erp4Adapter 业务调用必须传 acting_as_user_id（spec §11 模型 Y 强制）",
            )

        async def _do():
            try:
                r = await self._client.get(
                    path, headers=self._act_as_headers(acting_as_user_id), params=params,
                )
                self._raise_for_status(r)
                return r.json()
            except httpx.RequestError as e:
                raise ErpSystemError(f"网络错误: {e}") from e
        return await self._breaker.call(_do)

    def _raise_for_status(self, r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise ErpPermissionError(f"{r.status_code}: {self._safe_detail(r)}")
        if r.status_code == 404:
            raise ErpNotFoundError(self._safe_detail(r))
        if r.status_code >= 500:
            raise ErpSystemError(f"{r.status_code}: {self._safe_detail(r)}")
        if r.status_code >= 400:
            raise ErpAdapterError(f"{r.status_code}: {self._safe_detail(r)}")

    @staticmethod
    def _safe_detail(r: httpx.Response) -> str:
        try:
            return r.json().get("detail", r.text[:200])
        except Exception:
            return r.text[:200]
