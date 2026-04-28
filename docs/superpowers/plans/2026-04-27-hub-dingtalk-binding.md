# 钉钉 Stream 接入 + 绑定流程实施计划（Plan 3 / 5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Plan 2 骨架上接入钉钉 Stream 模式，实现端到端钉钉机器人对接：用户发 `/绑定 <ERP 用户名>` → HUB 调 ERP 生成绑定码 → 钉钉里回复码 → 用户登 ERP 个人中心二次确认 → ERP 调 HUB confirm-final → HUB 写入 binding 关系 → 钉钉里 push 绑定成功 + 隐私告知。同时实现解绑、离职/禁用同步、ERP 用户启用状态缓存。

**Architecture:** 实现 `DingTalkStreamAdapter`（ChannelAdapter Protocol 的具体实现）+ `Erp4Adapter`（DownstreamAdapter Protocol，含 ApiKey + Acting-As 头注入）+ `BindingService`（编排绑定/解绑业务流程）+ ERP 反向回调路由（接收 confirm-final / 离职事件 / 禁用同步）。绑定码、解绑、离职、禁用四条生命周期业务都在本 plan 闭环。**不**实现具体业务用例（查商品/历史价 → Plan 4）；**不**实现 Web 后台对话监控等 UI（Plan 5）。

**Tech Stack:** dingtalk-stream（Python 官方 SDK）+ httpx（调 ERP）+ Plan 2 已有的 Tortoise ORM / Redis Streams / hub.crypto / hub.ports。

**前置阅读：**
- [HUB Spec §8 绑定流程](../specs/2026-04-27-hub-middleware-design.md#8-绑定流程)
- [HUB Spec §10 钉钉接入](../specs/2026-04-27-hub-middleware-design.md#10-钉钉接入stream-模式)
- [HUB Spec §11 ERP 接入](../specs/2026-04-27-hub-middleware-design.md#11-erp-接入模型-y--单-apikey--scopes)
- [Plan 1 ERP 集成改动](2026-04-27-erp-integration-changes.md)（必须先完成 Task 1-13）
- [Plan 2 HUB 骨架](2026-04-27-hub-skeleton.md)（必须先完成）

**前置依赖：**
- ✅ Plan 2 完成：HUB 骨架可启动、4 容器跑起来、6 端口接口已定义、Tortoise 模型已建好、加密 secret 已就绪
- ✅ Plan 1 完成：ERP 已部署 v058 迁移、ApiKey 鉴权已开（ENABLE_API_KEY_AUTH=true）、绑定码接口已开（ENABLE_DINGTALK_BINDING=true）、admin 已在 ERP 后台创建一把含 `act_as_user + system_calls` scope 的 ApiKey
- ✅ 钉钉企业内部应用已注册（用户已是钉钉管理员），拿到 AppKey / AppSecret / 机器人 RobotID
- ✅ 钉钉测试组织已加开发组成员

**估时：** 5-6 天

---

## 文件结构

### 新增文件

| 文件 | 职责 |
|---|---|
| `backend/hub/adapters/__init__.py` | adapters 入口 |
| `backend/hub/adapters/channel/__init__.py` | channel adapters 聚合 |
| `backend/hub/adapters/channel/dingtalk_stream.py` | `DingTalkStreamAdapter` 入站（Stream 长连接，仅 gateway） |
| `backend/hub/adapters/channel/dingtalk_sender.py` | `DingTalkSender` 出站（HTTP OpenAPI，无连接，gateway+worker 共用） |
| `backend/hub/adapters/downstream/__init__.py` | downstream adapters |
| `backend/hub/adapters/downstream/erp4.py` | `Erp4Adapter` 实现（含 ApiKey + 强制 Acting-As）|
| `backend/hub/services/__init__.py` | 业务编排服务 |
| `backend/hub/services/binding_service.py` | 绑定/解绑业务流程（事务原子化 + 双向冲突检测 + token_id 防 replay） |
| `backend/hub/services/identity_service.py` | dingtalk_userid → hub_user → ERP 启用状态聚合 |
| `backend/hub/services/erp_active_cache.py` | ERP user.is_active 缓存（TTL 10 分钟） |
| `backend/hub/routers/internal_callbacks.py` | ERP 反向回调入口（confirm-final，409 冲突语义） |
| `backend/hub/handlers/__init__.py` | task handler 入口 |
| `backend/hub/handlers/dingtalk_inbound.py` | 钉钉入站消息 task handler（命令路由 + IdentityService 检查） |
| `backend/hub/handlers/dingtalk_outbound.py` | 钉钉出站消息 task handler（DingTalkSender HTTP push） |
| `backend/hub/cron/__init__.py` | 定时任务 |
| `backend/hub/cron/dingtalk_user_sync.py` | 每日巡检（C 路径） + handle_offboard_event 函数预留（A 路径） |
| `backend/hub/messages.py` | 钉钉回复文案模板（含中文大白话） |
| `backend/hub/models/consumed_token.py` | ConsumedBindingToken 模型（erp_token_id UNIQUE 防 replay） |
| `backend/hub/lifecycle/__init__.py` | 进程生命周期组件（lifespan 内的后台 task 等） |
| `backend/hub/lifecycle/dingtalk_connect.py` | gateway 启动后台 task：等钉钉应用配置就绪后连 Stream（注入 adapter_factory + poll_interval_seconds） |

### 修改文件

| 文件 | 修改 |
|---|---|
| `backend/main.py` | 注册 internal_callbacks 路由；gateway lifespan 调 hub.lifecycle.dingtalk_connect.connect_dingtalk_stream_when_ready；inbound 投递给 worker |
| `backend/worker.py` | 构造 DingTalkSender + IdentityService + ErpActiveCache；注册 dingtalk_inbound + dingtalk_outbound handler；ChannelApp + DownstreamSystem 双就绪前轮询等待 |
| `backend/hub/models/__init__.py` | 追加导出 ConsumedBindingToken |
| `backend/hub/models/identity.py` | DownstreamIdentity unique_together 追加 `("downstream_type", "downstream_user_id")` 兜底并发同 ERP 用户冲突 |
| `backend/hub/config.py` | 加 erp_to_hub_secret Settings 字段 |
| `.env.example` | 加 HUB_ERP_TO_HUB_SECRET（必须等于 ERP 端 ERP_TO_HUB_SECRET） |
| `docker-compose.yml` | hub-gateway environment 注入 HUB_ERP_TO_HUB_SECRET |
| `README.md` | 部署步骤加 secret 生成 + ERP 同步说明 |
| `backend/pyproject.toml` | dependencies 加 `dingtalk-stream` |
| `backend/tests/conftest.py` | TABLES_TO_TRUNCATE 加 `consumed_binding_token` |
| `backend/migrations/` | aerich migrate 产物（Plan 3 加 consumed_binding_token 表 + downstream_identity 新唯一索引） |

### 测试

| 文件 | 数量 | 职责 |
|---|---|---|
| `backend/tests/test_erp4_adapter.py` | 5 | Erp4Adapter（mock httpx） |
| `backend/tests/test_erp_active_cache.py` | 4 | 缓存 TTL + force_refresh |
| `backend/tests/test_identity_service.py` | 5 | dingtalk → HUB → ERP 启用解析 |
| `backend/tests/test_dingtalk_stream_adapter.py` | 3 | _HubChatbotHandler + start register 注入 |
| `backend/tests/test_dingtalk_sender.py` | 3 | access_token 取得/缓存 + send_text |
| `backend/tests/test_binding_service.py` | 11 | 初始化绑定 / unbind / confirm 含原子事务 / token replay / 双向冲突（含 ERP 不消费 token） / 并发同 token / 并发同 erp_user_id 兜底 / revoked 复活 / revoked 复活换 ERP |
| `backend/tests/test_dingtalk_inbound_handler.py` | 6 | 命令路由 + 禁用拦截 + 未绑定提示 |
| `backend/tests/test_dingtalk_outbound_handler.py` | 3 | text / markdown / unknown type |
| `backend/tests/test_internal_callbacks.py` | 4 | confirm-final 接收 + 鉴权 + replay + **409 冲突** |
| `backend/tests/test_dingtalk_user_sync.py` | 2 | 每日巡检 |
| `backend/tests/test_dingtalk_connect.py` | 3 | gateway 自动连接 Stream（ChannelApp 后置写入 / cancel / 失败重试） |
| **合计** | **49** | |

---

## Task 1：Erp4Adapter（DownstreamAdapter 实现）

**Files:**
- Create: `backend/hub/adapters/__init__.py`
- Create: `backend/hub/adapters/downstream/__init__.py`
- Create: `backend/hub/adapters/downstream/erp4.py`
- Test: `backend/tests/test_erp4_adapter.py`

- [ ] **Step 1: 写测试（mock httpx）**

文件 `backend/tests/test_erp4_adapter.py`：
```python
import pytest
import httpx
from httpx import MockTransport, Response


@pytest.fixture
def erp_url():
    return "http://erp.test.local"


@pytest.mark.asyncio
async def test_act_as_user_call_includes_headers(erp_url):
    """业务调用必须带 X-API-Key + X-Acting-As-User-Id 头。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter

    captured_headers = {}

    def handler(request: httpx.Request) -> Response:
        captured_headers.update(request.headers)
        return Response(200, json={"items": []})

    adapter = Erp4Adapter(
        base_url=erp_url, api_key="test-key-xyz",
        transport=MockTransport(handler),
    )
    await adapter.search_products(query="x", acting_as_user_id=42)

    assert captured_headers.get("x-api-key") == "test-key-xyz"
    assert captured_headers.get("x-acting-as-user-id") == "42"


@pytest.mark.asyncio
async def test_act_as_call_without_user_id_raises():
    """ErpAdapter 强制要求 acting_as_user_id；缺失抛 RuntimeError。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter

    adapter = Erp4Adapter(base_url="http://x", api_key="k", transport=MockTransport(lambda r: Response(200)))
    with pytest.raises(RuntimeError, match="acting_as_user_id"):
        await adapter.search_products(query="x", acting_as_user_id=None)


@pytest.mark.asyncio
async def test_system_call_no_acting_as(erp_url):
    """系统级调用（生成绑定码）不带 X-Acting-As-User-Id。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter

    captured_headers = {}
    def handler(request):
        captured_headers.update(request.headers)
        return Response(200, json={"code": "123456", "expires_in": 300})

    adapter = Erp4Adapter(
        base_url=erp_url, api_key="test-key", transport=MockTransport(handler),
    )
    result = await adapter.generate_binding_code(erp_username="zhangsan", dingtalk_userid="m1")
    assert result["code"] == "123456"
    # 系统接口不应该带 Acting-As 头
    assert "x-acting-as-user-id" not in captured_headers


@pytest.mark.asyncio
async def test_health_check_returns_bool(erp_url):
    from hub.adapters.downstream.erp4 import Erp4Adapter
    adapter = Erp4Adapter(
        base_url=erp_url, api_key="k",
        transport=MockTransport(lambda r: Response(200, json={"status": "ok"})),
    )
    assert await adapter.health_check() is True

    adapter_down = Erp4Adapter(
        base_url=erp_url, api_key="k",
        transport=MockTransport(lambda r: Response(503)),
    )
    assert await adapter_down.health_check() is False


@pytest.mark.asyncio
async def test_403_translated_to_permission_error():
    """ERP 返回 403 → 抛 PermissionError 给上游。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter, ErpPermissionError

    adapter = Erp4Adapter(
        base_url="http://x", api_key="k",
        transport=MockTransport(lambda r: Response(403, json={"detail": "no perm"})),
    )
    with pytest.raises(ErpPermissionError):
        await adapter.search_products(query="x", acting_as_user_id=1)
```

- [ ] **Step 2: 实现 Erp4Adapter**

文件 `backend/hub/adapters/__init__.py`：
```python
"""HUB Adapter 实现（ChannelAdapter / DownstreamAdapter / CapabilityProvider 的具体实现）。"""
```

文件 `backend/hub/adapters/downstream/__init__.py`：
```python
from hub.adapters.downstream.erp4 import Erp4Adapter

__all__ = ["Erp4Adapter"]
```

文件 `backend/hub/adapters/downstream/erp4.py`：
```python
"""Erp4Adapter：调 ERP-4 HTTP API 的客户端。

强约束（spec §11）：
- 业务接口（act_as_user scope）调用必须带 X-Acting-As-User-Id；缺失抛 RuntimeError
- 系统接口（system_calls scope）调用不带 X-Acting-As-User-Id
- 错误分类：401/403 → ErpPermissionError；404 → ErpNotFoundError；5xx → ErpSystemError
"""
from __future__ import annotations
import httpx
from typing import Any


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

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------- 系统级接口（system_calls scope） -------------

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
        return await self._act_as_get("/api/v1/products", acting_as_user_id, params={"q": query})

    async def search_customers(self, query: str, *, acting_as_user_id: int | None) -> dict:
        return await self._act_as_get("/api/v1/customers", acting_as_user_id, params={"q": query})

    async def get_product_customer_prices(
        self, product_id: int, customer_id: int, limit: int = 5,
        *, acting_as_user_id: int | None,
    ) -> dict:
        return await self._act_as_get(
            f"/api/v1/products/{product_id}/customer-prices",
            acting_as_user_id,
            params={"customer_id": customer_id, "limit": limit},
        )

    # ------------- 私有 HTTP 方法 -------------

    def _system_headers(self) -> dict:
        return {"X-API-Key": self.api_key}

    def _act_as_headers(self, acting_as: int) -> dict:
        return {"X-API-Key": self.api_key, "X-Acting-As-User-Id": str(acting_as)}

    async def _system_get(self, path: str, params: dict | None = None) -> dict:
        try:
            r = await self._client.get(path, headers=self._system_headers(), params=params)
            self._raise_for_status(r)
            return r.json()
        except httpx.RequestError as e:
            raise ErpSystemError(f"网络错误: {e}")

    async def _system_post(self, path: str, json: dict) -> dict:
        try:
            r = await self._client.post(path, headers=self._system_headers(), json=json)
            self._raise_for_status(r)
            return r.json()
        except httpx.RequestError as e:
            raise ErpSystemError(f"网络错误: {e}")

    async def _act_as_get(
        self, path: str, acting_as_user_id: int | None, params: dict | None = None,
    ) -> dict:
        if acting_as_user_id is None:
            raise RuntimeError(
                "Erp4Adapter 业务调用必须传 acting_as_user_id（spec §11 模型 Y 强制）"
            )
        try:
            r = await self._client.get(
                path, headers=self._act_as_headers(acting_as_user_id), params=params,
            )
            self._raise_for_status(r)
            return r.json()
        except httpx.RequestError as e:
            raise ErpSystemError(f"网络错误: {e}")

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
```

- [ ] **Step 3: 跑测试**

```bash
cd /Users/lin/Desktop/hub/backend
pytest tests/test_erp4_adapter.py -v
```
期望：5 个测试全 PASS。

- [ ] **Step 4: 提交**

```bash
git add backend/hub/adapters/ backend/tests/test_erp4_adapter.py
git commit -m "feat(hub): Erp4Adapter（X-API-Key + X-Acting-As-User-Id 强制 + 错误归类）"
```

---

## Task 2：ERP 启用状态缓存（10 分钟 TTL + 立即同步）

**Files:**
- Create: `backend/hub/services/__init__.py`
- Create: `backend/hub/services/erp_active_cache.py`
- Test: `backend/tests/test_erp_active_cache.py`

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_erp_active_cache.py`：
```python
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_cache_hit_within_ttl():
    """TTL 内不调 ERP，直接返回缓存。"""
    from hub.services.erp_active_cache import ErpActiveCache
    from hub.models import HubUser, ErpUserStateCache

    user = await HubUser.create(display_name="x")
    await ErpUserStateCache.create(
        hub_user=user, erp_active=True,
        checked_at=datetime.now(timezone.utc),
    )

    erp_adapter = AsyncMock()
    cache = ErpActiveCache(erp_adapter=erp_adapter, ttl_seconds=600)

    result = await cache.is_active(hub_user=user, erp_user_id=42)
    assert result is True
    erp_adapter.get_user_active_state.assert_not_called()


@pytest.mark.asyncio
async def test_cache_miss_calls_erp_and_caches():
    """缓存过期 → 调 ERP → 写回缓存。"""
    from hub.services.erp_active_cache import ErpActiveCache
    from hub.models import HubUser, ErpUserStateCache

    user = await HubUser.create(display_name="y")

    erp_adapter = AsyncMock()
    erp_adapter.get_user_active_state = AsyncMock(return_value={"is_active": True, "username": "y"})

    cache = ErpActiveCache(erp_adapter=erp_adapter, ttl_seconds=600)
    result = await cache.is_active(hub_user=user, erp_user_id=42)
    assert result is True
    erp_adapter.get_user_active_state.assert_awaited_once_with(42)

    # 写回了缓存
    cached = await ErpUserStateCache.filter(hub_user_id=user.id).first()
    assert cached is not None
    assert cached.erp_active is True


@pytest.mark.asyncio
async def test_cache_expired_refreshes():
    """缓存超过 TTL → 重新调 ERP。"""
    from hub.services.erp_active_cache import ErpActiveCache
    from hub.models import HubUser, ErpUserStateCache

    user = await HubUser.create(display_name="z")
    old_time = datetime.now(timezone.utc) - timedelta(seconds=700)
    await ErpUserStateCache.create(hub_user=user, erp_active=True, checked_at=old_time)

    erp_adapter = AsyncMock()
    erp_adapter.get_user_active_state = AsyncMock(return_value={"is_active": False, "username": "z"})

    cache = ErpActiveCache(erp_adapter=erp_adapter, ttl_seconds=600)
    result = await cache.is_active(hub_user=user, erp_user_id=42)
    assert result is False
    erp_adapter.get_user_active_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache():
    """force_refresh=True 跳过 TTL，强制调 ERP。"""
    from hub.services.erp_active_cache import ErpActiveCache
    from hub.models import HubUser, ErpUserStateCache

    user = await HubUser.create(display_name="a")
    await ErpUserStateCache.create(
        hub_user=user, erp_active=True, checked_at=datetime.now(timezone.utc),
    )

    erp_adapter = AsyncMock()
    erp_adapter.get_user_active_state = AsyncMock(return_value={"is_active": False})

    cache = ErpActiveCache(erp_adapter=erp_adapter, ttl_seconds=600)
    result = await cache.is_active(hub_user=user, erp_user_id=42, force_refresh=True)
    assert result is False
    erp_adapter.get_user_active_state.assert_awaited_once()
```

- [ ] **Step 2: 实现 erp_active_cache**

文件 `backend/hub/services/__init__.py`：
```python
"""HUB 业务编排服务。"""
```

文件 `backend/hub/services/erp_active_cache.py`：
```python
"""ERP 用户启用状态缓存（spec §8.4 ERP 用户禁用同步）。"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from hub.models import HubUser, ErpUserStateCache


class ErpActiveCache:
    def __init__(self, erp_adapter, *, ttl_seconds: int = 600):
        self.erp = erp_adapter
        self.ttl = ttl_seconds

    async def is_active(
        self, hub_user: HubUser, erp_user_id: int, *, force_refresh: bool = False,
    ) -> bool:
        """查 ERP 用户启用状态：优先缓存，TTL 过期或 force 时调 ERP。"""
        now = datetime.now(timezone.utc)

        if not force_refresh:
            cache = await ErpUserStateCache.filter(hub_user_id=hub_user.id).first()
            if cache and (now - cache.checked_at).total_seconds() < self.ttl:
                return cache.erp_active

        # 缓存过期或 force → 调 ERP
        result = await self.erp.get_user_active_state(erp_user_id)
        is_active = bool(result.get("is_active", False))

        # 写回缓存（upsert）
        cache = await ErpUserStateCache.filter(hub_user_id=hub_user.id).first()
        if cache:
            cache.erp_active = is_active
            cache.checked_at = now
            await cache.save()
        else:
            await ErpUserStateCache.create(
                hub_user=hub_user, erp_active=is_active, checked_at=now,
            )

        return is_active
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
pytest tests/test_erp_active_cache.py -v
git add backend/hub/services/__init__.py backend/hub/services/erp_active_cache.py \
        backend/tests/test_erp_active_cache.py
git commit -m "feat(hub): ERP 启用状态缓存（TTL 10 分钟 + force_refresh）"
```

---

## Task 2.5：IdentityService（钉钉身份 → HUB 身份 → 检查 ERP 启用状态）

**Files:**
- Create: `backend/hub/services/identity_service.py`
- Test: `backend/tests/test_identity_service.py`

把 `dingtalk_userid → ChannelUserBinding(active) → HubUser → DownstreamIdentity(erp) → ErpActiveCache.is_active` 这条链路统一封装。所有非"绑定/解绑"命令在进入业务前必须过此服务，否则禁用用户能继续用机器人。

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_identity_service.py`：
```python
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_resolve_active_user():
    from hub.services.identity_service import IdentityService, IdentityResolution
    from hub.models import HubUser, ChannelUserBinding, DownstreamIdentity

    user = await HubUser.create(display_name="A")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m1", status="active",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=42)

    erp_cache = AsyncMock()
    erp_cache.is_active = AsyncMock(return_value=True)

    svc = IdentityService(erp_active_cache=erp_cache)
    res = await svc.resolve(dingtalk_userid="m1")

    assert res.found is True
    assert res.erp_active is True
    assert res.hub_user_id == user.id
    assert res.erp_user_id == 42


@pytest.mark.asyncio
async def test_resolve_unbound():
    from hub.services.identity_service import IdentityService

    erp_cache = AsyncMock()
    svc = IdentityService(erp_active_cache=erp_cache)
    res = await svc.resolve(dingtalk_userid="never_bound")

    assert res.found is False
    assert res.erp_active is False
    erp_cache.is_active.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_revoked_binding():
    """status=revoked 视同未绑定。"""
    from hub.services.identity_service import IdentityService
    from hub.models import HubUser, ChannelUserBinding

    user = await HubUser.create(display_name="B")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m2", status="revoked",
    )

    svc = IdentityService(erp_active_cache=AsyncMock())
    res = await svc.resolve(dingtalk_userid="m2")
    assert res.found is False


@pytest.mark.asyncio
async def test_resolve_erp_disabled():
    """绑定有效但 ERP 用户被禁用。"""
    from hub.services.identity_service import IdentityService
    from hub.models import HubUser, ChannelUserBinding, DownstreamIdentity

    user = await HubUser.create(display_name="C")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m3", status="active",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=99)

    erp_cache = AsyncMock()
    erp_cache.is_active = AsyncMock(return_value=False)

    svc = IdentityService(erp_active_cache=erp_cache)
    res = await svc.resolve(dingtalk_userid="m3")

    assert res.found is True
    assert res.erp_active is False  # 关键：ERP 已禁用
    assert res.erp_user_id == 99


@pytest.mark.asyncio
async def test_resolve_no_erp_identity():
    """已绑定 HUB 但没关联 ERP 身份（不该出现的边界情况）。"""
    from hub.services.identity_service import IdentityService
    from hub.models import HubUser, ChannelUserBinding

    user = await HubUser.create(display_name="D")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m4", status="active",
    )
    # 不创建 DownstreamIdentity

    svc = IdentityService(erp_active_cache=AsyncMock())
    res = await svc.resolve(dingtalk_userid="m4")
    assert res.found is True
    assert res.erp_user_id is None
    assert res.erp_active is False
```

- [ ] **Step 2: 实现 IdentityService**

文件 `backend/hub/services/identity_service.py`：
```python
"""IdentityService：渠道身份 → HUB 身份 → 检查下游启用状态。

inbound handler 必须在进入业务前调 resolve()，禁用用户不能用机器人。
"""
from __future__ import annotations
from dataclasses import dataclass
from hub.models import ChannelUserBinding, DownstreamIdentity


@dataclass
class IdentityResolution:
    found: bool
    erp_active: bool
    hub_user_id: int | None = None
    erp_user_id: int | None = None
    binding: ChannelUserBinding | None = None


class IdentityService:
    def __init__(self, erp_active_cache):
        self.erp_cache = erp_active_cache

    async def resolve(self, dingtalk_userid: str) -> IdentityResolution:
        """钉钉 userid → HUB 身份 + ERP 启用状态。"""
        binding = await ChannelUserBinding.filter(
            channel_type="dingtalk", channel_userid=dingtalk_userid, status="active",
        ).select_related("hub_user").first()

        if binding is None:
            return IdentityResolution(found=False, erp_active=False)

        di = await DownstreamIdentity.filter(
            hub_user_id=binding.hub_user_id, downstream_type="erp",
        ).first()
        if di is None:
            # 绑定了但没 ERP 身份（异常）；视为已找到 HUB 身份但 ERP 不可用
            return IdentityResolution(
                found=True, erp_active=False,
                hub_user_id=binding.hub_user_id, erp_user_id=None, binding=binding,
            )

        active = await self.erp_cache.is_active(
            hub_user=binding.hub_user, erp_user_id=di.downstream_user_id,
        )
        return IdentityResolution(
            found=True, erp_active=active,
            hub_user_id=binding.hub_user_id, erp_user_id=di.downstream_user_id,
            binding=binding,
        )
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
pytest tests/test_identity_service.py -v
git add backend/hub/services/identity_service.py backend/tests/test_identity_service.py
git commit -m "feat(hub): IdentityService（dingtalk_userid → HUB 身份 + ERP 启用状态检查）"
```

---

## Task 3：DingTalkStreamAdapter（入站）+ DingTalkSender（出站，HTTP-only）

**架构修正：**
- **入站**走 Stream（`DingTalkStreamAdapter`，仅 gateway 进程持有，避免重复连接）
- **出站**走钉钉 OpenAPI HTTP 调用（`DingTalkSender`，无持久连接，gateway 和 worker 都能用，没有连接冲突）

**Files:**
- Create: `backend/hub/adapters/channel/__init__.py`
- Create: `backend/hub/adapters/channel/dingtalk_stream.py`（入站）
- Create: `backend/hub/adapters/channel/dingtalk_sender.py`（出站，HTTP-only）
- Test: `backend/tests/test_dingtalk_stream_adapter.py`
- Test: `backend/tests/test_dingtalk_sender.py`

**SDK 真实 API**（参考 [dingtalk-stream PyPI](https://pypi.org/project/dingtalk-stream/) 官方示例）：
- `Credential(app_key, app_secret)` 构造凭据
- `DingTalkStreamClient(credential)` + `register_callback_handler(topic, handler_inst)`
- 业务逻辑写在 `ChatbotHandler` 子类的 `process(callback)` 方法里
- 回包用 `(AckMessage.STATUS_OK, 'OK')` 元组
- `client.start_forever()`（同步）或异步任务里跑 `start()`（按 SDK 版本而定）

- [ ] **Step 1: 写 DingTalkStreamAdapter 测试（用真实 SDK Handler 形式）**

文件 `backend/tests/test_dingtalk_stream_adapter.py`：
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from hub.ports import InboundMessage


@pytest.mark.asyncio
async def test_chatbot_handler_routes_inbound_to_callback():
    """SDK ChatbotHandler.process() 触发 → adapter 转 InboundMessage → 业务回调。"""
    from hub.adapters.channel.dingtalk_stream import DingTalkStreamAdapter, _HubChatbotHandler

    received: list[InboundMessage] = []
    async def callback(msg: InboundMessage):
        received.append(msg)

    handler = _HubChatbotHandler(callback)

    # 模拟 SDK 传入的 callback 数据（dingtalk-stream 的 callback.data 结构）
    fake_callback = MagicMock()
    fake_callback.data = {
        "senderStaffId": "manager4521",
        "conversationId": "cid-1",
        "text": {"content": "查 SKU100"},
        "createAt": 1700000000000,
    }
    # SDK process 返回 (AckMessage.STATUS_OK, 'OK')
    result = await handler.process(fake_callback)

    assert len(received) == 1
    msg = received[0]
    assert msg.channel_type == "dingtalk"
    assert msg.channel_userid == "manager4521"
    assert msg.conversation_id == "cid-1"
    assert msg.content == "查 SKU100"
    assert msg.timestamp == 1700000000

    # 返回 SDK 期望的 ack 形态
    status, body = result
    assert body == "OK"


@pytest.mark.asyncio
async def test_start_registers_handler_with_sdk():
    """start() 通过 SDK Credential + register_callback_handler 注册 ChatbotHandler。"""
    from hub.adapters.channel.dingtalk_stream import DingTalkStreamAdapter

    with patch("hub.adapters.channel.dingtalk_stream.DingTalkStreamClient") as MockClient, \
         patch("hub.adapters.channel.dingtalk_stream.Credential") as MockCred:
        mock_inst = MockClient.return_value
        mock_inst.start = AsyncMock()
        mock_inst.register_callback_handler = MagicMock()

        adapter = DingTalkStreamAdapter(app_key="k", app_secret="s")
        async def cb(msg): pass
        adapter.on_message(cb)
        await adapter.start()

        MockCred.assert_called_once_with("k", "s")
        mock_inst.register_callback_handler.assert_called_once()
        mock_inst.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_handler_no_callback_warns_but_acks():
    """没注册业务回调时，handler 仍返回正常 ack（避免 SDK 重投）。"""
    from hub.adapters.channel.dingtalk_stream import _HubChatbotHandler
    handler = _HubChatbotHandler(callback=None)
    fake_callback = MagicMock()
    fake_callback.data = {"senderStaffId": "x", "text": {"content": "y"}}
    result = await handler.process(fake_callback)
    assert result[1] == "OK"
```

- [ ] **Step 2: 写 DingTalkSender 测试**

文件 `backend/tests/test_dingtalk_sender.py`：
```python
import pytest
import httpx
from httpx import MockTransport, Response


@pytest.mark.asyncio
async def test_sender_acquires_access_token():
    """access_token 通过 AppKey/AppSecret 调钉钉 OpenAPI 取得。"""
    from hub.adapters.channel.dingtalk_sender import DingTalkSender

    captured = []
    def handler(req: httpx.Request) -> Response:
        captured.append(req.url.path)
        if "gettoken" in str(req.url):
            return Response(200, json={"errcode": 0, "access_token": "tk_xyz", "expires_in": 7200})
        return Response(200, json={"errcode": 0})

    sender = DingTalkSender(
        app_key="k", app_secret="s", robot_code="rc",
        transport=MockTransport(handler),
    )
    token = await sender._get_access_token()
    assert token == "tk_xyz"
    assert any("gettoken" in p for p in captured)


@pytest.mark.asyncio
async def test_send_text_to_user():
    """send_text 调钉钉机器人发消息 OpenAPI。"""
    from hub.adapters.channel.dingtalk_sender import DingTalkSender

    captured_payloads = []
    def handler(req: httpx.Request) -> Response:
        if "gettoken" in str(req.url):
            return Response(200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200})
        captured_payloads.append({"path": req.url.path, "body": req.content})
        return Response(200, json={"processQueryKey": "abc"})

    sender = DingTalkSender(
        app_key="k", app_secret="s", robot_code="rc",
        transport=MockTransport(handler),
    )
    await sender.send_text(dingtalk_userid="u1", text="hi 你好")
    assert any("hi 你好" in str(p["body"]) for p in captured_payloads)


@pytest.mark.asyncio
async def test_send_text_caches_token():
    """同一 sender 多次 send 应只取一次 token。"""
    from hub.adapters.channel.dingtalk_sender import DingTalkSender

    token_calls = 0
    def handler(req: httpx.Request) -> Response:
        nonlocal token_calls
        if "gettoken" in str(req.url):
            token_calls += 1
            return Response(200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200})
        return Response(200, json={})

    sender = DingTalkSender(
        app_key="k", app_secret="s", robot_code="rc",
        transport=MockTransport(handler),
    )
    await sender.send_text("u1", "msg1")
    await sender.send_text("u1", "msg2")
    assert token_calls == 1
```

- [ ] **Step 3: 实现 DingTalkStreamAdapter（仅入站）**

文件 `backend/hub/adapters/channel/__init__.py`：
```python
from hub.adapters.channel.dingtalk_stream import DingTalkStreamAdapter
from hub.adapters.channel.dingtalk_sender import DingTalkSender

__all__ = ["DingTalkStreamAdapter", "DingTalkSender"]
```

文件 `backend/hub/adapters/channel/dingtalk_stream.py`：
```python
"""DingTalkStreamAdapter：钉钉 Stream 入站消息接入。

**仅 gateway 进程持有此 adapter**——Stream 是单一长连接，多进程同时连会导致重复收消息。
出站消息走 DingTalkSender（HTTP OpenAPI），无连接冲突，gateway / worker 都可用。

SDK 真实 API（dingtalk-stream PyPI 官方示例）：
- Credential(app_key, app_secret)
- DingTalkStreamClient(credential).register_callback_handler(topic, handler_inst)
- ChatbotHandler.process(callback) → (AckMessage.STATUS_OK, 'OK')
"""
from __future__ import annotations
import logging
from typing import Awaitable, Callable
from hub.ports import InboundMessage

logger = logging.getLogger("hub.adapter.dingtalk_stream")

try:
    from dingtalk_stream import (
        DingTalkStreamClient, Credential, ChatbotHandler, ChatbotMessage, AckMessage,
    )
except ImportError:
    DingTalkStreamClient = None
    Credential = None
    ChatbotHandler = object  # 测试环境占位
    ChatbotMessage = None
    AckMessage = type("AckMessage", (), {"STATUS_OK": "OK", "STATUS_SYSTEM_EXCEPTION": "EX"})


InboundCallback = Callable[[InboundMessage], Awaitable[None]] | None


class _HubChatbotHandler(ChatbotHandler):
    """SDK ChatbotHandler 子类：把钉钉 callback 转 InboundMessage 后调用业务回调。"""

    def __init__(self, callback: InboundCallback):
        super().__init__() if hasattr(ChatbotHandler, "__init__") else None
        self._callback = callback

    async def process(self, callback):
        try:
            data = callback.data if hasattr(callback, "data") else (callback or {})
            ts_ms = data.get("createAt") or 0
            msg = InboundMessage(
                channel_type="dingtalk",
                channel_userid=str(data.get("senderStaffId") or ""),
                conversation_id=str(data.get("conversationId") or ""),
                content=(data.get("text", {}) or {}).get("content", ""),
                content_type="text",
                timestamp=int(ts_ms // 1000),
                raw_payload=data,
            )
            if self._callback is not None:
                await self._callback(msg)
            else:
                logger.warning("收到钉钉消息但未注册业务回调")
        except Exception:
            logger.exception("钉钉入站消息处理异常")
        # 无论成功失败都 ack（错误已记日志；具体重试由业务/任务队列负责）
        return AckMessage.STATUS_OK, "OK"


class DingTalkStreamAdapter:
    """ChannelAdapter Protocol 实现（仅入站）。"""

    channel_type = "dingtalk"

    def __init__(self, app_key: str, app_secret: str, *, robot_id: str | None = None):
        self.app_key = app_key
        self.app_secret = app_secret
        self.robot_id = robot_id
        self._callback: InboundCallback = None
        self._client = None

    def on_message(self, handler: Callable[[InboundMessage], Awaitable[None]]) -> None:
        self._callback = handler

    async def start(self) -> None:
        if DingTalkStreamClient is None:
            raise RuntimeError("dingtalk_stream SDK 未安装（pip install dingtalk-stream）")
        credential = Credential(self.app_key, self.app_secret)
        self._client = DingTalkStreamClient(credential)
        topic = ChatbotMessage.TOPIC if ChatbotMessage else "/v1.0/im/bot/messages/get"
        self._client.register_callback_handler(topic, _HubChatbotHandler(self._callback))
        logger.info("DingTalkStream 已注册 ChatbotHandler，开始连接钉钉")
        await self._client.start()

    async def stop(self) -> None:
        if self._client and hasattr(self._client, "stop"):
            await self._client.stop()
```

- [ ] **Step 4: 实现 DingTalkSender（HTTP OpenAPI 出站）**

文件 `backend/hub/adapters/channel/dingtalk_sender.py`：
```python
"""DingTalkSender：钉钉机器人**主动 push** 消息（HTTP OpenAPI，不依赖 Stream 连接）。

为什么单独拆出：
- DingTalkStreamAdapter 是 Stream 长连接，gateway 持有；worker 不能重复连
- 主动 push 是无状态 HTTP 调用（OpenAPI），gateway / worker 都能调，无连接冲突
- 流程：调 https://oapi.dingtalk.com/gettoken 取 access_token（缓存 ~2h）→
  调 /v1.0/robot/oToMessages/batchSend 用 robotCode + access_token 发消息

注意：access_token 应在多实例间共享缓存（Redis），本 plan 实现简化为进程内缓存；
Plan 5 在多 worker 部署时升级到 Redis 共享。
"""
from __future__ import annotations
import json
import time
import logging
import httpx

logger = logging.getLogger("hub.adapter.dingtalk_sender")


GET_TOKEN_URL = "https://oapi.dingtalk.com/gettoken"
SEND_OTO_URL = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"


class DingTalkSendError(Exception):
    pass


class DingTalkSender:
    """钉钉机器人主动 push 消息（HTTP OpenAPI）。"""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        robot_code: str,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.robot_code = robot_code
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0

    async def aclose(self):
        await self._client.aclose()

    async def _get_access_token(self) -> str:
        now = time.time()
        if self._cached_token and now < self._token_expires_at - 60:  # 提前 1 分钟刷
            return self._cached_token
        r = await self._client.get(
            GET_TOKEN_URL, params={"appkey": self.app_key, "appsecret": self.app_secret},
        )
        r.raise_for_status()
        body = r.json()
        if body.get("errcode") != 0:
            raise DingTalkSendError(f"gettoken 失败: {body}")
        self._cached_token = body["access_token"]
        self._token_expires_at = now + int(body.get("expires_in", 7200))
        return self._cached_token

    async def send_text(self, dingtalk_userid: str, text: str) -> None:
        await self._send_oto(
            user_ids=[dingtalk_userid],
            msg_key="sampleText",
            msg_param={"content": text},
        )

    async def send_markdown(self, dingtalk_userid: str, title: str, markdown: str) -> None:
        await self._send_oto(
            user_ids=[dingtalk_userid],
            msg_key="sampleMarkdown",
            msg_param={"title": title, "text": markdown},
        )

    async def send_action_card(self, dingtalk_userid: str, actioncard: dict) -> None:
        await self._send_oto(
            user_ids=[dingtalk_userid],
            msg_key="sampleActionCard",
            msg_param=actioncard,
        )

    async def _send_oto(self, user_ids: list[str], msg_key: str, msg_param: dict) -> None:
        token = await self._get_access_token()
        body = {
            "robotCode": self.robot_code,
            "userIds": user_ids,
            "msgKey": msg_key,
            "msgParam": json.dumps(msg_param, ensure_ascii=False),
        }
        r = await self._client.post(
            SEND_OTO_URL,
            headers={"x-acs-dingtalk-access-token": token},
            json=body,
        )
        if r.status_code >= 400:
            raise DingTalkSendError(f"send oto 失败 {r.status_code}: {r.text[:200]}")
```

- [ ] **Step 5: 安装依赖 + 跑测试**

修改 `backend/pyproject.toml` dependencies 加：
```toml
    "dingtalk-stream>=0.18",
```

```bash
cd /Users/lin/Desktop/hub/backend
pip install -e ".[dev]"
pytest tests/test_dingtalk_stream_adapter.py tests/test_dingtalk_sender.py -v
```
期望：6 个测试 PASS（Stream 3 + Sender 3）。

- [ ] **Step 6: 提交**

```bash
git add backend/hub/adapters/channel/ backend/pyproject.toml \
        backend/tests/test_dingtalk_stream_adapter.py \
        backend/tests/test_dingtalk_sender.py
git commit -m "feat(hub): DingTalkStreamAdapter（入站，真实 SDK API）+ DingTalkSender（出站，HTTP OpenAPI）"
```

---

## Task 4：BindingService（绑定/解绑业务编排）

**Files:**
- Create: `backend/hub/services/binding_service.py`
- Create: `backend/hub/messages.py`（钉钉回复文案模板）
- Test: `backend/tests/test_binding_service.py`

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_binding_service.py`：
```python
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_initiate_binding_user_exists_generates_code():
    """ERP 用户存在 → 调 ERP 生成绑定码 → 返回回复文案。"""
    from hub.services.binding_service import BindingService

    erp = AsyncMock()
    erp.user_exists = AsyncMock(return_value=True)
    erp.generate_binding_code = AsyncMock(return_value={"code": "742815", "expires_in": 300})

    svc = BindingService(erp_adapter=erp)
    result = await svc.initiate_binding(dingtalk_userid="m1", erp_username="zhangsan")

    assert result.success is True
    assert "742815" in result.reply_text
    assert "5 分钟" in result.reply_text or "5分钟" in result.reply_text
    erp.generate_binding_code.assert_awaited_once_with(
        erp_username="zhangsan", dingtalk_userid="m1",
    )


@pytest.mark.asyncio
async def test_initiate_binding_user_not_exists():
    from hub.services.binding_service import BindingService

    erp = AsyncMock()
    erp.user_exists = AsyncMock(return_value=False)

    svc = BindingService(erp_adapter=erp)
    result = await svc.initiate_binding(dingtalk_userid="m1", erp_username="nobody")

    assert result.success is False
    assert "未找到" in result.reply_text
    erp.generate_binding_code.assert_not_called()


@pytest.mark.asyncio
async def test_already_bound_returns_friendly_message():
    """已经绑定过 → 提示先解绑。"""
    from hub.services.binding_service import BindingService
    from hub.models import HubUser, ChannelUserBinding, DownstreamIdentity

    user = await HubUser.create(display_name="A")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m1", status="active",
    )

    erp = AsyncMock()
    svc = BindingService(erp_adapter=erp)
    result = await svc.initiate_binding(dingtalk_userid="m1", erp_username="x")

    assert result.success is False
    assert "已经绑定" in result.reply_text
    erp.user_exists.assert_not_called()


@pytest.mark.asyncio
async def test_unbind_self():
    """用户主动解绑自己。"""
    from hub.services.binding_service import BindingService
    from hub.models import HubUser, ChannelUserBinding

    user = await HubUser.create(display_name="B")
    binding = await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m2", status="active",
    )

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.unbind_self(dingtalk_userid="m2")

    assert result.success is True
    assert "已解绑" in result.reply_text
    refreshed = await ChannelUserBinding.get(id=binding.id)
    assert refreshed.status == "revoked"


@pytest.mark.asyncio
async def test_unbind_when_not_bound():
    from hub.services.binding_service import BindingService

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.unbind_self(dingtalk_userid="never_bound")

    assert result.success is False
    assert "未绑定" in result.reply_text or "没有" in result.reply_text


@pytest.mark.asyncio
async def test_confirm_final_writes_binding():
    """ERP 反向通知 confirm-final → 写入 binding + downstream_identity + 默认角色。"""
    from hub.services.binding_service import BindingService
    from hub.models import HubUser, ChannelUserBinding, DownstreamIdentity, HubUserRole, HubRole
    from hub.seed import run_seed

    await run_seed()  # 确保 bot_user_basic 角色存在

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=1,
        dingtalk_userid="m3", erp_user_id=99, erp_username="zhao",
        erp_display_name="赵三",
    )
    assert result.success is True
    assert result.hub_user_id is not None

    binding = await ChannelUserBinding.filter(channel_userid="m3").first()
    assert binding is not None
    assert binding.status == "active"

    di = await DownstreamIdentity.filter(
        hub_user_id=result.hub_user_id, downstream_type="erp",
    ).first()
    assert di.downstream_user_id == 99

    role = await HubRole.get(code="bot_user_basic")
    user_roles = await HubUserRole.filter(hub_user_id=result.hub_user_id, role_id=role.id)
    assert len(user_roles) == 1


@pytest.mark.asyncio
async def test_confirm_final_idempotent_by_token_id():
    """同一 token_id 重复 confirm 应直接返回已处理结果，不重复创建 binding。"""
    from hub.services.binding_service import BindingService
    from hub.models import ChannelUserBinding
    from hub.seed import run_seed
    await run_seed()

    svc = BindingService(erp_adapter=AsyncMock())
    r1 = await svc.confirm_final(
        token_id=42,
        dingtalk_userid="m4", erp_user_id=100, erp_username="qian", erp_display_name="钱",
    )
    r2 = await svc.confirm_final(
        token_id=42,  # 同一 token_id replay
        dingtalk_userid="m4", erp_user_id=100, erp_username="qian", erp_display_name="钱",
    )
    assert r1.success and r2.success
    assert r1.hub_user_id == r2.hub_user_id
    assert r2.note == "already_consumed"
    bindings = await ChannelUserBinding.filter(channel_userid="m4")
    assert len(bindings) == 1


@pytest.mark.asyncio
async def test_confirm_final_conflict_dingtalk_already_bound_to_other_erp():
    """同 dingtalk_userid 已绑定别的 ERP 用户 → 拒绝（必须先解绑）。"""
    from hub.services.binding_service import BindingService
    from hub.models import HubUser, ChannelUserBinding, DownstreamIdentity
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="A")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_conflict", status="active",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=200)

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=50,
        dingtalk_userid="m_conflict",
        erp_user_id=999,  # 不同的 ERP 用户
        erp_username="other", erp_display_name="他",
    )
    assert result.success is False
    assert "已绑定" in result.reply_text or "冲突" in result.reply_text


@pytest.mark.asyncio
async def test_confirm_final_conflict_erp_user_owned_by_other_hub_user():
    """同 erp_user_id 已被另一个 dingtalk 账号占用 → 拒绝。"""
    from hub.services.binding_service import BindingService
    from hub.models import HubUser, ChannelUserBinding, DownstreamIdentity
    from hub.seed import run_seed
    await run_seed()

    other = await HubUser.create(display_name="Other")
    await ChannelUserBinding.create(
        hub_user=other, channel_type="dingtalk", channel_userid="m_other", status="active",
    )
    await DownstreamIdentity.create(hub_user=other, downstream_type="erp", downstream_user_id=300)

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=51,
        dingtalk_userid="m_new",  # 新钉钉账号
        erp_user_id=300,  # 但 ERP 用户已被 m_other 占用
        erp_username="x", erp_display_name="X",
    )
    assert result.success is False
    assert "已被" in result.reply_text or "占用" in result.reply_text


@pytest.mark.asyncio
async def test_confirm_final_concurrent_same_erp_user_only_one_wins():
    """两个并发请求带不同 token_id + 不同 dingtalk_userid + **同 erp_user_id**
    → DownstreamIdentity 唯一约束兜底，只有一个赢家。"""
    import asyncio
    from hub.services.binding_service import BindingService
    from hub.models import DownstreamIdentity, ChannelUserBinding
    from hub.seed import run_seed
    await run_seed()

    svc = BindingService(erp_adapter=AsyncMock())

    async def attempt(token_id: int, dingtalk_userid: str):
        return await svc.confirm_final(
            token_id=token_id,
            dingtalk_userid=dingtalk_userid, erp_user_id=2024,  # 同一 ERP 用户
            erp_username="x", erp_display_name="X",
        )

    r1, r2 = await asyncio.gather(
        attempt(token_id=8001, dingtalk_userid="m_concurrent_a"),
        attempt(token_id=8002, dingtalk_userid="m_concurrent_b"),
    )

    # DownstreamIdentity 唯一约束保证 erp_user_id=2024 只对应一条 row
    dis = await DownstreamIdentity.filter(
        downstream_type="erp", downstream_user_id=2024,
    ).all()
    assert len(dis) == 1, f"期望同 ERP 用户只 1 条 DownstreamIdentity，实际 {len(dis)}"

    # 只有一个 ChannelUserBinding 与该 erp_user_id 关联
    winner_hub_user_id = dis[0].hub_user_id
    bindings = await ChannelUserBinding.filter(
        hub_user_id=winner_hub_user_id, status="active",
    ).all()
    assert len(bindings) == 1


@pytest.mark.asyncio
async def test_confirm_final_revoked_rebind_to_different_erp_updates_di():
    """同 dingtalk + revoked 状态下绑定不同 ERP 用户 → DownstreamIdentity 应更新到新 ERP。"""
    from hub.services.binding_service import BindingService
    from hub.models import HubUser, ChannelUserBinding, DownstreamIdentity
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="R")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_rebind_diff",
        status="revoked", revoked_reason="self_unbind",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=4001)

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=9001,
        dingtalk_userid="m_rebind_diff",
        erp_user_id=4002,  # 不同 ERP 用户
        erp_username="r2", erp_display_name="R2",
    )
    assert result.success is True

    # 旧 DownstreamIdentity 应已更新到 4002
    di = await DownstreamIdentity.filter(
        hub_user_id=user.id, downstream_type="erp",
    ).first()
    assert di.downstream_user_id == 4002, "revoked 复活换 ERP 应更新 di"

    # 不应该保留两条 di 记录
    all_di = await DownstreamIdentity.filter(
        hub_user_id=user.id, downstream_type="erp",
    ).all()
    assert len(all_di) == 1


@pytest.mark.asyncio
async def test_confirm_final_concurrent_same_token_no_dirty_binding():
    """两个并发请求同 token_id，应只有一个赢，且失败方不留绑定副作用。"""
    import asyncio
    from hub.services.binding_service import BindingService
    from hub.models import ChannelUserBinding, HubUser, ConsumedBindingToken
    from hub.seed import run_seed
    await run_seed()

    svc = BindingService(erp_adapter=AsyncMock())

    async def attempt(dingtalk_userid: str, erp_user_id: int):
        return await svc.confirm_final(
            token_id=999,  # 同 token_id
            dingtalk_userid=dingtalk_userid, erp_user_id=erp_user_id,
            erp_username="x", erp_display_name="X",
        )

    # 两个并发请求带不同 dingtalk_userid（恶意 replay）
    r1, r2 = await asyncio.gather(
        attempt("m_concurrent_1", 1001),
        attempt("m_concurrent_2", 1002),
        return_exceptions=False,
    )

    # 只应有一个赢家创建了绑定
    bindings = await ChannelUserBinding.all()
    winner_bindings = [b for b in bindings if b.channel_userid in ("m_concurrent_1", "m_concurrent_2")]
    assert len(winner_bindings) == 1, f"期望 1 个绑定，实际 {len(winner_bindings)}"

    # consumed_binding_token 只应有一条 token_id=999
    consumed = await ConsumedBindingToken.filter(erp_token_id=999).all()
    assert len(consumed) == 1


@pytest.mark.asyncio
async def test_confirm_final_conflict_does_not_consume_token():
    """冲突场景应整体回滚，token 未被消费，用户解绑后可用同一码重试。"""
    from hub.services.binding_service import BindingService
    from hub.models import (
        HubUser, ChannelUserBinding, DownstreamIdentity, ConsumedBindingToken,
    )
    from hub.seed import run_seed
    await run_seed()

    # 预置冲突场景
    user = await HubUser.create(display_name="占位")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_taken", status="active",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=500)

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=777,
        dingtalk_userid="m_taken",  # 已绑到其他 ERP
        erp_user_id=999,
        erp_username="y", erp_display_name="Y",
    )
    assert result.success is False

    # 关键：token 未被消费（事务回滚）
    consumed = await ConsumedBindingToken.filter(erp_token_id=777).first()
    assert consumed is None


@pytest.mark.asyncio
async def test_confirm_final_revoked_binding_can_rebind():
    """先前 revoke 的同 dingtalk_userid + 同 erp_user_id 可重新激活。"""
    from hub.services.binding_service import BindingService
    from hub.models import HubUser, ChannelUserBinding, DownstreamIdentity
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="R")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_rebind",
        status="revoked", revoked_reason="self_unbind",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=400)

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=52,
        dingtalk_userid="m_rebind", erp_user_id=400,
        erp_username="r", erp_display_name="R",
    )
    assert result.success is True
    binding = await ChannelUserBinding.filter(channel_userid="m_rebind").first()
    assert binding.status == "active"
```

- [ ] **Step 2: 实现 messages.py**

文件 `backend/hub/messages.py`：
```python
"""钉钉回复文案模板（中文大白话原则）。"""
from __future__ import annotations


def binding_code_reply(code: str, ttl_minutes: int = 5) -> str:
    return (
        f"绑定码已生成：{code}\n\n"
        f"请在 {ttl_minutes} 分钟内登录 ERP，进入「设置 → 钉钉绑定」"
        f"输入此码完成确认。"
    )


def binding_user_not_found(erp_username: str) -> str:
    return f"未找到 ERP 用户「{erp_username}」，请检查用户名是否正确。"


def binding_already_bound(erp_username: str | None = None) -> str:
    suffix = f"到 ERP 用户「{erp_username}」" if erp_username else ""
    return f"该钉钉账号已经绑定{suffix}。如需换绑请先发送 /解绑。"


def binding_success(erp_display_name: str) -> str:
    return (
        f"绑定成功，欢迎 {erp_display_name}！\n"
        "发送「帮助」查看可用功能。"
    )


def privacy_notice() -> str:
    return (
        "为了功能改进和问题排查，你跟我的对话内容会被记录 30 天后自动删除，"
        "仅授权管理员可查看。如有疑问请联系管理员。"
    )


def unbind_success() -> str:
    return "已解绑。下次发送消息会重新触发绑定流程。"


def unbind_not_bound() -> str:
    return "你还没绑定 ERP 账号。请发送 /绑定 你的ERP用户名 开始绑定。"


def system_error(detail: str | None = None) -> str:
    base = "系统暂时出错了，请稍后重试。"
    return f"{base}（{detail}）" if detail else base


def help_message(available_commands: list[str]) -> str:
    cmds = "\n".join(f"• {c}" for c in available_commands)
    return f"我能帮你做这些：\n\n{cmds}\n\n输入「帮助」可再次查看此信息。"
```

- [ ] **Step 2.5: 新增 ConsumedBindingToken 模型 + 加固 DownstreamIdentity 唯一约束**

(1) 文件 `backend/hub/models/consumed_token.py`：
```python
"""ERP confirm-final 调用 HUB 时携带的 token_id 防 replay 表。"""
from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class ConsumedBindingToken(Model):
    id = fields.IntField(pk=True)
    erp_token_id = fields.IntField(unique=True)  # 唯一约束 = 防 replay 物理保证
    hub_user_id = fields.IntField()
    consumed_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "consumed_binding_token"
```

(2) 修改 `backend/hub/models/__init__.py` 末尾追加：
```python
from hub.models.consumed_token import ConsumedBindingToken
__all__.append("ConsumedBindingToken")
```

(3) **关键加固：修改 Plan 2 的 `backend/hub/models/identity.py` 给 `DownstreamIdentity` 加第二个唯一约束**——保证"同一下游用户不能被两个 HUB 用户同时绑定"在数据库层面成立，避免应用层 read-then-insert 的并发漏洞：

```python
class DownstreamIdentity(Model):
    id = fields.IntField(pk=True)
    hub_user = fields.ForeignKeyField("models.HubUser", related_name="downstream_identities")
    downstream_type = fields.CharField(max_length=30)
    downstream_user_id = fields.IntField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "downstream_identity"
        unique_together = (
            ("hub_user_id", "downstream_type"),         # 一个 HUB 用户对每种下游只有一个身份（Plan 2 已有）
            ("downstream_type", "downstream_user_id"),  # **新增**：一个下游用户只能被一个 HUB 用户绑定
        )
```

(4) 跑 aerich migrate 生成迁移（同时含 ConsumedBindingToken 新表 + DownstreamIdentity 新增唯一索引）：

```bash
cd /Users/lin/Desktop/hub/backend
export HUB_DATABASE_URL="postgres://hub:hub@localhost:5434/hub_dev"  # 用 Plan 2 的迁移 dev 库
docker start hub-pg-aerich-init || (
    docker rm -f hub-pg-aerich-init 2>/dev/null || true
    docker run -d --name hub-pg-aerich-init \
        -e POSTGRES_USER=hub -e POSTGRES_PASSWORD=hub -e POSTGRES_DB=hub_dev \
        -p 5434:5432 postgres:16
    until docker exec hub-pg-aerich-init pg_isready -U hub > /dev/null 2>&1; do sleep 1; done
    aerich upgrade  # 先把 Plan 2 baseline 跑上
)
aerich migrate --name plan3_consumed_token_and_downstream_unique
ls migrations/models/
unset HUB_DATABASE_URL
```
期望：生成新的迁移文件，含 `CREATE TABLE consumed_binding_token` 和 `ALTER TABLE downstream_identity ADD CONSTRAINT ... UNIQUE (downstream_type, downstream_user_id)`。

迁移文件随 commit 一并提交。

- [ ] **Step 3: 实现 BindingService**

文件 `backend/hub/services/binding_service.py`：
```python
"""绑定/解绑业务编排。"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction
from hub.models import (
    HubUser, ChannelUserBinding, DownstreamIdentity, HubRole, HubUserRole,
    ConsumedBindingToken,
)
from hub import messages


@dataclass
class BindingResult:
    success: bool
    reply_text: str
    hub_user_id: int | None = None
    note: str | None = None  # already_consumed / conflict / created / reactivated


class BindingService:
    DEFAULT_ROLE_CODE = "bot_user_basic"

    def __init__(self, erp_adapter):
        self.erp = erp_adapter

    async def initiate_binding(self, dingtalk_userid: str, erp_username: str) -> BindingResult:
        """用户在钉钉发 /绑定 X → 校验 → 调 ERP 生成绑定码 → 返回回复文案。"""
        # 检查是否已绑定
        existing = await ChannelUserBinding.filter(
            channel_type="dingtalk", channel_userid=dingtalk_userid, status="active",
        ).first()
        if existing:
            return BindingResult(
                success=False, reply_text=messages.binding_already_bound(),
            )

        # 校验 ERP 用户存在
        try:
            exists = await self.erp.user_exists(erp_username)
        except Exception as e:
            return BindingResult(success=False, reply_text=messages.system_error(str(e)))

        if not exists:
            return BindingResult(
                success=False, reply_text=messages.binding_user_not_found(erp_username),
            )

        # 调 ERP 生成绑定码
        try:
            result = await self.erp.generate_binding_code(
                erp_username=erp_username, dingtalk_userid=dingtalk_userid,
            )
        except Exception as e:
            return BindingResult(success=False, reply_text=messages.system_error(str(e)))

        return BindingResult(
            success=True,
            reply_text=messages.binding_code_reply(
                code=result["code"], ttl_minutes=result.get("expires_in", 300) // 60,
            ),
        )

    async def unbind_self(self, dingtalk_userid: str) -> BindingResult:
        """用户主动解绑。"""
        binding = await ChannelUserBinding.filter(
            channel_type="dingtalk", channel_userid=dingtalk_userid, status="active",
        ).first()

        if binding is None:
            return BindingResult(success=False, reply_text=messages.unbind_not_bound())

        binding.status = "revoked"
        binding.revoked_at = datetime.now(timezone.utc)
        binding.revoked_reason = "self_unbind"
        await binding.save()

        return BindingResult(success=True, reply_text=messages.unbind_success())

    async def confirm_final(
        self, *, token_id: int, dingtalk_userid: str, erp_user_id: int,
        erp_username: str, erp_display_name: str,
    ) -> BindingResult:
        """ERP 反向通知 confirm-final → 原子地消费 token + 写绑定 + downstream + 默认角色。

        **原子性边界（关键）：**
        所有写操作（token 消费 + binding + downstream_identity + role）必须在单一数据库事务里。
        任何分支失败 → 整体回滚 → 没有"binding 写了但 token 没消费"或"token 消费了但 binding 没建"。

        **冲突 vs already_consumed 区别：**
        - **already_consumed**（同 token_id 二次到达）：return success=True + note；不做任何副作用；
          ERP 端可视为成功（同一码不应被消费两次，HUB 这边返回 success=True 表示"我已经处理过了"）。
        - **conflict**（不同 token_id 但绑定关系冲突）：return success=False；
          **token 不消费**（事务回滚）→ 用户解绑后还能用同一码重试；
          外层 endpoint 把 success=False 翻译成 HTTP 409 让 ERP 也不要 mark used。
        """
        # ---- 1. 已消费 token 快路径检查（read-only，无副作用）----
        existing_consumed = await ConsumedBindingToken.filter(erp_token_id=token_id).first()
        if existing_consumed:
            return BindingResult(
                success=True, reply_text="该绑定请求已处理",
                hub_user_id=existing_consumed.hub_user_id, note="already_consumed",
            )

        # ---- 2. 进入原子事务：token 消费 + 冲突检查 + 副作用 全在此事务 ----
        try:
            async with in_transaction():
                # 2.1 消费 token：UNIQUE(erp_token_id) 保证并发安全
                #     占位 hub_user_id=0，事务末尾再 update 真实值
                try:
                    consumed = await ConsumedBindingToken.create(
                        erp_token_id=token_id, hub_user_id=0,
                    )
                except IntegrityError:
                    # 并发场景：另一个请求已先消费同 token_id
                    raise _AlreadyConsumed()

                # 2.2 冲突检查：dingtalk → 不同 ERP
                existing_binding = await ChannelUserBinding.filter(
                    channel_type="dingtalk", channel_userid=dingtalk_userid,
                ).first()
                if existing_binding and existing_binding.status == "active":
                    existing_di = await DownstreamIdentity.filter(
                        hub_user_id=existing_binding.hub_user_id, downstream_type="erp",
                    ).first()
                    if existing_di and existing_di.downstream_user_id != erp_user_id:
                        raise _Conflict(
                            "该钉钉账号已绑定到其他 ERP 用户。如需换绑请先发送 /解绑。",
                            "conflict_dingtalk_already_bound",
                        )

                # 2.3 冲突检查：ERP → 不同 dingtalk
                other_di = await DownstreamIdentity.filter(
                    downstream_type="erp", downstream_user_id=erp_user_id,
                ).first()
                if other_di:
                    other_active = await ChannelUserBinding.filter(
                        hub_user_id=other_di.hub_user_id,
                        channel_type="dingtalk", status="active",
                    ).first()
                    if other_active and other_active.channel_userid != dingtalk_userid:
                        raise _Conflict(
                            "该 ERP 用户已被另一个钉钉账号占用，请联系管理员解绑后再绑。",
                            "conflict_erp_user_owned",
                        )

                # 2.4 找/建 hub_user + binding
                if existing_binding and existing_binding.status == "active":
                    hub_user = await HubUser.get(id=existing_binding.hub_user_id)
                    note = "already_active"
                elif existing_binding and existing_binding.status == "revoked":
                    hub_user = await HubUser.get(id=existing_binding.hub_user_id)
                    existing_binding.status = "active"
                    existing_binding.bound_at = datetime.now(timezone.utc)
                    existing_binding.revoked_at = None
                    existing_binding.revoked_reason = None
                    await existing_binding.save()
                    note = "reactivated"
                else:
                    hub_user = await HubUser.create(display_name=erp_display_name)
                    await ChannelUserBinding.create(
                        hub_user=hub_user, channel_type="dingtalk",
                        channel_userid=dingtalk_userid,
                        display_meta={
                            "erp_username": erp_username,
                            "erp_display_name": erp_display_name,
                        },
                        status="active",
                    )
                    note = "created"

                # 2.5 写/更新 downstream_identity
                # revoked 复活换 ERP 用户时必须**更新**而非保留旧值，否则 IdentityService 会
                # 解析到旧 ERP user → 数据错位。Step 2.3 的 ERP→不同 dingtalk 冲突检查已经
                # 兜住"新 erp_user_id 已被别人占用"的情况，所以这里 update 是安全的。
                di = await DownstreamIdentity.filter(
                    hub_user_id=hub_user.id, downstream_type="erp",
                ).first()
                if di is None:
                    try:
                        await DownstreamIdentity.create(
                            hub_user=hub_user, downstream_type="erp",
                            downstream_user_id=erp_user_id,
                        )
                    except IntegrityError:
                        # UNIQUE(downstream_type, downstream_user_id) 兜底：
                        # 极端并发场景下另一个事务先 commit 了同 erp_user_id
                        raise _Conflict(
                            "该 ERP 用户已被另一个钉钉账号占用，请联系管理员解绑后再绑。",
                            "conflict_erp_user_owned",
                        )
                elif di.downstream_user_id != erp_user_id:
                    # revoked 复活换不同 ERP → 更新（注意 UNIQUE 约束）
                    di.downstream_user_id = erp_user_id
                    try:
                        await di.save()
                    except IntegrityError:
                        raise _Conflict(
                            "该 ERP 用户已被另一个钉钉账号占用，请联系管理员解绑后再绑。",
                            "conflict_erp_user_owned",
                        )
                    if note == "reactivated":
                        note = "reactivated_with_new_erp"

                # 2.6 默认角色
                role = await HubRole.get(code=self.DEFAULT_ROLE_CODE)
                await HubUserRole.get_or_create(hub_user_id=hub_user.id, role_id=role.id)

                # 2.7 把 consumed token 的 hub_user_id 更新为真实值（同事务内）
                consumed.hub_user_id = hub_user.id
                await consumed.save()

            # 事务正常 commit
            return BindingResult(
                success=True,
                reply_text=messages.binding_success(erp_display_name),
                hub_user_id=hub_user.id,
                note=note,
            )

        except _AlreadyConsumed:
            # 事务已 rollback；按"已消费"返回（外层不重发通知）
            existing = await ConsumedBindingToken.filter(erp_token_id=token_id).first()
            return BindingResult(
                success=True, reply_text="该绑定请求已处理",
                hub_user_id=existing.hub_user_id if existing else None,
                note="already_consumed",
            )
        except _Conflict as e:
            # 事务已 rollback：consumed_binding_token 没有 commit，token 仍可用
            # 用户 /解绑 后可用同一码再 confirm
            return BindingResult(success=False, reply_text=e.reply, note=e.code)


class _AlreadyConsumed(Exception):
    """内部异常：用于 confirm_final 事务内传递"已消费"信号。"""


class _Conflict(Exception):
    """内部异常：用于 confirm_final 事务内传递冲突信号（事务回滚不消费 token）。"""
    def __init__(self, reply: str, code: str):
        self.reply = reply
        self.code = code
```

- [ ] **Step 4: 更新测试 conftest TABLES_TO_TRUNCATE 加新表**

修改 `backend/tests/conftest.py` 中的 `TABLES_TO_TRUNCATE` 列表，**在最前面**追加 `consumed_binding_token`（FK 依赖最浅）：

```python
TABLES_TO_TRUNCATE = [
    "consumed_binding_token",  # Plan 3 新增
    "meta_audit_log", "audit_log", "task_payload", "task_log",
    # ... （Plan 2 原有顺序保持不变）
]
```

否则测试间的 `consumed_binding_token` 残留会导致同 token_id 的测试相互污染。

- [ ] **Step 5: 跑测试 + 提交（含模型 / 迁移 / __init__ / conftest 全部）**

```bash
pytest tests/test_binding_service.py -v
git add backend/hub/messages.py \
        backend/hub/services/binding_service.py \
        backend/hub/models/consumed_token.py \
        backend/hub/models/identity.py \
        backend/hub/models/__init__.py \
        backend/migrations/ \
        backend/tests/conftest.py \
        backend/tests/test_binding_service.py
git commit -m "feat(hub): BindingService 事务原子化 + ConsumedBindingToken + DownstreamIdentity 唯一约束加固 + aerich 迁移"
```

**注意：** 必须把以下 8 项一起提交，漏 commit 是 review 高频陷阱：
- `backend/hub/services/binding_service.py`
- `backend/hub/messages.py`
- `backend/hub/models/consumed_token.py`（新模型）
- `backend/hub/models/identity.py`（**Plan 2 模型加新唯一约束**）
- `backend/hub/models/__init__.py`（追加导出）
- `backend/migrations/`（aerich migrate 产物：新表 + 新唯一索引）
- `backend/tests/conftest.py`（TABLES_TO_TRUNCATE 加新表）
- `backend/tests/test_binding_service.py`

---

## Task 4.5：dingtalk_outbound handler（出站消息 task）

**Files:**
- Create: `backend/hub/handlers/dingtalk_outbound.py`
- Test: `backend/tests/test_dingtalk_outbound_handler.py`

`dingtalk_outbound` 是统一的出站任务类型。任何业务侧（confirm-final / inbound handler / 未来告警等）都通过投递此任务来 push 钉钉，由 worker 用 `DingTalkSender` 实际发送。这样：
- gateway 进程不用直接调钉钉 OpenAPI（保持只接 Stream）
- worker 进程不连 Stream，但能通过 sender 发消息
- 出站重试/失败/死信走 worker 通用逻辑

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_dingtalk_outbound_handler.py`：
```python
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_outbound_text_calls_sender():
    from hub.handlers.dingtalk_outbound import handle_outbound

    sender = AsyncMock()
    payload = {
        "task_id": "t1", "task_type": "dingtalk_outbound",
        "payload": {"channel_userid": "u1", "type": "text", "text": "hi"},
    }
    await handle_outbound(payload, sender=sender)

    sender.send_text.assert_awaited_once_with(dingtalk_userid="u1", text="hi")


@pytest.mark.asyncio
async def test_outbound_markdown_calls_sender():
    from hub.handlers.dingtalk_outbound import handle_outbound

    sender = AsyncMock()
    payload = {
        "task_id": "t2", "task_type": "dingtalk_outbound",
        "payload": {
            "channel_userid": "u1", "type": "markdown",
            "title": "T", "markdown": "# x",
        },
    }
    await handle_outbound(payload, sender=sender)

    sender.send_markdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbound_unknown_type_raises():
    from hub.handlers.dingtalk_outbound import handle_outbound

    sender = AsyncMock()
    payload = {
        "task_id": "t3", "task_type": "dingtalk_outbound",
        "payload": {"channel_userid": "u1", "type": "weird"},
    }
    with pytest.raises(ValueError):
        await handle_outbound(payload, sender=sender)
```

- [ ] **Step 2: 实现 handler**

文件 `backend/hub/handlers/dingtalk_outbound.py`：
```python
"""dingtalk_outbound task handler：用 DingTalkSender 主动 push 消息到钉钉。

业务侧（confirm-final / inbound handler / 告警等）投递此 task；
worker 消费 → 调 DingTalkSender HTTP OpenAPI → 完成 push。
"""
from __future__ import annotations
import logging

logger = logging.getLogger("hub.handler.dingtalk_outbound")


async def handle_outbound(task_data: dict, *, sender) -> None:
    payload = task_data.get("payload", {})
    userid = payload.get("channel_userid")
    msg_type = payload.get("type", "text")

    if not userid:
        logger.error(f"dingtalk_outbound 缺 channel_userid: {payload}")
        return

    if msg_type == "text":
        await sender.send_text(dingtalk_userid=userid, text=payload.get("text", ""))
    elif msg_type == "markdown":
        await sender.send_markdown(
            dingtalk_userid=userid,
            title=payload.get("title", ""),
            markdown=payload.get("markdown", ""),
        )
    elif msg_type == "actioncard":
        await sender.send_action_card(
            dingtalk_userid=userid, actioncard=payload.get("actioncard", {}),
        )
    else:
        raise ValueError(f"未知 outbound type: {msg_type}")
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
pytest tests/test_dingtalk_outbound_handler.py -v
git add backend/hub/handlers/dingtalk_outbound.py \
        backend/tests/test_dingtalk_outbound_handler.py
git commit -m "feat(hub): dingtalk_outbound handler（统一出站任务，DingTalkSender HTTP push）"
```

---

## Task 5：钉钉入站消息 task handler（路由 + 解析 + 编排）

**Files:**
- Create: `backend/hub/handlers/__init__.py`
- Create: `backend/hub/handlers/dingtalk_inbound.py`
- Test: `backend/tests/test_dingtalk_inbound_handler.py`

入站消息 task 的职责：解析"绑定/解绑/帮助"等命令 → 编排 BindingService → push 回复给钉钉。具体业务用例（查商品/历史价等）由 Plan 4 接手。

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_dingtalk_inbound_handler.py`：
```python
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_bind_command_routed_to_initiate():
    from hub.handlers.dingtalk_inbound import handle_inbound

    binding_svc = AsyncMock()
    binding_svc.initiate_binding = AsyncMock(
        return_value=AsyncMock(success=True, reply_text="reply"),
    )
    identity_svc = AsyncMock()  # 绑定命令不需要查身份
    sender = AsyncMock()

    payload = {
        "task_id": "t1", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m1", "content": "/绑定 zhangsan",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=binding_svc, identity_service=identity_svc, sender=sender,
    )

    binding_svc.initiate_binding.assert_awaited_once_with(
        dingtalk_userid="m1", erp_username="zhangsan",
    )
    sender.send_text.assert_awaited_once()
    identity_svc.resolve.assert_not_called()  # 绑定命令不需要 IdentityService


@pytest.mark.asyncio
async def test_unbind_command_routed():
    from hub.handlers.dingtalk_inbound import handle_inbound

    binding_svc = AsyncMock()
    binding_svc.unbind_self = AsyncMock(
        return_value=AsyncMock(success=True, reply_text="已解绑"),
    )
    identity_svc = AsyncMock()
    sender = AsyncMock()

    payload = {
        "task_id": "t2", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m1", "content": "/解绑",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=binding_svc, identity_service=identity_svc, sender=sender,
    )
    binding_svc.unbind_self.assert_awaited_once_with(dingtalk_userid="m1")


@pytest.mark.asyncio
async def test_help_command_returns_help_message():
    from hub.handlers.dingtalk_inbound import handle_inbound

    sender = AsyncMock()
    payload = {
        "task_id": "t3", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m1", "content": "帮助",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=AsyncMock(), identity_service=AsyncMock(), sender=sender,
    )
    sender.send_text.assert_awaited_once()
    sent_text = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "帮助" in sent_text or "帮你做" in sent_text


@pytest.mark.asyncio
async def test_unknown_command_for_unbound_user_triggers_binding_hint():
    """未绑定用户发任意话 → 提示先绑定。"""
    from hub.handlers.dingtalk_inbound import handle_inbound
    from hub.services.identity_service import IdentityResolution

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(found=False, erp_active=False))

    binding_svc = AsyncMock()
    sender = AsyncMock()
    payload = {
        "task_id": "t4", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m_unknown", "content": "查 SKU100",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=binding_svc, identity_service=identity_svc, sender=sender,
    )

    sender.send_text.assert_awaited_once()
    sent_text = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "绑定" in sent_text


@pytest.mark.asyncio
async def test_disabled_erp_user_blocked():
    """已绑定但 ERP 用户被禁用 → 拒绝并提示。"""
    from hub.handlers.dingtalk_inbound import handle_inbound
    from hub.services.identity_service import IdentityResolution

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=False, hub_user_id=1, erp_user_id=99,
    ))
    binding_svc = AsyncMock()
    sender = AsyncMock()
    payload = {
        "task_id": "t5", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m_disabled", "content": "查 SKU100",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=binding_svc, identity_service=identity_svc, sender=sender,
    )

    sender.send_text.assert_awaited_once()
    sent_text = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "停用" in sent_text or "禁用" in sent_text


@pytest.mark.asyncio
async def test_active_user_unrecognized_command():
    """已绑定 + ERP 启用 → 未识别命令提示帮助（业务用例 Plan 4 接手）。"""
    from hub.handlers.dingtalk_inbound import handle_inbound
    from hub.services.identity_service import IdentityResolution

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    sender = AsyncMock()
    payload = {
        "task_id": "t6", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m_active", "content": "随便说点啥",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=AsyncMock(), identity_service=identity_svc, sender=sender,
    )
    sender.send_text.assert_awaited_once()
```

- [ ] **Step 2: 实现 handler**

文件 `backend/hub/handlers/__init__.py`：
```python
"""HUB task handler 实现。"""
```

文件 `backend/hub/handlers/dingtalk_inbound.py`：
```python
"""钉钉入站消息 task handler。

职责（Plan 3 范围）：
- 解析命令：/绑定 <user> / /解绑 / 帮助
- 编排 BindingService
- 非绑定/解绑命令前必须过 IdentityService（识别 + 检查 ERP 启用状态）
- ERP 用户禁用 → 拒绝并提示
- 未绑定 → 提示先绑定

依赖外部注入（避免直连 Stream）：
- binding_service: BindingService
- identity_service: IdentityService
- sender: DingTalkSender（HTTP OpenAPI，不依赖 Stream 连接）

不在 Plan 3 范围（Plan 4）：自然语言意图解析 / 商品查询 / 历史价等具体业务用例。
"""
from __future__ import annotations
import re
import logging
from hub import messages

logger = logging.getLogger("hub.handler.dingtalk_inbound")


RE_BIND = re.compile(r"^/?绑定\s+(\S+)\s*$")
RE_UNBIND = re.compile(r"^/?解绑\s*$")
RE_HELP = re.compile(r"^/?(help|帮助|\?|菜单)\s*$", re.IGNORECASE)


async def handle_inbound(
    task_data: dict, *,
    binding_service,
    identity_service,
    sender,
) -> None:
    """处理一条钉钉入站消息任务。

    task_data 结构：
        {
            task_id, task_type="dingtalk_inbound",
            payload: { channel_userid, content, conversation_id, timestamp }
        }
    """
    payload = task_data.get("payload", {})
    channel_userid = payload.get("channel_userid", "")
    content = (payload.get("content") or "").strip()

    # 1. 命令路由（绑定/解绑命令不需要先解析身份）
    m_bind = RE_BIND.match(content)
    if m_bind:
        erp_username = m_bind.group(1)
        result = await binding_service.initiate_binding(
            dingtalk_userid=channel_userid, erp_username=erp_username,
        )
        await _send_text(sender, channel_userid, result.reply_text)
        return

    if RE_UNBIND.match(content):
        result = await binding_service.unbind_self(dingtalk_userid=channel_userid)
        await _send_text(sender, channel_userid, result.reply_text)
        return

    if RE_HELP.match(content):
        cmds = [
            "/绑定 你的ERP用户名 — 绑定 ERP 账号",
            "/解绑 — 解绑当前账号",
            "查 SKU100 — 查商品（Plan 4 启用）",
            "查 SKU100 给阿里 — 查客户历史价（Plan 4 启用）",
        ]
        await _send_text(sender, channel_userid, messages.help_message(cmds))
        return

    # 2. 非绑定命令必须过 IdentityService（解析身份 + 检查 ERP 启用状态）
    resolution = await identity_service.resolve(dingtalk_userid=channel_userid)

    if not resolution.found:
        await _send_text(
            sender, channel_userid,
            "请先发送「/绑定 你的ERP用户名」完成绑定。\n发送「帮助」查看更多说明。",
        )
        return

    if not resolution.erp_active:
        await _send_text(
            sender, channel_userid,
            "你的 ERP 账号已停用，机器人无法继续为你服务。请联系管理员核实。",
        )
        return

    # 3. 已绑定 + 启用，但本 plan 无业务用例（Plan 4 接手）
    await _send_text(
        sender, channel_userid,
        "我没听懂，请发送「帮助」查看可用功能。\n业务功能（查商品 / 查报价）将在后续上线。",
    )


async def _send_text(sender, userid: str, text: str) -> None:
    try:
        await sender.send_text(dingtalk_userid=userid, text=text)
    except Exception:
        logger.exception(f"send_text 失败 userid={userid}")
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
pytest tests/test_dingtalk_inbound_handler.py -v
git add backend/hub/handlers/ backend/tests/test_dingtalk_inbound_handler.py
git commit -m "feat(hub): 钉钉入站消息 handler（命令路由 + 绑定/解绑/帮助）"
```

---

## Task 6：内部回调路由（ERP confirm-final 反向通知）

**Files:**
- Create: `backend/hub/routers/internal_callbacks.py`
- Modify: `backend/main.py`（注册 router + admin key 鉴权）
- Test: `backend/tests/test_internal_callbacks.py`

ERP 在用户完成"输绑定码 + 二次确认"后，会调 HUB 的 `/internal/binding/confirm-final` 通知 HUB 写入 binding 关系。这个接口必须有鉴权（防止伪造），用 ERP-to-HUB 共享密钥校验。

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_internal_callbacks.py`：
```python
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app_client(setup_db, monkeypatch):
    monkeypatch.setenv("HUB_ERP_TO_HUB_SECRET", "shared-secret-xyz")
    from hub import config
    config._settings = None  # 清缓存
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_confirm_final_writes_binding_and_dispatches_outbound(app_client):
    """confirm-final 成功 → 写 binding + 投递 dingtalk_outbound 任务（含成功通知 + 隐私告知）。"""
    from hub.seed import run_seed
    await run_seed()

    # 注入 fake task runner 验证投递
    from main import app
    submitted_tasks = []

    class FakeRunner:
        async def submit(self, task_type, payload):
            submitted_tasks.append((task_type, payload))
            return "fake-task-id"

    app.state.task_runner = FakeRunner()

    payload = {
        "token_id": 1,
        "erp_user_id": 99, "erp_username": "wang",
        "erp_display_name": "王五", "dingtalk_userid": "m99",
    }
    resp = await app_client.post(
        "/hub/v1/internal/binding/confirm-final",
        json=payload,
        headers={"X-ERP-Secret": "shared-secret-xyz"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    from hub.models import ChannelUserBinding
    binding = await ChannelUserBinding.filter(channel_userid="m99").first()
    assert binding is not None

    # 验证投递了 dingtalk_outbound 任务，包含成功通知 + 隐私告知
    outbound_tasks = [t for t in submitted_tasks if t[0] == "dingtalk_outbound"]
    assert len(outbound_tasks) >= 1
    payloads = [t[1] for t in outbound_tasks]
    all_text = " ".join(str(p) for p in payloads)
    assert "绑定成功" in all_text or "欢迎" in all_text
    assert "30 天" in all_text or "记录" in all_text  # 隐私告知文案


@pytest.mark.asyncio
async def test_confirm_final_conflict_returns_409(app_client):
    """冲突场景应返回 409 让 ERP raise_for_status 抛错，不消费 binding code。"""
    from hub.seed import run_seed
    from hub.models import HubUser, ChannelUserBinding, DownstreamIdentity
    await run_seed()

    # 预置冲突：m_taken 已绑到 erp_user 600
    user = await HubUser.create(display_name="A")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_taken", status="active",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=600)

    payload = {
        "token_id": 888,
        "dingtalk_userid": "m_taken",  # 已被占
        "erp_user_id": 999,  # 想绑到不同 ERP 用户
        "erp_username": "x", "erp_display_name": "X",
    }
    resp = await app_client.post(
        "/hub/v1/internal/binding/confirm-final",
        json=payload,
        headers={"X-ERP-Secret": "shared-secret-xyz"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert "conflict_" in body.get("detail", {}).get("error", "")


@pytest.mark.asyncio
async def test_confirm_final_rejects_wrong_secret(app_client):
    payload = {"token_id": 1, "erp_user_id": 99, "erp_username": "x",
               "erp_display_name": "X", "dingtalk_userid": "m99"}
    resp = await app_client.post(
        "/hub/v1/internal/binding/confirm-final",
        json=payload,
        headers={"X-ERP-Secret": "wrong"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_confirm_final_token_replay_returns_success_but_no_dup_outbound(app_client):
    """同 token_id 重复 confirm 返回 success 但不重复投递 outbound。"""
    from hub.seed import run_seed
    await run_seed()

    from main import app
    submitted_tasks = []

    class FakeRunner:
        async def submit(self, task_type, payload):
            submitted_tasks.append((task_type, payload))
            return "fake-task-id"

    app.state.task_runner = FakeRunner()

    payload = {"token_id": 42, "erp_user_id": 100, "erp_username": "y",
               "erp_display_name": "Y", "dingtalk_userid": "m100"}
    headers = {"X-ERP-Secret": "shared-secret-xyz"}

    r1 = await app_client.post("/hub/v1/internal/binding/confirm-final", json=payload, headers=headers)
    r2 = await app_client.post("/hub/v1/internal/binding/confirm-final", json=payload, headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200

    from hub.models import ChannelUserBinding
    bindings = await ChannelUserBinding.filter(channel_userid="m100")
    assert len(bindings) == 1

    # 第二次 replay 不应该再投递成功通知
    outbound_count = len([t for t in submitted_tasks if t[0] == "dingtalk_outbound"])
    assert outbound_count <= 2  # 第一次绑定成功通知 + 隐私告知（可分两条），不应该是 4
```

- [ ] **Step 2: 实现 router**

**部署配置同步（必做，否则 confirm-final 会 503）：**

(1) 修改 `backend/hub/config.py` Settings 加：
```python
erp_to_hub_secret: str | None = Field(default=None, description="ERP 调 HUB 反向回调的共享密钥")
```

(2) 修改 `/Users/lin/Desktop/hub/.env.example` 在"运行时常量"区块下面追加：
```bash
# === ERP-HUB 双向通信共享密钥（必填）===
# 用于 ERP 调 HUB /hub/v1/internal/binding/confirm-final 的 X-ERP-Secret 头校验。
# 必须与 ERP 端 ERP_TO_HUB_SECRET 同值，否则 confirm-final 会 401/403。
# 生成方式：openssl rand -hex 32
HUB_ERP_TO_HUB_SECRET=
```

(3) 修改 `/Users/lin/Desktop/hub/docker-compose.yml` 的 `hub-gateway` service `environment` 区块追加（注意 worker 不需要，只 gateway 接 confirm-final 路由）：
```yaml
  hub-gateway:
    # ...
    environment:
      HUB_DATABASE_URL: ...
      HUB_REDIS_URL: ...
      HUB_ERP_TO_HUB_SECRET: ${HUB_ERP_TO_HUB_SECRET}  # 从 .env 读
```

(4) 修改 `README.md` 的部署步骤"配置 .env"步骤加一行：
```bash
# 生成 HUB_ERP_TO_HUB_SECRET（必须等于 ERP 端 ERP_TO_HUB_SECRET）
echo "HUB_ERP_TO_HUB_SECRET=$(openssl rand -hex 32)" >> .env
# 把同样的值同步到 ERP 端 .env 的 ERP_TO_HUB_SECRET
```

文件 `backend/hub/routers/internal_callbacks.py`：
```python
"""ERP 反向回调到 HUB 的入口（confirm-final / 钉钉员工事件等）。"""
from __future__ import annotations
import secrets
from fastapi import APIRouter, Header, HTTPException, Body, Request
from pydantic import BaseModel
from hub.config import get_settings
from hub.services.binding_service import BindingService

router = APIRouter(prefix="/hub/v1/internal", tags=["internal_callbacks"])


def _verify_erp_secret(x_erp_secret: str | None) -> None:
    """共享密钥校验（ERP → HUB 用）。"""
    if x_erp_secret is None:
        raise HTTPException(status_code=401, detail="缺少 X-ERP-Secret 头")
    expected = get_settings().erp_to_hub_secret
    if not expected:
        raise HTTPException(status_code=503, detail="HUB_ERP_TO_HUB_SECRET 未配置")
    if not secrets.compare_digest(x_erp_secret, expected):
        raise HTTPException(status_code=403, detail="X-ERP-Secret 不匹配")


class ConfirmFinalRequest(BaseModel):
    token_id: int
    erp_user_id: int
    erp_username: str
    erp_display_name: str
    dingtalk_userid: str


@router.post("/binding/confirm-final")
async def confirm_final(
    request: Request,
    payload: ConfirmFinalRequest = Body(...),
    x_erp_secret: str | None = Header(default=None, alias="X-ERP-Secret"),
):
    """ERP 个人中心二次确认成功后调用。

    HTTP 状态码语义（关键，避免 ERP 误标 binding code 为 used）：
    - 200：成功（首次创建 / 复活 / already_consumed）；ERP 可放心 mark used
    - 409 Conflict：业务冲突（dingtalk 已绑别人 / ERP 用户已被占用）；ERP 不应 mark used，
      让用户解绑后用同一码重试或重新生成
    - 401/403：鉴权失败
    - 5xx：HUB 内部错误
    """
    _verify_erp_secret(x_erp_secret)

    svc = BindingService(erp_adapter=None)
    result = await svc.confirm_final(
        token_id=payload.token_id,
        dingtalk_userid=payload.dingtalk_userid,
        erp_user_id=payload.erp_user_id,
        erp_username=payload.erp_username,
        erp_display_name=payload.erp_display_name,
    )

    # 冲突场景：返回 409 让 ERP raise_for_status 抛错，不消费 binding code
    if not result.success and result.note and result.note.startswith("conflict_"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": result.note,
                "message": result.reply_text,
            },
        )

    # 成功（含 already_consumed / created / reactivated / reactivated_with_new_erp / already_active）→ 200
    # 仅"首次成功创建/激活/换绑 ERP"才投递通知；replay / already_active 不重复发
    if result.success and result.note in ("created", "reactivated", "reactivated_with_new_erp"):
        runner = getattr(request.app.state, "task_runner", None)
        if runner:
            from hub import messages as msgs
            await runner.submit("dingtalk_outbound", {
                "channel_userid": payload.dingtalk_userid,
                "type": "text",
                "text": msgs.binding_success(payload.erp_display_name),
            })
            await runner.submit("dingtalk_outbound", {
                "channel_userid": payload.dingtalk_userid,
                "type": "text",
                "text": msgs.privacy_notice(),
            })

    return {
        "success": result.success,
        "hub_user_id": result.hub_user_id,
        "note": result.note,
    }
```

**给 Plan 1 的同步说明（写进 spec 修复表）：** Plan 1 ERP `confirm_binding` 使用 `r.raise_for_status()` 处理 HUB 响应，HUB 返回 409 时 raise 异常 → ERP 把异常转成 502 抛给前端 → ERP 的 `code_record.used_at` **不会** 被 mark。因此 HUB 用 409 表达冲突就能阻止 ERP 误消费 binding code。Plan 1 这段代码无需修改即可正确工作。

- [ ] **Step 3: 在 main.py 注册 router**

修改 `backend/main.py` 在 setup router 之后追加：
```python
from hub.routers import internal_callbacks
app.include_router(internal_callbacks.router)
```

- [ ] **Step 4: 跑测试 + 提交（含部署配置同步：.env.example / docker-compose.yml / README.md）**

```bash
pytest tests/test_internal_callbacks.py -v
git add backend/hub/routers/internal_callbacks.py \
        backend/hub/config.py \
        backend/main.py \
        backend/tests/test_internal_callbacks.py \
        .env.example \
        docker-compose.yml \
        README.md
git commit -m "feat(hub): /internal/binding/confirm-final 反向回调 + ERP 共享密钥鉴权 + 部署配置同步"
```

**注意：** 必须包含以下 7 项。漏 commit `.env.example` / `docker-compose.yml` / `README.md` 会让 docker compose 部署时 `HUB_ERP_TO_HUB_SECRET` 没注入到容器，confirm-final 直接 503，绑定流程断在这里。

---

## Task 7：每日巡检（C 路径）+ A 路径函数预留

**修正：** A 路径（钉钉 SDK 订阅离职事件）需要的 SDK topic 名、payload 格式、callback 注册方式仍依赖 dingtalk-stream SDK 具体版本，且只有上线后才能真正端到端验证。**Plan 3 仅实现 C 路径每日巡检**（足以保证最终一致性）+ 预留 `handle_offboard_event(dingtalk_userid)` 函数，A 路径的 SDK 订阅集成移到 Plan 5 或上线前评估。

**Files:**
- Create: `backend/hub/cron/__init__.py`
- Create: `backend/hub/cron/dingtalk_user_sync.py`（每日巡检 + handle_offboard_event 函数预留）
- Test: `backend/tests/test_dingtalk_user_sync.py`

- [ ] **Step 1: 写每日巡检测试**

文件 `backend/tests/test_dingtalk_user_sync.py`：
```python
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_daily_audit_revokes_offboarded_users():
    """钉钉返回的现役员工列表中没有的，已绑定的标记 revoked。"""
    from hub.cron.dingtalk_user_sync import daily_employee_audit
    from hub.models import HubUser, ChannelUserBinding

    user1 = await HubUser.create(display_name="A")
    user2 = await HubUser.create(display_name="B")
    await ChannelUserBinding.create(
        hub_user=user1, channel_type="dingtalk", channel_userid="m_active",
        status="active",
    )
    await ChannelUserBinding.create(
        hub_user=user2, channel_type="dingtalk", channel_userid="m_offboarded",
        status="active",
    )

    # 钉钉那边只有 m_active，m_offboarded 已离职
    dingtalk_client = AsyncMock()
    dingtalk_client.fetch_active_userids = AsyncMock(return_value={"m_active"})

    await daily_employee_audit(dingtalk_client)

    b1 = await ChannelUserBinding.filter(channel_userid="m_active").first()
    b2 = await ChannelUserBinding.filter(channel_userid="m_offboarded").first()
    assert b1.status == "active"
    assert b2.status == "revoked"
    assert b2.revoked_reason == "daily_audit"


@pytest.mark.asyncio
async def test_daily_audit_skips_already_revoked():
    from hub.cron.dingtalk_user_sync import daily_employee_audit
    from hub.models import HubUser, ChannelUserBinding

    user = await HubUser.create(display_name="C")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_old",
        status="revoked", revoked_reason="self_unbind",
        revoked_at=datetime.now(timezone.utc),
    )

    dingtalk_client = AsyncMock()
    dingtalk_client.fetch_active_userids = AsyncMock(return_value=set())

    await daily_employee_audit(dingtalk_client)

    # 不修改已 revoked 的（保留原 reason）
    b = await ChannelUserBinding.filter(channel_userid="m_old").first()
    assert b.revoked_reason == "self_unbind"
```

- [ ] **Step 2: 实现 daily_employee_audit**

文件 `backend/hub/cron/__init__.py`：
```python
"""HUB 定时任务。"""
```

文件 `backend/hub/cron/dingtalk_user_sync.py`：
```python
"""钉钉员工同步：C 路径（每日巡检，本 Plan 实现） + A 路径函数预留。

详见 spec §8.3 离职/踢出钉钉自动同步。
A 路径（实时事件订阅）的 SDK topic / callback 集成需要 dingtalk-stream SDK
具体版本验证，移到 Plan 5；handle_offboard_event() 已就绪，未来订阅生效后立即可用。
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from hub.models import ChannelUserBinding

logger = logging.getLogger("hub.cron.dingtalk_user_sync")


async def daily_employee_audit(dingtalk_client) -> dict:
    """每日凌晨调用：拉钉钉企业全员 → 对比 binding → 已离职的标记 revoked。

    Args:
        dingtalk_client: 提供 fetch_active_userids() -> set[str] 的对象

    Returns: 统计字典
    """
    active_userids = await dingtalk_client.fetch_active_userids()
    logger.info(f"钉钉企业现役 userid 数量: {len(active_userids)}")

    bindings = await ChannelUserBinding.filter(
        channel_type="dingtalk", status="active",
    )
    revoked_count = 0
    for b in bindings:
        if b.channel_userid not in active_userids:
            b.status = "revoked"
            b.revoked_at = datetime.now(timezone.utc)
            b.revoked_reason = "daily_audit"
            await b.save()
            revoked_count += 1
            logger.info(f"daily_audit revoke: dingtalk_userid={b.channel_userid}")

    return {
        "active_dingtalk_userids": len(active_userids),
        "active_bindings_before": len(bindings),
        "revoked": revoked_count,
    }


async def handle_offboard_event(dingtalk_userid: str) -> None:
    """钉钉事件订阅 A 路径：实时收到离职事件 → 立即 revoke。"""
    binding = await ChannelUserBinding.filter(
        channel_type="dingtalk", channel_userid=dingtalk_userid, status="active",
    ).first()
    if binding is None:
        return
    binding.status = "revoked"
    binding.revoked_at = datetime.now(timezone.utc)
    binding.revoked_reason = "dingtalk_offboard"
    await binding.save()
    logger.info(f"event-driven revoke: dingtalk_userid={dingtalk_userid}")
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
pytest tests/test_dingtalk_user_sync.py -v
git add backend/hub/cron/ backend/tests/test_dingtalk_user_sync.py
git commit -m "feat(hub): 钉钉员工离职同步（A 事件订阅 + C 每日巡检兜底）"
```

注意：cron 调度器（在 lifespan 里起 asyncio task 每天 03:00 跑 `daily_employee_audit`）的具体集成由 Plan 5 完成（与告警调度器一起）。本 Task 仅实现业务逻辑函数。

---

## Task 8：把 DingTalkStreamAdapter / DingTalkSender 接入 main.py + worker.py

**Files:**
- Modify: `backend/main.py`（gateway lifespan：启动 Stream + 投递 inbound 任务）
- Modify: `backend/worker.py`（构造 DingTalkSender + 注册 inbound / outbound handler）

**关键设计：**
- **Stream 连接只 gateway 持有**：用 `DingTalkStreamAdapter`，收到消息投递 `dingtalk_inbound` task
- **出站走 HTTP OpenAPI**：worker 用 `DingTalkSender`（无连接），消费 `dingtalk_inbound` 后业务回复也通过 sender，消费 `dingtalk_outbound` 直接 push
- **worker 不连 Stream**：杜绝重复收消息和职责混乱

- [ ] **Step 1: 修改 main.py（gateway 启动 stream + 投递；后台轮询等配置）**

**关键：** 初始化向导完成前 ChannelApp 还不存在，gateway 不能"启动时查一次就放弃"——否则向导写入配置后 gateway 还是没连 Stream，用户发 /绑定 永远不会进入 Redis。改为后台 task 轮询：30 秒查一次，配置就绪后建立 Stream 连接（成功后 task 退出）。

修改 `backend/main.py:lifespan` 在 seed 之后追加：
```python
    import asyncio as _asyncio
    from hub.adapters.channel.dingtalk_stream import DingTalkStreamAdapter
    from hub.models import ChannelApp
    from hub.crypto import decrypt_secret
    from hub.queue import RedisStreamsRunner
    from redis.asyncio import Redis as AsyncRedis

    redis_client = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    runner = RedisStreamsRunner(redis_client=redis_client)
    app.state.task_runner = runner
    app.state.dingtalk_adapter = None

    async def _connect_dingtalk_stream_when_ready():
        """后台 task：等钉钉应用配置就绪后连 Stream。一旦连上即退出。

        如果配置在 lifespan 启动时已存在（运维已配好），第一次 loop 就连上。
        如果是首次部署走向导：每 30 秒查一次，向导完成 ChannelApp 写入后下个 loop 就连上。
        """
        while True:
            try:
                channel_app = await ChannelApp.filter(
                    channel_type="dingtalk", status="active",
                ).first()
                if channel_app is None:
                    logger.info("钉钉应用配置尚未就绪，30 秒后重试")
                    await _asyncio.sleep(30)
                    continue

                app_key = decrypt_secret(channel_app.encrypted_app_key, purpose="config_secrets")
                app_secret = decrypt_secret(channel_app.encrypted_app_secret, purpose="config_secrets")
                adapter = DingTalkStreamAdapter(
                    app_key=app_key, app_secret=app_secret, robot_id=channel_app.robot_id,
                )

                async def on_inbound(msg):
                    await runner.submit("dingtalk_inbound", {
                        "channel_type": msg.channel_type,
                        "channel_userid": msg.channel_userid,
                        "conversation_id": msg.conversation_id,
                        "content": msg.content,
                        "timestamp": msg.timestamp,
                    })

                adapter.on_message(on_inbound)
                await adapter.start()
                app.state.dingtalk_adapter = adapter
                logger.info("钉钉 Stream 已连接")
                return  # 连上即退出 task
            except Exception:
                logger.exception("钉钉 Stream 连接失败，30 秒后重试")
                await _asyncio.sleep(30)

    connect_task = _asyncio.create_task(_connect_dingtalk_stream_when_ready())
    app.state.dingtalk_connect_task = connect_task

    yield

    # 关闭顺序：先取消连接 task，再 stop adapter
    if not connect_task.done():
        connect_task.cancel()
    if app.state.dingtalk_adapter is not None:
        await app.state.dingtalk_adapter.stop()
    await redis_client.aclose()
```

**说明：**
- 首次部署：docker compose up → gateway 启动后台 task → admin 走向导写入 ChannelApp → 30 秒内 task 检测到 → 自动连 Stream，**无需重启 gateway**
- 运维已配置场景：lifespan 启动 → task 第一次 loop 立即连上 → 无延迟
- 失败重试：连接异常自动 30 秒重试，长期不可恢复时由运维查日志/重启

- [ ] **Step 2: 修改 worker.py 用 DingTalkSender 代替 Stream（避免重复连接）**

修改 `backend/worker.py`：
```python
import asyncio
import logging
from hub.database import init_db, close_db
from hub.worker_runtime import WorkerRuntime
from hub.config import get_settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hub.worker")


async def main():
    await init_db()
    settings = get_settings()

    from redis.asyncio import Redis
    from hub.adapters.channel.dingtalk_sender import DingTalkSender
    from hub.adapters.downstream.erp4 import Erp4Adapter
    from hub.services.binding_service import BindingService
    from hub.services.identity_service import IdentityService
    from hub.services.erp_active_cache import ErpActiveCache
    from hub.handlers.dingtalk_inbound import handle_inbound
    from hub.handlers.dingtalk_outbound import handle_outbound
    from hub.models import ChannelApp, DownstreamSystem
    from hub.crypto import decrypt_secret

    redis_client = Redis.from_url(settings.redis_url, decode_responses=False)

    # 钉钉 + ERP 配置必须**双双就绪**才能注册 dingtalk_inbound handler——
    # 否则 worker 启动 + 收到入站消息时 binding_service=None，handler 直接 return，
    # WorkerRuntime 会 ACK 掉消息，**用户消息被静默丢弃**。
    channel_app = None
    ds = None
    while channel_app is None or ds is None:
        if channel_app is None:
            channel_app = await ChannelApp.filter(channel_type="dingtalk", status="active").first()
        if ds is None:
            ds = await DownstreamSystem.filter(downstream_type="erp", status="active").first()
        missing = []
        if channel_app is None:
            missing.append("钉钉应用")
        if ds is None:
            missing.append("ERP 下游")
        if missing:
            logger.info(f"等待初始化向导完成 [{', '.join(missing)}] 配置 ...（30 秒后重试）")
            await asyncio.sleep(30)

    dt_app_key = decrypt_secret(channel_app.encrypted_app_key, purpose="config_secrets")
    dt_app_secret = decrypt_secret(channel_app.encrypted_app_secret, purpose="config_secrets")
    sender = DingTalkSender(
        app_key=dt_app_key, app_secret=dt_app_secret,
        robot_code=channel_app.robot_id or "",
    )

    # ERP adapter + 身份解析 + 启用状态缓存（前面已等到 ds 不为 None）
    erp_api_key = decrypt_secret(ds.encrypted_apikey, purpose="config_secrets")
    erp_adapter = Erp4Adapter(base_url=ds.base_url, api_key=erp_api_key)
    erp_active_cache = ErpActiveCache(erp_adapter=erp_adapter, ttl_seconds=600)
    identity_service = IdentityService(erp_active_cache=erp_active_cache)
    binding_service = BindingService(erp_adapter=erp_adapter)

    runtime = WorkerRuntime(redis_client=redis_client)

    async def dingtalk_inbound_handler(task_data):
        # 进 worker.run() 之前已经轮询确认 binding_service 和 identity_service 都就绪。
        # 此处不再有 None 兜底——一旦走到这里就必须真处理；handler 内部异常由
        # WorkerRuntime 转入死信（不静默 ACK）。
        await handle_inbound(
            task_data,
            binding_service=binding_service,
            identity_service=identity_service,
            sender=sender,
        )

    async def dingtalk_outbound_handler(task_data):
        await handle_outbound(task_data, sender=sender)

    runtime.register("dingtalk_inbound", dingtalk_inbound_handler)
    runtime.register("dingtalk_outbound", dingtalk_outbound_handler)

    try:
        await runtime.run()
    finally:
        if erp_adapter:
            await erp_adapter.aclose()
        await sender.aclose()
        await redis_client.aclose()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2.5: 补 gateway 自动连接 Stream 单元测试**

把 lifespan 里的 `_connect_dingtalk_stream_when_ready` 抽成可独立 import 的模块函数（建议放 `backend/hub/lifecycle/dingtalk_connect.py`），便于测试注入 fake adapter 和短轮询间隔。

文件 `backend/hub/lifecycle/__init__.py`：
```python
"""HUB 进程生命周期组件（lifespan 内的后台 task 等）。"""
```

文件 `backend/hub/lifecycle/dingtalk_connect.py`：
```python
"""gateway 启动后台 task：等钉钉应用配置就绪后连 Stream。"""
from __future__ import annotations
import asyncio
import logging
from typing import Callable, Awaitable
from hub.models import ChannelApp
from hub.crypto import decrypt_secret

logger = logging.getLogger("hub.lifecycle.dingtalk_connect")


async def connect_dingtalk_stream_when_ready(
    *,
    on_inbound: Callable[[object], Awaitable[None]],
    adapter_factory: Callable[..., object],
    poll_interval_seconds: float = 30.0,
) -> object | None:
    """轮询 ChannelApp 配置 → 就绪后用 adapter_factory 建 adapter → start。

    Args:
        on_inbound: 入站消息回调（投递任务到 queue 等）
        adapter_factory: 构造 adapter 的工厂（生产用 DingTalkStreamAdapter，
            测试可注入 fake，便于断言 start 被调用）
        poll_interval_seconds: 轮询间隔（生产 30，测试 0.05）

    Returns: 已 start 的 adapter，None 表示被取消
    """
    while True:
        try:
            channel_app = await ChannelApp.filter(
                channel_type="dingtalk", status="active",
            ).first()
            if channel_app is None:
                logger.info("钉钉应用配置尚未就绪，下一轮重试")
                await asyncio.sleep(poll_interval_seconds)
                continue

            app_key = decrypt_secret(channel_app.encrypted_app_key, purpose="config_secrets")
            app_secret = decrypt_secret(channel_app.encrypted_app_secret, purpose="config_secrets")
            adapter = adapter_factory(
                app_key=app_key, app_secret=app_secret, robot_id=channel_app.robot_id,
            )
            adapter.on_message(on_inbound)
            await adapter.start()
            logger.info("钉钉 Stream 已连接")
            return adapter
        except asyncio.CancelledError:
            logger.info("connect task 被取消")
            return None
        except Exception:
            logger.exception("钉钉 Stream 连接失败，下一轮重试")
            await asyncio.sleep(poll_interval_seconds)
```

main.py 的 lifespan 改为调用这个模块函数（保持原本逻辑等价）：
```python
from hub.lifecycle.dingtalk_connect import connect_dingtalk_stream_when_ready
from hub.adapters.channel.dingtalk_stream import DingTalkStreamAdapter

async def on_inbound(msg):
    await runner.submit("dingtalk_inbound", {
        "channel_type": msg.channel_type,
        "channel_userid": msg.channel_userid,
        "conversation_id": msg.conversation_id,
        "content": msg.content,
        "timestamp": msg.timestamp,
    })

async def _bg():
    adapter = await connect_dingtalk_stream_when_ready(
        on_inbound=on_inbound,
        adapter_factory=DingTalkStreamAdapter,
    )
    app.state.dingtalk_adapter = adapter

connect_task = _asyncio.create_task(_bg())
app.state.dingtalk_connect_task = connect_task
```

文件 `backend/tests/test_dingtalk_connect.py`：
```python
import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_connect_waits_then_starts_when_channel_app_appears():
    """初始无 ChannelApp → 启动 task → 写入 ChannelApp → adapter.start() 被调用。"""
    from hub.lifecycle.dingtalk_connect import connect_dingtalk_stream_when_ready
    from hub.models import ChannelApp
    from hub.crypto import encrypt_secret

    started = {"called": False, "args": None}

    class FakeAdapter:
        def __init__(self, *, app_key, app_secret, robot_id):
            self._on = None
            started["args"] = (app_key, app_secret, robot_id)
        def on_message(self, h): self._on = h
        async def start(self): started["called"] = True
        async def stop(self): pass

    async def on_inbound(msg): pass

    # 启动 task（此时还没 ChannelApp）
    task = asyncio.create_task(connect_dingtalk_stream_when_ready(
        on_inbound=on_inbound, adapter_factory=FakeAdapter,
        poll_interval_seconds=0.05,
    ))

    # 短暂等待 → 让 task 经历 1-2 轮空轮询
    await asyncio.sleep(0.15)
    assert started["called"] is False

    # 写入 ChannelApp
    await ChannelApp.create(
        channel_type="dingtalk", name="dt",
        encrypted_app_key=encrypt_secret("fake_key", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("fake_secret", purpose="config_secrets"),
        robot_id="robot_x", status="active",
    )

    # 等下一轮轮询连上
    adapter = await asyncio.wait_for(task, timeout=2.0)
    assert started["called"] is True
    assert started["args"][0] == "fake_key"
    assert adapter is not None


@pytest.mark.asyncio
async def test_connect_returns_none_when_cancelled():
    """task 被 cancel → 返回 None，不抛异常。"""
    from hub.lifecycle.dingtalk_connect import connect_dingtalk_stream_when_ready

    async def on_inbound(msg): pass

    task = asyncio.create_task(connect_dingtalk_stream_when_ready(
        on_inbound=on_inbound,
        adapter_factory=lambda **kw: AsyncMock(),
        poll_interval_seconds=0.5,
    ))
    await asyncio.sleep(0.1)
    task.cancel()
    result = await task
    assert result is None


@pytest.mark.asyncio
async def test_connect_retries_on_adapter_start_failure():
    """adapter.start() 抛错 → 下一轮重试，不让 task 死掉。"""
    from hub.lifecycle.dingtalk_connect import connect_dingtalk_stream_when_ready
    from hub.models import ChannelApp
    from hub.crypto import encrypt_secret

    await ChannelApp.create(
        channel_type="dingtalk", name="dt",
        encrypted_app_key=encrypt_secret("k", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s", purpose="config_secrets"),
        robot_id="r", status="active",
    )

    call_count = {"n": 0}

    class FlakyAdapter:
        def __init__(self, **kw): pass
        def on_message(self, h): pass
        async def start(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first start fails")
        async def stop(self): pass

    async def on_inbound(msg): pass

    task = asyncio.create_task(connect_dingtalk_stream_when_ready(
        on_inbound=on_inbound, adapter_factory=FlakyAdapter,
        poll_interval_seconds=0.05,
    ))
    adapter = await asyncio.wait_for(task, timeout=2.0)
    assert adapter is not None
    assert call_count["n"] == 2  # 第一次失败重试，第二次成功
```

跑测试：
```bash
pytest tests/test_dingtalk_connect.py -v
```
期望：3 个测试 PASS。

- [ ] **Step 3: 端到端验证（需要真实钉钉应用 + ERP staging）**

```bash
cd /Users/lin/Desktop/hub
docker compose up -d
sleep 8

# 1. 完成初始化向导（在 HUB 后台 / 直接 SQL 注入测试 ChannelApp + DownstreamSystem）
# 2. 用钉钉测试组织成员发送 "/绑定 zhangsan" 给机器人
# 3. 钉钉里应收到 6 位绑定码
# 4. 登录 ERP staging，输入码 + 二次确认
# 5. 钉钉里应收到 "绑定成功，欢迎 张三！" + 隐私告知
```


- [ ] **Step 4: 提交**

```bash
git add backend/main.py \
        backend/worker.py \
        backend/hub/lifecycle/__init__.py \
        backend/hub/lifecycle/dingtalk_connect.py \
        backend/tests/test_dingtalk_connect.py
git commit -m "feat(hub): main.py 启动钉钉 Stream（lifecycle.dingtalk_connect 后台轮询）+ worker 注册双 handler + 单测"
```

**注意：** 必须包含以下 5 项。漏 commit `backend/hub/lifecycle/` 会让 main.py import 找不到模块，gateway 启动直接报错 ModuleNotFoundError；漏 test_dingtalk_connect.py 会让 49 测试统计对不上 + 自动连接逻辑无回归保护。

---

## Task 9：自审 + 端到端验证

- [ ] **Step 1: 跑全部测试**

```bash
cd /Users/lin/Desktop/hub/backend
pytest -v
```
期望：Plan 2（约 30+） + Plan 3（**49 条**）全 PASS。
Plan 3 测试明细见顶部"测试"表（11 个测试文件，合计 49 测试）。

- [ ] **Step 2: 端到端验证**

按 Task 8 Step 3 跑通整个绑定流程。如果钉钉测试组织还没准备好，至少跑：
- HUB 启动 4 容器无错误
- pytest 全绿
- 手动 POST `/hub/v1/internal/binding/confirm-final`（带共享密钥）能成功写入 binding

- [ ] **Step 3: 验证记录**

文件 `docs/superpowers/plans/notes/2026-04-27-plan3-end-to-end-verification.md`：
```markdown
# Plan 3 端到端验证记录

日期：____
执行人：____

## 验证项
1. test_erp4_adapter.py：5 PASS
2. test_erp_active_cache.py：4 PASS
3. test_identity_service.py：5 PASS
4. test_dingtalk_stream_adapter.py：3 PASS
5. test_dingtalk_sender.py：3 PASS
6. test_binding_service.py：11 PASS（含原子事务/双向冲突/同 token 并发/同 erp_user_id 并发/复活/复活换 ERP）
7. test_dingtalk_inbound_handler.py：6 PASS（含禁用拦截）
8. test_dingtalk_outbound_handler.py：3 PASS
9. test_internal_callbacks.py：4 PASS（含 409 冲突）
10. test_dingtalk_user_sync.py：2 PASS
11. test_dingtalk_connect.py：3 PASS（自动连接 / cancel / 失败重试）
合计：49 PASS

11. 端到端钉钉绑定全流程（如条件允许）：
   - /绑定 → 收到 6 位码：✅ / ❌
   - ERP 输码 + 二次确认 → 钉钉收"绑定成功" + 隐私告知：✅ / ❌
   - /解绑 → 钉钉收"已解绑"：✅ / ❌
   - ERP 用户禁用 → 机器人拒绝服务（10 分钟内生效）：✅ / ❌
   - 冲突场景（m_other 已绑 erp_user 100，m_new 试绑同 erp_user 100）→ ERP 显示通信失败、绑定码未消费：✅ / ❌
   - 首次部署：docker compose up 未配钉钉 → 走向导写入 ChannelApp → gateway 自动连 Stream（≤30 秒）：✅ / ❌

## 已知缺口（Plan 4-5 处理）
- 自然语言意图解析（IntentParser 实现）：Plan 4
- 商品查询 / 历史价业务用例：Plan 4
- AI fallback：Plan 4
- 完整 Web 后台对话监控：Plan 5
- cron 调度器集成（每日巡检定时跑）：Plan 5
- 钉钉离职事件 SDK 订阅集成（A 路径具体接入 SDK 调用）：Plan 5（实际线上启用前）
```

```bash
git add docs/superpowers/plans/notes/
git commit -m "docs(hub): Plan 3 端到端验证记录"
```

---

## Self-Review（v6，应用第五轮 review 反馈后）

### Spec 覆盖检查

| Spec 章节 | Plan 任务 | ✓ |
|---|---|---|
| §8.1 首次绑定（绑定码双向确认）| Task 4（initiate） + Task 6（confirm-final） | ✓ |
| §8.2 解绑（自助）| Task 4 unbind_self + Task 5 命令路由 | ✓ |
| §8.3 离职/踢出 A+C | Task 7 daily_employee_audit + handle_offboard_event | ✓ |
| §8.4 ERP 用户禁用同步 | Task 2 ErpActiveCache | ✓ |
| §10.1-10.3 钉钉 Stream 接入 | Task 3 + Task 8 | ✓ |
| §11.1-11.5 ERP 接入 + 模型 Y | Task 1 Erp4Adapter（强制 acting_as） | ✓ |
| §13.4 ERP 故障降级 | Task 1 错误归类（PermissionError / SystemError）；具体熔断策略 Plan 4 | 部分 |
| 钉钉回复文案大白话原则 | Task 4 messages.py | ✓ |

### Placeholder Scan

- ✓ 无 "TODO" / "TBD"
- ✓ 所有测试有完整代码
- ✓ DingTalkStreamAdapter 已使用 dingtalk-stream PyPI 官方 API（`Credential` / `register_callback_handler` / `ChatbotHandler.process` / `AckMessage.STATUS_OK`），不再有 SDK 适配占位
- ✓ DingTalkSender 完整实现钉钉机器人 OpenAPI（gettoken / oToMessages/batchSend）

### 类型一致性

- ✓ ChannelAdapter Protocol 字段（channel_type / start / stop / send_message / on_message）与 DingTalkStreamAdapter 实现一致
- ✓ DownstreamAdapter Protocol 与 Erp4Adapter 一致
- ✓ X-API-Key / X-Acting-As-User-Id header 与 Plan 1 ERP 端校验一致
- ✓ X-ERP-Secret header 与 ERP 个人中心 Plan 1 调用方一致（Plan 1 `internal_binding.py` 使用 `os.environ.get('ERP_TO_HUB_SECRET')` 调 HUB；HUB 这边对应配置叫 HUB_ERP_TO_HUB_SECRET，命名建议在 ERP 那边也对齐为 `ERP_TO_HUB_SECRET`，Plan 1 的 .env 与 HUB 端配对）

### 范围检查

Plan 3 完成后达到：
- ✅ 钉钉机器人能收发消息
- ✅ 绑定流程端到端跑通
- ✅ 解绑自助
- ✅ ERP 用户禁用 / 离职同步
- ✅ ERP 启用状态缓存生效
- ❌ 无业务用例（查商品/历史价 → Plan 4）
- ❌ 无 AI 解析（Plan 4）
- ❌ 无完整 Web 后台对话监控（Plan 5）

### 与 Plan 1 / 2 的接口对齐

- HUB → ERP：调用 `/api/v1/internal/binding-codes/generate`（Plan 1 已实现）+ 业务 endpoint 时自动加 `X-API-Key + X-Acting-As-User-Id`（Plan 1 鉴权中间件接收）
- ERP → HUB：调用 `/hub/v1/internal/binding/confirm-final`（本 plan 实现接收方），ERP 需在 .env 配置 `ERP_TO_HUB_SECRET`，HUB 配置 `HUB_ERP_TO_HUB_SECRET`，两边值相同
- HUB ChannelApp / DownstreamSystem 表已在 Plan 2 创建，本 plan 仅消费

### 与 Plan 4 / 5 的预留

- Task 5 `dingtalk_inbound.py` 已留出"未识别命令"分支，Plan 4 只需在此追加 IntentParser 调用
- Task 7 `daily_employee_audit` 函数已就绪，Plan 5 cron 调度器调用即可
- DingTalkStreamAdapter 已包装好 SDK，Plan 5 增加事件订阅（员工离职 / 卡片回调）只需扩展 `_on_*` 回调即可

---

### v2 第一轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-V2-A | confirm-final 没给钉钉 push 绑定成功 + 隐私告知 | 新增 Task 4.5 dingtalk_outbound handler；confirm_final endpoint 在成功（note=created/reactivated）后投递两条 dingtalk_outbound 任务（绑定成功 + 隐私告知）；测试断言投递发生且 token replay 不重复投递 |
| P1-V2-B | ERP 禁用状态缓存未接入消息链路 | 新增 Task 2.5 IdentityService（dingtalk_userid → HUB 身份 + ErpActiveCache 检查）；inbound handler 改为先 resolve → 禁用用户拒绝并提示；新增 disabled / no-erp-identity / unrecognized 三个测试 |
| P1-V2-C | Worker 建立第二条 Stream 连接 | 拆出 DingTalkSender（HTTP OpenAPI，无连接）→ worker 只用 sender 不连 Stream；gateway 持唯一 Stream 连接；inbound handler 接收 sender 而非 channel_adapter |
| P1-V2-D | DingTalkStreamAdapter 用未验证的 SDK API | 完整重写按 dingtalk-stream PyPI 官方示例：Credential / register_callback_handler / ChatbotHandler.process / AckMessage.STATUS_OK；测试改为校验 \_HubChatbotHandler 行为 + 注册逻辑 |
| P1-V2-E | confirm-final 忽略 token_id 幂等 | 新增 ConsumedBindingToken 模型（erp_token_id UNIQUE 防 replay）+ aerich migrate 加迁移；BindingService.confirm_final 加 token_id 参数；冲突检测：dingtalk→不同 ERP / ERP→不同 dingtalk 两类；新增 5 个测试覆盖 replay / 双向冲突 / revoked 复活 |
| P2-V2-F | A 路径事件订阅写入验收但未实际接 SDK | Task 7 标题降级为"每日巡检 + A 路径函数预留"；移除"修改 DingTalkStreamAdapter 订阅离职事件"步骤；handle_offboard_event 函数保留备 Plan 5 SDK 集成时调用；验收标准 A 路径文字降级 |

---

### v3 第二轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-V3-A | ConsumedBindingToken 模型/迁移/conftest 漏 commit | Task 4 拆 Step 4（更新 conftest TABLES_TO_TRUNCATE 加 consumed_binding_token）+ Step 5（commit 包含 7 项：service / messages / consumed_token model / models __init__ / migrations / conftest / 测试），加显眼"漏 commit 是高频陷阱"提示 |
| P1-V3-B | token_id 消费不是原子边界（先 binding 副作用再 token consume） | confirm_final 整体放进 `async with in_transaction()`，token consume 在事务内**最先**插入（占位 hub_user_id=0）→ 冲突检查 → 副作用 → 最后 update token 真实 hub_user_id；并发场景 IntegrityError 抛 _AlreadyConsumed → 回滚；冲突抛 _Conflict → 回滚（**token 不消费**，用户解绑后可用同码重试）；新增并发竞争测试 + 冲突未消费测试 |
| P1-V3-C | confirm-final 冲突返回 200 让 ERP 误标 used | endpoint 检测 `result.note.startswith("conflict_")` → 抛 HTTPException(409)；ERP 端 raise_for_status 自动抛错不会 mark used；新增 `test_confirm_final_conflict_returns_409` |
| P2-V3-D | worker 无 ChannelApp 时退出且 redis_client 不关 | worker.py 改为轮询等待 ChannelApp 就绪（30 秒重试），不退出，避免 docker 反复重启循环 |
| P3-V3-E | 文档摘要/测试统计与 v2 实际不同步 | 顶部"新增文件" / "修改文件" / "测试"表全部重写：加入 dingtalk_sender / identity_service / dingtalk_outbound / consumed_token；测试表加数量列（合计 44 测试）；修改文件加 conftest / migrations / pyproject |

---

### v4 第三轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-V4-A | 同 ERP 用户可被并发绑定到多个钉钉账号（DownstreamIdentity 缺 `(downstream_type, downstream_user_id)` UNIQUE） | 修改 Plan 2 `backend/hub/models/identity.py` 在 `unique_together` 加 `("downstream_type", "downstream_user_id")`；Task 4 aerich migrate 同 commit 含此约束；BindingService 在 di create / save 调用处捕获 `IntegrityError` → 抛 `_Conflict("conflict_erp_user_owned")`；新增并发同 erp_user_id 测试断言只 1 条 di + 1 条 binding |
| P1-V4-B | revoked 复活换不同 ERP 用户时 di 不更新（IdentityService 仍解析旧 ERP） | confirm_final 事务内对 di 三种情况：None → create；存在且 == 新 erp_user_id → 不动；存在且 != → update（捕获 IntegrityError 抛 _Conflict）；note=`reactivated_with_new_erp`；endpoint 投递 outbound 触发条件加该 note；新增 `test_confirm_final_revoked_rebind_to_different_erp_updates_di` |
| P1-V4-C | gateway 启动时无 ChannelApp 后不自动重连 Stream | main.py lifespan 启动后台 task `_connect_dingtalk_stream_when_ready`：30 秒轮询 ChannelApp，配置就绪后建立 Stream（连上即退出 task）；首次部署 docker compose up → 走向导 → 30 秒内自动连接，无需重启 gateway；shutdown 时 cancel task + stop adapter；Task 9 验证记录加该端到端场景 |
| P3-V4-D | 自审 / 验证记录测试口径未同步（Task 9 仍写"约 25"，验证记录"约 29"，Placeholder Scan 仍提 SDK 适配占位） | Task 9 Step 1 改为"44 条"；验证记录 10 个测试文件名 + 数量列出 + 合计 44；新增 ERP 禁用 / 冲突 / gateway 自动重连 三个端到端验收项；Placeholder Scan 改为"已使用官方 SDK API，不再有适配占位" |

---

### v5 第四轮 review 修复清单（清尾巴 P2/P3）

| # | 反馈 | 修复 |
|---|---|---|
| P2-V5-A | HUB_ERP_TO_HUB_SECRET 只加 Settings 没接入部署 | Task 6 Step 2 加部署配置同步小节：`.env.example` 加该字段 + `docker-compose.yml` hub-gateway environment 注入 + `README.md` 加 `openssl rand -hex 32` 生成 + ERP 端同步说明；顶部"修改文件"表加这 3 个文件 |
| P2-V5-B | gateway Stream 自动连接缺单测 | 把 lifespan 内 `_connect_dingtalk_stream_when_ready` 抽到 `hub/lifecycle/dingtalk_connect.py`（模块级函数 + 注入 adapter_factory + poll_interval_seconds）；新增 `tests/test_dingtalk_connect.py` 3 个测试（写入 ChannelApp 触发连接 / cancel 返回 None / start 失败重试）；main.py lifespan 改为调该函数 |
| P2-V5-C | worker 只等 ChannelApp，缺 ERP 时 ACK 掉入站消息 | worker.py 改为同时轮询 ChannelApp + DownstreamSystem 两者就绪才进入 run；handler 内删除 "binding_service is None 跳过" 兜底（一旦走到 handler 必须真处理；异常由 WorkerRuntime 转死信不静默 ACK） |
| P3-V5-D | 测试统计未同步（实际 49） | 顶部测试表加 `test_dingtalk_connect.py` 3 个 + binding_service 9→11；合计 44→49；Task 9 Step 1 期望 49；验证记录 11 项合计 49 |

---

### v6 第五轮 review 修复清单（漏 commit 清尾）

| # | 反馈 | 修复 |
|---|---|---|
| P1-V6-A | Task 8 新增 lifecycle 模块和测试没进 git add | Task 8 Step 4 git add 加 `backend/hub/lifecycle/__init__.py` + `backend/hub/lifecycle/dingtalk_connect.py` + `backend/tests/test_dingtalk_connect.py`；commit msg 同步更新；加红字提示"漏 commit `backend/hub/lifecycle/` 会让 main.py import 找不到模块" |
| P2-V6-B | Task 6 部署配置文件没进 git add | Task 6 Step 4 git add 加 `.env.example` + `docker-compose.yml` + `README.md`；commit msg 加"+部署配置同步"；加红字提示"漏会让 docker compose 部署时 confirm-final 503" |
| P3-V6-C | 顶部文件结构漏列 v4/v5 新改动 | "新增文件"表加 `backend/hub/lifecycle/__init__.py` + `backend/hub/lifecycle/dingtalk_connect.py`；"修改文件"表加 `backend/hub/models/identity.py`（v4 新增 UNIQUE 约束）；同步更新 main.py / worker.py / migrations 行的描述对齐实际改动 |

---

**Plan 3 v6 结束（已修复 v1 + v2 + v3 + v4 + v5 + v6 六轮 review 反馈，共 22 处问题）**
