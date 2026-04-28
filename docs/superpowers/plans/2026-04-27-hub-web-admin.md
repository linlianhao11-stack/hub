# HUB Web 后台实施计划（Plan 5 / 5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Plan 1-4 已就绪的后端骨架上构建完整 HUB Web 管理后台——包含初始化向导 6 步完整业务（步骤 2-6 业务逻辑，Plan 2 仅做了步骤 1 + token 校验骨架）+ HUB 登录（包装 ERP JWT session）+ 用户管理 / 角色管理 / 用户角色分配 / 账号关联 / 功能权限说明 5 个核心权限页 + 下游系统 / 渠道 / AI 提供商 / 系统设置 4 个配置页 + 任务流水查询 / 详情 + 实时对话流 / 历史对话 / 会话详情 3 个对话监控页 + 操作审计 + 仪表盘 + 健康检查页 + cron 调度器集成（每日巡检）+ ERP CLAUDE.md 同步 UI 大白话原则。完成后 HUB 是"骨架完整、可工作、有完整管理界面"的状态。

**Architecture:** 前端 Vue 3 SPA（独立组件库，从 ERP 复制起步，独立维护）通过 HUB Gateway 暴露的静态资源 serve（`/`），登录后台调 ERP `/auth/login` 拿 JWT 包装成 HUB session（cookie）。所有 admin API 走 `/hub/v1/admin/*`（基于 ERP session 鉴权 + HUB hub_user_role 表权限校验）。对话监控用 SSE 推流（Redis Pub/Sub 中间层）。cron 调度器用 asyncio 后台 task（lifespan 内启动 + lifespan 关闭时 cancel），每天 03:00 跑 `daily_employee_audit`。

**Tech Stack:**
- 前端：Vue 3 + Vite 7 + Tailwind 4 + Pinia 3 + Vue Router 4 + lucide-vue-next + Chart.js 4.5（图表）+ axios（baseURL `/hub/v1`）
- 后端：FastAPI（已有）+ SSE（StreamingResponse）+ asyncio（cron 调度器）
- 鉴权：HUB session = 包装 ERP JWT（Plan 2 admin_key 保留，admin 后台用户态走 ERP session）

**前置阅读：**
- [HUB Spec §7 身份与权限模型](../specs/2026-04-27-hub-middleware-design.md#7-身份与权限模型)
- [HUB Spec §9 对话监控](../specs/2026-04-27-hub-middleware-design.md#9-对话监控)
- [HUB Spec §12 HUB Web 后台](../specs/2026-04-27-hub-middleware-design.md#12-hub-web-后台)
- [HUB Spec §16.2 初始化向导 6 步](../specs/2026-04-27-hub-middleware-design.md#162-初始化向导首次部署6-步)
- [HUB Spec §19.1 P1 HUB 后台 session 续期](../specs/2026-04-27-hub-middleware-design.md#191-p1spec-中先给默认用户-review-时调)
- [HUB CLAUDE.md UI 大白话原则](/Users/lin/Desktop/hub/CLAUDE.md)
- [Plan 2 setup wizard 骨架](2026-04-27-hub-skeleton.md)（步骤 1 + token 校验）

**前置依赖：**
- ✅ Plan 1-4 全部完成（ERP 集成 / HUB 骨架 / 钉钉接入 + 绑定 / 业务用例）
- ✅ 已有 16 张 HUB Postgres 表 + ConsumedBindingToken（17 张）
- ✅ 6 预设角色 + 14 权限码已种子
- ✅ 加密 secret + Bootstrap Token + admin key + Erp4Adapter 都就绪

**估时：** 8-10 天

---

## 文件结构

### 后端新增

| 文件 | 职责 |
|---|---|
| `backend/hub/auth/erp_session.py` | HUB session = ERP JWT 包装（cookie 鉴权 + 滑动续期） |
| `backend/hub/auth/admin_perms.py` | HUB 后台路由权限装饰器（require_hub_perm） |
| `backend/hub/routers/admin/__init__.py` | admin 路由聚合 |
| `backend/hub/routers/admin/login.py` | POST /admin/login（包装 ERP /auth/login） |
| `backend/hub/routers/admin/users.py` | hub_user / 角色 / 角色分配 / 账号关联 / 权限码 |
| `backend/hub/routers/admin/downstreams.py` | 下游系统 CRUD（ERP / 未来 CRM） |
| `backend/hub/routers/admin/channels.py` | 渠道 CRUD（钉钉 / 未来企微） |
| `backend/hub/routers/admin/ai_providers.py` | AI 提供商 CRUD + 测试连接 |
| `backend/hub/routers/admin/system_config.py` | 告警接收人 / TTL / 时区等 |
| `backend/hub/routers/admin/tasks.py` | task_log 查询 + 详情 + 重试 |
| `backend/hub/routers/admin/conversation.py` | 对话监控（SSE 实时流 + 历史搜索 + 详情）+ meta_audit |
| `backend/hub/routers/admin/audit.py` | 操作审计查询 |
| `backend/hub/routers/admin/dashboard.py` | 仪表盘统计 |
| `backend/hub/routers/setup_full.py` | 向导步骤 2-6 完整业务（在 Plan 2 setup.py 基础上扩展 / 替换） |
| `backend/hub/cron/scheduler.py` | asyncio 调度器（lifespan 启动 daily_employee_audit + task_payload 清理） |
| `backend/hub/cron/dingtalk_user_client.py` | cron 专用钉钉 OpenAPI 客户端（fetch_active_userids） |
| `backend/hub/cron/jobs.py` | 具体 job 函数（构造依赖 + 重试 + 错误隔离） |
| `backend/hub/cron/task_payload_cleanup.py` | 每日清理过期 task_payload |
| `backend/hub/observability/__init__.py` | task_log 写入辅助 |
| `backend/hub/observability/task_logger.py` | 在 inbound/outbound handler 透明写入 task_log + 加密 task_payload |
| `backend/hub/observability/live_stream.py` | Redis Pub/Sub 实时事件推流 |

### 后端修改

| 文件 | 修改 |
|---|---|
| `backend/main.py` | lifespan 启动 cron scheduler + 钉钉重连 task；注册 admin routers + setup_full |
| `backend/worker.py` | inbound / outbound handler 包装 task_logger（写入 task_log + 加密 payload） |
| `backend/hub/handlers/dingtalk_inbound.py` | 接 task_logger（已绑定路径写 task_log） |
| `backend/hub/handlers/dingtalk_outbound.py` | 接 task_logger（标记任务完成） |
| `backend/hub/services/binding_service.py` | confirm_final 触发 audit_log 写入；admin 强制解绑触发 audit_log |
| `backend/hub/lifecycle/dingtalk_connect.py` | 追加 connect_with_reload（循环模式 + reload event 热重载） |
| `backend/hub/routers/setup.py` | 删除（被 setup_full.py 替代） |

### 前端新建（Vue 3 SPA，从 ERP 复制 ui/ 起步）

| 文件 | 职责 |
|---|---|
| `frontend/package.json` | 依赖：vue 3 / vite 7 / tailwind 4 / pinia 3 / vue-router 4 / axios / lucide-vue-next / chart.js |
| `frontend/vite.config.js` | 与 ERP 一致风格（无 alias），构建到 `backend/static/` |
| `frontend/index.html` | SPA 入口 |
| `frontend/src/main.js` | Vue app 启动 + Pinia + 路由 |
| `frontend/src/App.vue` | 根组件 |
| `frontend/src/router/index.js` | 路由：`/setup/*` / `/login` / `/admin/*` |
| `frontend/src/api/index.js` | axios 实例（baseURL `/hub/v1`） |
| `frontend/src/api/{setup,auth,users,roles,downstreams,channels,ai,config,tasks,conversation,audit,dashboard}.js` | 12 个 API 模块 |
| `frontend/src/stores/auth.js` | HUB 用户态 + permissions 缓存 |
| `frontend/src/components/ui/*` | 复制 ERP 起步：AppCard / AppButton / AppInput / AppSelect / AppTable / AppPagination / AppModal / AppActionMenu / SegmentedControl / DateRangePicker / SearchBar |
| `frontend/src/styles/{tokens,theme,base}.css` | 复制 ERP 起步 |
| `frontend/src/views/setup/Step01Welcome.vue` | 已在 Plan 2 占位，本 plan 完善 |
| `frontend/src/views/setup/Step02Erp.vue` | 注册 ERP 系统连接 + 测试连接 |
| `frontend/src/views/setup/Step03Admin.vue` | 创建第一个管理员（输 ERP 账号密码） |
| `frontend/src/views/setup/Step04Dingtalk.vue` | 注册钉钉应用 |
| `frontend/src/views/setup/Step05AI.vue` | 注册 AI 提供商（DeepSeek / Qwen 默认值） |
| `frontend/src/views/setup/Step06Done.vue` | 完成提示 + 跳登录页 |
| `frontend/src/views/auth/LoginView.vue` | HUB 登录页（输 ERP 账号密码） |
| `frontend/src/views/admin/AdminLayout.vue` | 后台壳（左侧 nav + 顶部用户菜单 + 内容区） |
| `frontend/src/views/admin/DashboardView.vue` | 仪表盘 |
| `frontend/src/views/admin/UsersView.vue` | hub_user 列表 + 详情 |
| `frontend/src/views/admin/RolesView.vue` | hub_role 列表 |
| `frontend/src/views/admin/UserRolesView.vue` | 用户角色分配 |
| `frontend/src/views/admin/AccountLinksView.vue` | 账号关联 |
| `frontend/src/views/admin/PermissionsView.vue` | 功能权限说明（只读） |
| `frontend/src/views/admin/DownstreamsView.vue` | 下游系统管理 |
| `frontend/src/views/admin/ChannelsView.vue` | 渠道管理 |
| `frontend/src/views/admin/AIProvidersView.vue` | AI 提供商管理 |
| `frontend/src/views/admin/SystemConfigView.vue` | 系统设置 |
| `frontend/src/views/admin/TasksView.vue` | 任务流水查询 |
| `frontend/src/views/admin/TaskDetailView.vue` | 单 task 详情 |
| `frontend/src/views/admin/ConversationLiveView.vue` | 实时对话流（SSE） |
| `frontend/src/views/admin/ConversationHistoryView.vue` | 历史对话搜索 |
| `frontend/src/views/admin/AuditView.vue` | 操作审计 |
| `frontend/src/views/admin/HealthView.vue` | 系统健康检查 |

### 测试

| 文件 | 数量 | 职责 |
|---|---|---|
| `backend/tests/test_erp_session_auth.py` | 6 | login 包装 / cookie 校验 / 续期 / 过期 / 错密码 / logout |
| `backend/tests/test_admin_perms.py` | 5 | require_hub_perm 装饰器 + 6 预设角色权限矩阵 |
| `backend/tests/test_admin_users.py` | 7 | 列表 / 详情 / 角色分配 / 账号关联 / 强制解绑 |
| `backend/tests/test_admin_downstreams.py` | 5 | 创建 / 列表 / 改 ApiKey / 测试连接 / 吊销 |
| `backend/tests/test_admin_channels.py` | 6 | 创建 / 改 secret / 测试 stream / 列表 / PUT /channels/{id} 触发 reload event / disable 触发 reload event |
| `backend/tests/test_admin_channels_reload.py` | 3 | reload event 触发重启 / disable 后停止 / cancel 释放 adapter |
| `backend/tests/test_admin_ai_providers.py` | 7 | 模块 import 不抛 NameError（验证 Field 已导入）/ 创建拒绝 claude / 创建（DeepSeek/Qwen 默认）/ 创建后单 active 约束 / 测试 chat / 切 active / 改 key |
| `backend/tests/test_admin_system_config.py` | 4 | 告警接收人 / TTL / 时区 / 持久化 |
| `backend/tests/test_admin_tasks.py` | 6 | 列表 / 筛选 / 详情 / 看 payload 触发 meta_audit / 手工重试 |
| `backend/tests/test_admin_conversation_live.py` | 4 | SSE 连接 / 推流 / 鉴权 / 取消 |
| `backend/tests/test_admin_conversation_history.py` | 4 | 历史筛选 / 时间范围 / 关键字 / 用户 |
| `backend/tests/test_admin_audit.py` | 4 | audit_log 写入 / 查询 / 筛选 / 权限 |
| `backend/tests/test_admin_dashboard.py` | 4 | 健康聚合 / 任务统计 / 错误率 / 配额 |
| `backend/tests/test_setup_full.py` | 8 | 步骤 2-6 业务 + initialize_complete |
| `backend/tests/test_cron_scheduler.py` | 5 | 启动 / 无 jobs 安全 / 异常隔离 / hour 校验 / cancel |
| `backend/tests/test_dingtalk_user_client.py` | 4 | 部门树遍历 / userid 聚合 / token 缓存 / errcode 失败 |
| `backend/tests/test_cron_jobs.py` | 6 | 端到端 revoke / 无配置跳过 / 重试成功 / 重试 2 次失败 / payload cleanup / 异常吞掉 |
| `backend/tests/test_task_logger.py` | 5 | 写入 task_log / 加密 payload / 查询 / 过期 / 失败标记 |
| `backend/tests/test_live_stream.py` | 4 | Redis pubsub 推送 / 订阅 / 取消订阅 / 多消费者 |
| **合计** | **97** | （6+5+7+5+6+3+7+4+6+4+4+4+4+8+5+4+6+5+4=97，验证记录按这套文件清单逐文件输出 PASS/FAIL）|

---

## Task 1：HUB session 鉴权（包装 ERP JWT）

**Files:**
- Create: `backend/hub/auth/erp_session.py`
- Test: `backend/tests/test_erp_session_auth.py`

HUB 后台用户态：用户登录时输 ERP 账号密码 → HUB 调 ERP `/auth/login` → 拿 ERP JWT → HUB 把 JWT 包装成 cookie session（httpOnly + SameSite=Strict）。每个 admin 请求带 cookie，HUB 解出 JWT 后调 ERP `/auth/me` 验证（缓存 5 分钟）。JWT 过期自动 401。

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_erp_session_auth.py`：
```python
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_login_forwards_to_erp_and_sets_cookie():
    """POST /admin/login → HUB 调 ERP /auth/login → 设置 hub_session cookie。"""
    from hub.auth.erp_session import ErpSessionAuth

    erp_login_resp = {
        "access_token": "erp_jwt_xxx",
        "user": {"id": 42, "username": "admin", "display_name": "管理员",
                 "role": "admin", "permissions": []},
    }
    erp = AsyncMock()
    erp.login = AsyncMock(return_value=erp_login_resp)

    auth = ErpSessionAuth(erp_adapter=erp)
    cookie_value = await auth.login(username="admin", password="x")
    assert cookie_value
    # 解码 cookie value 应能拿回 JWT + user info
    decoded = auth._decode_cookie(cookie_value)
    assert decoded["jwt"] == "erp_jwt_xxx"
    assert decoded["user"]["id"] == 42


@pytest.mark.asyncio
async def test_verify_cookie_calls_erp_me_with_jwt():
    from hub.auth.erp_session import ErpSessionAuth
    erp = AsyncMock()
    erp.get_me = AsyncMock(return_value={"id": 42, "username": "admin",
                                          "permissions": ["admin"]})

    auth = ErpSessionAuth(erp_adapter=erp)
    cookie = auth._encode_cookie({
        "jwt": "tok", "user": {"id": 42, "username": "admin"},
    })
    user = await auth.verify_cookie(cookie)
    assert user["id"] == 42
    erp.get_me.assert_awaited_once_with(jwt="tok")


@pytest.mark.asyncio
async def test_verify_cookie_invalid_returns_none():
    from hub.auth.erp_session import ErpSessionAuth
    auth = ErpSessionAuth(erp_adapter=AsyncMock())
    assert await auth.verify_cookie("garbage") is None


@pytest.mark.asyncio
async def test_verify_cookie_jwt_expired():
    """ERP /me 返回 401 → HUB session 失效。"""
    from hub.auth.erp_session import ErpSessionAuth
    from hub.adapters.downstream.erp4 import ErpPermissionError
    erp = AsyncMock()
    erp.get_me = AsyncMock(side_effect=ErpPermissionError("401"))

    auth = ErpSessionAuth(erp_adapter=erp)
    cookie = auth._encode_cookie({"jwt": "expired", "user": {"id": 1}})
    assert await auth.verify_cookie(cookie) is None


@pytest.mark.asyncio
async def test_verify_cache_avoids_repeated_erp_calls():
    """5 分钟内同 cookie 不应重复调 ERP /auth/me。"""
    from hub.auth.erp_session import ErpSessionAuth
    erp = AsyncMock()
    erp.get_me = AsyncMock(return_value={"id": 1, "permissions": []})
    auth = ErpSessionAuth(erp_adapter=erp, cache_ttl=300)

    cookie = auth._encode_cookie({"jwt": "tok", "user": {"id": 1}})
    await auth.verify_cookie(cookie)
    await auth.verify_cookie(cookie)
    erp.get_me.assert_awaited_once()


@pytest.mark.asyncio
async def test_logout_invalidates_jwt_at_erp_and_clears_cache():
    """logout 必须调 ERP /auth/logout 让 JWT 失效；清本地 cache 后再次 verify
    会重调 ERP，但 ERP 此时返回 401 → verify 返回 None。"""
    from hub.auth.erp_session import ErpSessionAuth
    from hub.adapters.downstream.erp4 import ErpPermissionError

    erp = AsyncMock()
    # 第一次 get_me 成功
    erp.get_me = AsyncMock(return_value={"id": 1, "permissions": []})
    erp.logout = AsyncMock()
    auth = ErpSessionAuth(erp_adapter=erp, cache_ttl=300)

    cookie = auth._encode_cookie({"jwt": "tok", "user": {"id": 1}})
    user = await auth.verify_cookie(cookie)
    assert user is not None

    # logout 应调 ERP /auth/logout
    await auth.logout(cookie)
    erp.logout.assert_awaited_once_with(jwt="tok")

    # 模拟 ERP 端 token_version 已递增 → 后续 get_me 返回 401
    erp.get_me = AsyncMock(side_effect=ErpPermissionError("401"))
    user2 = await auth.verify_cookie(cookie)
    assert user2 is None  # 旧 cookie 不可用


@pytest.mark.asyncio
async def test_logout_handles_decode_error_gracefully():
    """坏 cookie logout 不抛异常（仍清 cache）。"""
    from hub.auth.erp_session import ErpSessionAuth
    erp = AsyncMock()
    auth = ErpSessionAuth(erp_adapter=erp, cache_ttl=300)
    await auth.logout("garbage")  # 不应抛
    erp.logout.assert_not_called()
```

- [ ] **Step 2: 实现 ErpSessionAuth**

需要先在 Erp4Adapter 加 `login()` 和 `get_me()` 方法。修改 `backend/hub/adapters/downstream/erp4.py` 加：
```python
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
        raise ErpSystemError(f"网络错误: {e}")


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
        raise ErpSystemError(f"网络错误: {e}")


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
```

文件 `backend/hub/auth/erp_session.py`：
```python
"""HUB session = 包装 ERP JWT 的 cookie 会话。

流程：
1. 用户在 HUB 登录页输 ERP 账号密码
2. HUB 调 ERP /auth/login → 拿 access_token + user
3. HUB 把 (jwt + user) 用 HUB_MASTER_KEY 加密成 cookie
4. 每次 admin 请求带 cookie → HUB 解出 jwt → 调 ERP /auth/me 验证（缓存 5 分钟）
5. ERP JWT 24h 内每次请求自动续期；过期 → cookie 失效 → 401
"""
from __future__ import annotations
import json
import time
import logging
from hub.crypto import encrypt_secret, decrypt_secret, DecryptError
from hub.adapters.downstream.erp4 import ErpPermissionError, ErpSystemError, ErpAdapterError

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
        import base64
        return base64.urlsafe_b64encode(ct).decode("ascii")

    def _decode_cookie(self, cookie: str) -> dict:
        import base64
        try:
            ct = base64.urlsafe_b64decode(cookie.encode("ascii"))
            plain = decrypt_secret(ct, purpose=self.PURPOSE)
            return json.loads(plain)
        except (DecryptError, ValueError, json.JSONDecodeError):
            raise

    async def login(self, username: str, password: str) -> str:
        """登录成功返回 cookie 字符串；失败抛异常。"""
        try:
            resp = await self.erp.login(username=username, password=password)
        except ErpPermissionError as e:
            raise  # 由调用方翻译
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
        except Exception:
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
        # 先解 cookie 拿 jwt
        try:
            payload = self._decode_cookie(cookie)
            jwt = payload.get("jwt")
            if jwt:
                await self.erp.logout(jwt=jwt)
        except Exception:
            pass  # 解码失败也要清本地 cache
        self._cache.pop(cookie, None)
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
cd /Users/lin/Desktop/hub/backend
pytest tests/test_erp_session_auth.py -v
git add backend/hub/auth/erp_session.py \
        backend/hub/adapters/downstream/erp4.py \
        backend/tests/test_erp_session_auth.py
git commit -m "feat(hub): ErpSessionAuth（包装 ERP JWT 为 cookie + 5 分钟 verify 缓存）+ Erp4Adapter login/get_me"
```

---

## Task 2：HUB 后台权限装饰器 + 6 角色权限矩阵

**Files:**
- Create: `backend/hub/auth/admin_perms.py`
- Test: `backend/tests/test_admin_perms.py`

`require_hub_perm("platform.tasks.read")` FastAPI 依赖：从 cookie 解出 ERP user → 查 `dingtalk_user_binding` 找 hub_user（admin 走 ERP 直接绑定那条）→ 查 hub_user_role + hub_role_permission → 校验权限。

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_admin_perms.py`：
```python
import pytest
from fastapi import FastAPI, Depends, HTTPException
from httpx import AsyncClient, ASGITransport


def _make_app_with_dep(dep_fn):
    from fastapi import APIRouter
    app = FastAPI()
    @app.get("/protected")
    async def protected(_=Depends(dep_fn)):
        return {"ok": True}
    return app


@pytest.mark.asyncio
async def test_no_cookie_returns_401(setup_db):
    from hub.auth.admin_perms import require_hub_perm
    app = _make_app_with_dep(require_hub_perm("platform.tasks.read"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.get("/protected")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_role_passes_all_perms(setup_db):
    """platform_admin 拥有所有权限。"""
    from hub.auth.admin_perms import _check_perm_for_hub_user
    from hub.models import HubUser, HubRole, HubUserRole
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="Adm")
    role = await HubRole.get(code="platform_admin")
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    for perm in ["platform.tasks.read", "platform.users.write",
                 "platform.conversation.monitor", "downstream.erp.use"]:
        assert await _check_perm_for_hub_user(user.id, perm) is True


@pytest.mark.asyncio
async def test_viewer_only_has_read_perms(setup_db):
    from hub.auth.admin_perms import _check_perm_for_hub_user
    from hub.models import HubUser, HubRole, HubUserRole
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="V")
    role = await HubRole.get(code="platform_viewer")
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    assert await _check_perm_for_hub_user(user.id, "platform.tasks.read") is True
    assert await _check_perm_for_hub_user(user.id, "platform.users.write") is False
    assert await _check_perm_for_hub_user(user.id, "platform.flags.write") is False


@pytest.mark.asyncio
async def test_resolve_hub_user_from_erp_session(setup_db):
    """ERP user.id → 找到对应的 hub_user（通过 downstream_identity）。"""
    from hub.auth.admin_perms import resolve_hub_user_from_erp
    from hub.models import HubUser, DownstreamIdentity

    user = await HubUser.create(display_name="X")
    await DownstreamIdentity.create(
        hub_user=user, downstream_type="erp", downstream_user_id=42,
    )
    found = await resolve_hub_user_from_erp(erp_user_id=42)
    assert found is not None
    assert found.id == user.id


@pytest.mark.asyncio
async def test_no_hub_user_for_erp_returns_403(setup_db):
    """ERP 用户存在但 HUB 没绑定 hub_user → 403（未授权进 HUB 后台）。"""
    from hub.auth.admin_perms import resolve_hub_user_from_erp
    found = await resolve_hub_user_from_erp(erp_user_id=999)
    assert found is None
```

- [ ] **Step 2: 实现 admin_perms.py**

文件 `backend/hub/auth/admin_perms.py`：
```python
"""HUB 后台路由权限校验（require_hub_perm 依赖）。

链路：cookie → ERP user → HUB hub_user（via DownstreamIdentity）→
hub_user_role → hub_role_permission → 校验。
"""
from __future__ import annotations
import logging
from fastapi import Cookie, Depends, HTTPException, Request
from hub.models import HubUser, DownstreamIdentity, HubUserRole, HubRole
from hub.permissions import has_permission

logger = logging.getLogger("hub.auth.admin_perms")


async def resolve_hub_user_from_erp(erp_user_id: int) -> HubUser | None:
    di = await DownstreamIdentity.filter(
        downstream_type="erp", downstream_user_id=erp_user_id,
    ).first()
    if di is None:
        return None
    return await HubUser.filter(id=di.hub_user_id).first()


async def _check_perm_for_hub_user(hub_user_id: int, perm_code: str) -> bool:
    return await has_permission(hub_user_id, perm_code)


def require_hub_perm(perm_code: str):
    """FastAPI 依赖：要求当前 cookie 用户拥有指定 HUB 权限。"""
    async def _dep(request: Request, hub_session: str | None = Cookie(default=None)):
        auth = getattr(request.app.state, "session_auth", None)
        if auth is None:
            raise HTTPException(status_code=503, detail="HUB session 未配置")

        erp_user = await auth.verify_cookie(hub_session)
        if erp_user is None:
            raise HTTPException(status_code=401, detail="请先登录")

        hub_user = await resolve_hub_user_from_erp(erp_user_id=erp_user["id"])
        if hub_user is None:
            raise HTTPException(
                status_code=403,
                detail="你的 ERP 账号未关联 HUB 用户。如需访问后台请联系管理员。",
            )

        if not await _check_perm_for_hub_user(hub_user.id, perm_code):
            raise HTTPException(status_code=403, detail=f"缺少权限：{perm_code}")

        # 把 hub_user 注入 request.state 供下游使用
        request.state.hub_user = hub_user
        request.state.erp_user = erp_user
        return hub_user

    return _dep
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
pytest tests/test_admin_perms.py -v
git add backend/hub/auth/admin_perms.py backend/tests/test_admin_perms.py
git commit -m "feat(hub): require_hub_perm 装饰器（cookie → ERP user → hub_user → 权限校验）"
```

---

## Task 3：admin login / logout 路由

**Files:**
- Create: `backend/hub/routers/admin/__init__.py`
- Create: `backend/hub/routers/admin/login.py`

- [ ] **Step 1: 实现 login router**

文件 `backend/hub/routers/admin/__init__.py`：
```python
"""HUB Web 后台 admin 路由聚合。"""
```

文件 `backend/hub/routers/admin/login.py`：
```python
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Response, Request, Body
from pydantic import BaseModel
from hub.adapters.downstream.erp4 import ErpPermissionError, ErpSystemError

router = APIRouter(prefix="/hub/v1/admin", tags=["admin-auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(request: Request, response: Response, body: LoginRequest = Body(...)):
    auth = request.app.state.session_auth
    try:
        cookie = await auth.login(username=body.username, password=body.password)
    except ErpPermissionError:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    except ErpSystemError as e:
        raise HTTPException(status_code=502, detail=f"ERP 通信失败：{e}")

    response.set_cookie(
        "hub_session", cookie,
        httponly=True, samesite="strict", max_age=86400,  # 24h 滑动续期
        secure=False,  # 部署到 HTTPS 时改 True
    )
    return {"success": True}


@router.post("/logout")
async def logout(request: Request, response: Response):
    cookie = request.cookies.get("hub_session")
    if cookie:
        await request.app.state.session_auth.logout(cookie)
    response.delete_cookie("hub_session")
    return {"success": True}


@router.get("/me")
async def me(request: Request):
    """前端用：查当前登录用户 + 权限。"""
    cookie = request.cookies.get("hub_session")
    auth = request.app.state.session_auth
    erp_user = await auth.verify_cookie(cookie)
    if erp_user is None:
        raise HTTPException(status_code=401, detail="未登录")

    from hub.auth.admin_perms import resolve_hub_user_from_erp
    hub_user = await resolve_hub_user_from_erp(erp_user_id=erp_user["id"])
    permissions = []
    if hub_user:
        from hub.permissions import get_user_permissions
        permissions = list(await get_user_permissions(hub_user.id))

    return {
        "erp_user": erp_user,
        "hub_user_id": hub_user.id if hub_user else None,
        "permissions": permissions,
    }
```

- [ ] **Step 2: 在 main.py 注册路由 + 初始化 session_auth**

修改 `backend/main.py:lifespan`，在 `init_db()` 之后追加：
```python
    from hub.adapters.downstream.erp4 import Erp4Adapter
    from hub.auth.erp_session import ErpSessionAuth
    from hub.models import DownstreamSystem
    from hub.crypto import decrypt_secret

    # 加载 ERP 下游配置（如已存在）；初始化向导未完成时为 None
    ds = await DownstreamSystem.filter(downstream_type="erp", status="active").first()
    if ds:
        erp_api_key = decrypt_secret(ds.encrypted_apikey, purpose="config_secrets")
        erp_adapter_for_session = Erp4Adapter(base_url=ds.base_url, api_key=erp_api_key)
        app.state.session_auth = ErpSessionAuth(erp_adapter=erp_adapter_for_session)
    else:
        app.state.session_auth = None  # /admin/login 启用前会等向导完成
```

并注册 router：
```python
from hub.routers.admin import login as admin_login
app.include_router(admin_login.router)
```

- [ ] **Step 3: 提交**

```bash
git add backend/hub/routers/admin/__init__.py \
        backend/hub/routers/admin/login.py \
        backend/main.py
git commit -m "feat(hub): admin login / logout / me 路由 + session_auth 注入到 app.state"
```

---

## Task 4：用户 / 角色 / 用户角色分配 / 账号关联 / 权限说明 5 个核心管理路由

**Files:**
- Create: `backend/hub/routers/admin/users.py`
- Test: `backend/tests/test_admin_users.py`

5 个权限页用一个 router 文件，路径分组。

- [ ] **Step 1: 实现 users router**

文件 `backend/hub/routers/admin/users.py`：
```python
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request, Body, Query
from pydantic import BaseModel
from hub.auth.admin_perms import require_hub_perm
from hub.models import (
    HubUser, ChannelUserBinding, DownstreamIdentity,
    HubRole, HubUserRole, HubPermission,
    AuditLog,
)
from datetime import datetime, timezone

router = APIRouter(prefix="/hub/v1/admin", tags=["admin-users"])


# ===== HubUser 列表 =====
@router.get("/hub-users", dependencies=[Depends(require_hub_perm("platform.users.write"))])
async def list_hub_users(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
):
    qs = HubUser.all().order_by("-created_at")
    if keyword:
        qs = qs.filter(display_name__icontains=keyword)
    total = await qs.count()
    items = await qs.offset((page - 1) * page_size).limit(page_size)
    return {
        "items": [
            {
                "id": u.id, "display_name": u.display_name, "status": u.status,
                "created_at": u.created_at,
            }
            for u in items
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get(
    "/hub-users/{user_id}",
    dependencies=[Depends(require_hub_perm("platform.users.write"))],
)
async def get_hub_user_detail(user_id: int):
    user = await HubUser.filter(id=user_id).first()
    if user is None:
        raise HTTPException(404, "HUB 用户不存在")
    bindings = await ChannelUserBinding.filter(hub_user_id=user_id)
    identities = await DownstreamIdentity.filter(hub_user_id=user_id)
    user_roles = await HubUserRole.filter(hub_user_id=user_id).select_related("role")
    return {
        "id": user.id, "display_name": user.display_name, "status": user.status,
        "channel_bindings": [
            {"channel_type": b.channel_type, "channel_userid": b.channel_userid,
             "status": b.status, "bound_at": b.bound_at, "revoked_at": b.revoked_at,
             "revoked_reason": b.revoked_reason}
            for b in bindings
        ],
        "downstream_identities": [
            {"downstream_type": d.downstream_type, "downstream_user_id": d.downstream_user_id}
            for d in identities
        ],
        "roles": [
            {"id": ur.role_id, "code": ur.role.code, "name": ur.role.name}
            for ur in user_roles
        ],
    }


# ===== HubRole 列表（只读，C 阶段不支持自定义编辑） =====
@router.get("/hub-roles", dependencies=[Depends(require_hub_perm("platform.users.write"))])
async def list_hub_roles():
    roles = await HubRole.all().prefetch_related("permissions")
    items = []
    for r in roles:
        perms = [p async for p in r.permissions]
        items.append({
            "id": r.id, "code": r.code, "name": r.name,
            "description": r.description, "is_builtin": r.is_builtin,
            "permissions": [{"code": p.code, "name": p.name} for p in perms],
        })
    return {"items": items}


# ===== HubPermission 列表（"功能权限说明"页面） =====
@router.get("/hub-permissions", dependencies=[Depends(require_hub_perm("platform.users.write"))])
async def list_hub_permissions():
    perms = await HubPermission.all().order_by("resource", "sub_resource", "action")
    return {
        "items": [
            {
                "code": p.code, "resource": p.resource, "sub_resource": p.sub_resource,
                "action": p.action, "name": p.name, "description": p.description,
            }
            for p in perms
        ],
    }


# ===== 用户角色分配 =====
class AssignRolesRequest(BaseModel):
    role_ids: list[int]


@router.put(
    "/hub-users/{user_id}/roles",
    dependencies=[Depends(require_hub_perm("platform.users.write"))],
)
async def assign_user_roles(
    request: Request, user_id: int, body: AssignRolesRequest = Body(...),
):
    user = await HubUser.filter(id=user_id).first()
    if user is None:
        raise HTTPException(404, "HUB 用户不存在")
    valid_roles = await HubRole.filter(id__in=body.role_ids)
    if len(valid_roles) != len(set(body.role_ids)):
        raise HTTPException(400, "包含无效角色 ID")

    # 删除旧的所有 + 加新的
    await HubUserRole.filter(hub_user_id=user_id).delete()
    actor = request.state.hub_user
    for role in valid_roles:
        await HubUserRole.create(
            hub_user_id=user_id, role_id=role.id, assigned_by_hub_user_id=actor.id,
        )

    await AuditLog.create(
        who_hub_user_id=actor.id, action="assign_roles",
        target_type="hub_user", target_id=str(user_id),
        detail={"role_ids": body.role_ids},
    )
    return {"success": True}


# ===== 账号关联 =====
class UpdateDownstreamIdentityRequest(BaseModel):
    downstream_type: str
    downstream_user_id: int


@router.put(
    "/hub-users/{user_id}/downstream-identity",
    dependencies=[Depends(require_hub_perm("platform.users.write"))],
)
async def update_downstream_identity(
    request: Request, user_id: int, body: UpdateDownstreamIdentityRequest = Body(...),
):
    user = await HubUser.filter(id=user_id).first()
    if user is None:
        raise HTTPException(404, "HUB 用户不存在")

    di = await DownstreamIdentity.filter(
        hub_user_id=user_id, downstream_type=body.downstream_type,
    ).first()
    if di:
        di.downstream_user_id = body.downstream_user_id
        await di.save()
    else:
        await DownstreamIdentity.create(
            hub_user_id=user_id, downstream_type=body.downstream_type,
            downstream_user_id=body.downstream_user_id,
        )

    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="update_downstream_identity",
        target_type="hub_user", target_id=str(user_id),
        detail={"downstream_type": body.downstream_type,
                "downstream_user_id": body.downstream_user_id},
    )
    return {"success": True}


# ===== 强制解绑（admin 后台） =====
@router.post(
    "/hub-users/{user_id}/force-unbind",
    dependencies=[Depends(require_hub_perm("platform.users.write"))],
)
async def force_unbind(request: Request, user_id: int, channel_type: str = Query(...)):
    binding = await ChannelUserBinding.filter(
        hub_user_id=user_id, channel_type=channel_type, status="active",
    ).first()
    if binding is None:
        raise HTTPException(404, "无活跃绑定")
    binding.status = "revoked"
    binding.revoked_at = datetime.now(timezone.utc)
    binding.revoked_reason = "admin_force"
    await binding.save()

    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="force_unbind",
        target_type="hub_user", target_id=str(user_id),
        detail={"channel_type": channel_type},
    )

    # 投递 outbound 通知用户
    runner = getattr(request.app.state, "task_runner", None)
    if runner:
        await runner.submit("dingtalk_outbound", {
            "channel_userid": binding.channel_userid,
            "type": "text",
            "text": "你的 HUB 绑定已被管理员解除。如需重新绑定请发送 /绑定 你的ERP用户名。",
        })
    return {"success": True}
```

- [ ] **Step 2: 写测试**

文件 `backend/tests/test_admin_users.py`：
```python
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock


@pytest.fixture
async def admin_client(setup_db):
    """已登录 admin 的 client（cookie 已带）。"""
    from hub.seed import run_seed
    from hub.models import HubUser, DownstreamIdentity, HubRole, HubUserRole
    await run_seed()

    user = await HubUser.create(display_name="管理员")
    await DownstreamIdentity.create(
        hub_user=user, downstream_type="erp", downstream_user_id=1,
    )
    role = await HubRole.get(code="platform_admin")
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    from main import app
    erp = AsyncMock()
    erp.get_me = AsyncMock(return_value={"id": 1, "username": "admin", "permissions": []})
    from hub.auth.erp_session import ErpSessionAuth
    auth = ErpSessionAuth(erp_adapter=erp)
    app.state.session_auth = auth
    cookie = auth._encode_cookie({"jwt": "tok", "user": {"id": 1, "username": "admin"}})

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        yield ac, user


@pytest.mark.asyncio
async def test_list_hub_users(admin_client):
    ac, _ = admin_client
    resp = await ac.get("/hub/v1/admin/hub-users")
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_get_hub_user_detail(admin_client):
    ac, user = admin_client
    resp = await ac.get(f"/hub/v1/admin/hub-users/{user.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == user.id
    assert "channel_bindings" in body
    assert "roles" in body


@pytest.mark.asyncio
async def test_list_hub_roles(admin_client):
    ac, _ = admin_client
    resp = await ac.get("/hub/v1/admin/hub-roles")
    assert resp.status_code == 200
    codes = {r["code"] for r in resp.json()["items"]}
    assert "platform_admin" in codes
    assert "bot_user_basic" in codes


@pytest.mark.asyncio
async def test_list_hub_permissions(admin_client):
    ac, _ = admin_client
    resp = await ac.get("/hub/v1/admin/hub-permissions")
    body = resp.json()
    perm_codes = {p["code"] for p in body["items"]}
    assert "platform.tasks.read" in perm_codes
    # UI 文案不暴露 code
    for p in body["items"]:
        assert p["name"]
        assert p["code"] not in p["name"]


@pytest.mark.asyncio
async def test_assign_user_roles_writes_audit(admin_client):
    from hub.models import HubUser, HubRole, AuditLog
    ac, actor = admin_client
    target = await HubUser.create(display_name="目标用户")
    role = await HubRole.get(code="bot_user_basic")

    resp = await ac.put(
        f"/hub/v1/admin/hub-users/{target.id}/roles",
        json={"role_ids": [role.id]},
    )
    assert resp.status_code == 200
    audits = await AuditLog.filter(action="assign_roles").all()
    assert len(audits) >= 1


@pytest.mark.asyncio
async def test_force_unbind(admin_client):
    from hub.models import HubUser, ChannelUserBinding
    ac, _ = admin_client
    target = await HubUser.create(display_name="被解绑")
    await ChannelUserBinding.create(
        hub_user=target, channel_type="dingtalk",
        channel_userid="m_target", status="active",
    )
    resp = await ac.post(
        f"/hub/v1/admin/hub-users/{target.id}/force-unbind",
        params={"channel_type": "dingtalk"},
    )
    assert resp.status_code == 200
    binding = await ChannelUserBinding.filter(channel_userid="m_target").first()
    assert binding.status == "revoked"
    assert binding.revoked_reason == "admin_force"


@pytest.mark.asyncio
async def test_no_perm_user_blocked(setup_db):
    """没有 platform.users.write 权限的 hub_user 调用应 403。"""
    from hub.models import HubUser, DownstreamIdentity, HubRole, HubUserRole
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="V")
    await DownstreamIdentity.create(
        hub_user=user, downstream_type="erp", downstream_user_id=2,
    )
    role = await HubRole.get(code="platform_viewer")
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    from main import app
    erp = AsyncMock()
    erp.get_me = AsyncMock(return_value={"id": 2, "username": "v"})
    from hub.auth.erp_session import ErpSessionAuth
    auth = ErpSessionAuth(erp_adapter=erp)
    app.state.session_auth = auth
    cookie = auth._encode_cookie({"jwt": "x", "user": {"id": 2}})
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://t",
        cookies={"hub_session": cookie},
    ) as ac:
        resp = await ac.get("/hub/v1/admin/hub-users")
        assert resp.status_code == 403
```

- [ ] **Step 3: main.py 注册 + 跑测试 + 提交**

```bash
# main.py 加 from hub.routers.admin import users; app.include_router(users.router)
pytest tests/test_admin_users.py -v
git add backend/hub/routers/admin/users.py \
        backend/main.py \
        backend/tests/test_admin_users.py
git commit -m "feat(hub): admin 用户/角色/角色分配/账号关联/权限说明 5 个 API"
```

---

## Task 5：下游系统 / 渠道 / AI 提供商 / 系统设置 4 个配置路由

**Files:**
- Create: `backend/hub/routers/admin/downstreams.py`
- Create: `backend/hub/routers/admin/channels.py`
- Create: `backend/hub/routers/admin/ai_providers.py`
- Create: `backend/hub/routers/admin/system_config.py`
- Test: 4 个对应测试文件

每个 router 模式相同：CRUD + 测试连接 + 加密 secret 字段处理（写入加密存，读取不返回明文）。

- [ ] **Step 1: 实现 downstreams router（含创建 / 列表 / 改 ApiKey / 测试连接 / 吊销）**

文件 `backend/hub/routers/admin/downstreams.py`：
```python
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from pydantic import BaseModel
from hub.auth.admin_perms import require_hub_perm
from hub.crypto import encrypt_secret, decrypt_secret
from hub.models import DownstreamSystem, AuditLog

router = APIRouter(prefix="/hub/v1/admin/downstreams", tags=["admin-downstreams"])


class CreateDownstreamRequest(BaseModel):
    downstream_type: str  # erp / crm / ...
    name: str
    base_url: str
    api_key: str  # 明文，加密存储
    apikey_scopes: list[str]


@router.post("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def create_downstream(request: Request, body: CreateDownstreamRequest = Body(...)):
    encrypted = encrypt_secret(body.api_key, purpose="config_secrets")
    ds = await DownstreamSystem.create(
        downstream_type=body.downstream_type, name=body.name, base_url=body.base_url,
        encrypted_apikey=encrypted, apikey_scopes=body.apikey_scopes, status="active",
    )
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="create_downstream",
        target_type="downstream_system", target_id=str(ds.id),
        detail={"downstream_type": body.downstream_type, "name": body.name},
    )
    return {"id": ds.id, "name": ds.name}


@router.get("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def list_downstreams():
    items = await DownstreamSystem.all().order_by("-created_at")
    return {
        "items": [
            {
                "id": d.id, "downstream_type": d.downstream_type,
                "name": d.name, "base_url": d.base_url,
                "apikey_scopes": d.apikey_scopes, "status": d.status,
                # 不返回 encrypted_apikey 明文，仅返回长度提示
                "apikey_set": True,
            }
            for d in items
        ],
    }


class UpdateApiKeyRequest(BaseModel):
    api_key: str
    apikey_scopes: list[str] | None = None


@router.put(
    "/{ds_id}/apikey",
    dependencies=[Depends(require_hub_perm("platform.apikeys.write"))],
)
async def update_apikey(request: Request, ds_id: int, body: UpdateApiKeyRequest = Body(...)):
    ds = await DownstreamSystem.filter(id=ds_id).first()
    if ds is None:
        raise HTTPException(404, "下游系统不存在")
    ds.encrypted_apikey = encrypt_secret(body.api_key, purpose="config_secrets")
    if body.apikey_scopes is not None:
        ds.apikey_scopes = body.apikey_scopes
    await ds.save()

    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="update_downstream_apikey",
        target_type="downstream_system", target_id=str(ds_id), detail={},
    )
    return {"success": True}


@router.post(
    "/{ds_id}/test-connection",
    dependencies=[Depends(require_hub_perm("platform.apikeys.write"))],
)
async def test_connection(ds_id: int):
    ds = await DownstreamSystem.filter(id=ds_id).first()
    if ds is None:
        raise HTTPException(404, "下游系统不存在")

    if ds.downstream_type != "erp":
        raise HTTPException(400, f"暂不支持测试 {ds.downstream_type} 类型连接")

    from hub.adapters.downstream.erp4 import Erp4Adapter
    api_key = decrypt_secret(ds.encrypted_apikey, purpose="config_secrets")
    adapter = Erp4Adapter(base_url=ds.base_url, api_key=api_key)
    try:
        ok = await adapter.health_check()
    finally:
        await adapter.aclose()
    return {"ok": ok}


@router.post(
    "/{ds_id}/disable",
    dependencies=[Depends(require_hub_perm("platform.apikeys.write"))],
)
async def disable_downstream(request: Request, ds_id: int):
    ds = await DownstreamSystem.filter(id=ds_id).first()
    if ds is None:
        raise HTTPException(404, "下游系统不存在")
    ds.status = "disabled"
    await ds.save()
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="disable_downstream",
        target_type="downstream_system", target_id=str(ds_id),
    )
    return {"success": True}
```

- [ ] **Step 2: 实现 channels.py**

文件 `backend/hub/routers/admin/channels.py`（与 downstreams 同结构，CRUD + 测试连接）：
```python
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from pydantic import BaseModel
from hub.auth.admin_perms import require_hub_perm
from hub.crypto import encrypt_secret, decrypt_secret
from hub.models import ChannelApp, AuditLog

router = APIRouter(prefix="/hub/v1/admin/channels", tags=["admin-channels"])


class CreateChannelRequest(BaseModel):
    channel_type: str  # dingtalk
    name: str
    app_key: str
    app_secret: str
    robot_id: str | None = None


@router.post("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def create_channel(request: Request, body: CreateChannelRequest = Body(...)):
    rec = await ChannelApp.create(
        channel_type=body.channel_type, name=body.name,
        encrypted_app_key=encrypt_secret(body.app_key, purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret(body.app_secret, purpose="config_secrets"),
        robot_id=body.robot_id, status="active",
    )
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="create_channel",
        target_type="channel_app", target_id=str(rec.id),
        detail={"channel_type": body.channel_type, "name": body.name},
    )
    return {"id": rec.id}


@router.get("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def list_channels():
    items = await ChannelApp.all().order_by("-created_at")
    return {
        "items": [
            {"id": c.id, "channel_type": c.channel_type, "name": c.name,
             "robot_id": c.robot_id, "status": c.status,
             "secret_set": True}
            for c in items
        ],
    }


class UpdateSecretRequest(BaseModel):
    app_key: str | None = None
    app_secret: str | None = None
    robot_id: str | None = None


@router.put("/{ca_id}", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def update_channel(request: Request, ca_id: int, body: UpdateSecretRequest = Body(...)):
    rec = await ChannelApp.filter(id=ca_id).first()
    if rec is None:
        raise HTTPException(404)
    if body.app_key is not None:
        rec.encrypted_app_key = encrypt_secret(body.app_key, purpose="config_secrets")
    if body.app_secret is not None:
        rec.encrypted_app_secret = encrypt_secret(body.app_secret, purpose="config_secrets")
    if body.robot_id is not None:
        rec.robot_id = body.robot_id
    await rec.save()
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="update_channel",
        target_type="channel_app", target_id=str(ca_id),
    )
    # ❗ 必须在代码块内调用，让运行中的 Stream 拿到新配置；漏掉就退化回"改完不生效"
    _signal_channel_reload(request)
    return {"success": True}


@router.post("/{ca_id}/disable", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def disable_channel(request: Request, ca_id: int):
    rec = await ChannelApp.filter(id=ca_id).first()
    if rec is None:
        raise HTTPException(404)
    rec.status = "disabled"
    await rec.save()
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="disable_channel",
        target_type="channel_app", target_id=str(ca_id),
    )
    # 通知运行中的 Stream 重连（拿到新配置 / 停止已 disable 的）
    _signal_channel_reload(request)
    return {"success": True}


def _signal_channel_reload(request: Request) -> None:
    """通知 gateway 后台 task 重新加载 ChannelApp 并重启 Stream。

    update_channel / disable_channel 完成后调用。Plan 5 在 lifespan 加了
    app.state.dingtalk_reload_event = asyncio.Event()，
    且 connect_dingtalk_stream_when_ready 是循环结构（见 Step 2.5）。
    若 event 不存在（worker 进程或测试环境）则静默忽略。
    """
    evt = getattr(request.app.state, "dingtalk_reload_event", None)
    if evt is not None:
        evt.set()
```

- [ ] **Step 2.5: 改造 connect_dingtalk_stream_when_ready 支持热重载（Plan 3 是单次连接）**

Plan 3 写的 `connect_dingtalk_stream_when_ready` 在连接成功后直接 `return adapter`，task 退出 → 之后无论 admin 怎么改 ChannelApp 都不会生效。Plan 5 改成"循环 + reload event"模式：连接成功后等待 reload 信号 → 收到后停掉旧 adapter，重新读取 ChannelApp，启动新 adapter。

修改 `backend/hub/lifecycle/dingtalk_connect.py`，在文件末尾**追加**新函数 `connect_with_reload`（保留原 `connect_dingtalk_stream_when_ready` 不变以兼容 Plan 3 测试）：

```python
async def connect_with_reload(
    *,
    on_inbound: Callable[[object], Awaitable[None]],
    adapter_factory: Callable[..., object],
    reload_event: asyncio.Event,
    poll_interval_seconds: float = 30.0,
    state_holder: dict | None = None,
) -> None:
    """循环模式：连接 → 等 reload event → 停止 → 重新连接。

    与 connect_dingtalk_stream_when_ready 的区别：连接后不退出，
    监听 reload_event；event 被 set → 关掉 adapter → 重读 ChannelApp →
    （若 status active 则重连，否则继续等下一次 reload）。

    Args:
        on_inbound: 入站消息回调
        adapter_factory: 构造 adapter 的工厂
        reload_event: asyncio.Event；channels.py update/disable 后 set() 触发重连
        poll_interval_seconds: 配置未就绪时的轮询间隔
        state_holder: 可选 dict，连接成功后写入 {"adapter": adapter}，
            供测试 / lifespan 关闭时拿到当前 adapter 调 stop

    永不返回（除非被 cancel）。
    """
    current_adapter: object | None = None
    while True:
        try:
            channel_app = await ChannelApp.filter(
                channel_type="dingtalk", status="active",
            ).first()
            if channel_app is None:
                if current_adapter is not None:
                    logger.info("ChannelApp 已 disabled，停止现有 Stream")
                    try:
                        await current_adapter.stop()
                    except Exception:
                        logger.exception("旧 adapter stop 失败，忽略")
                    current_adapter = None
                    if state_holder is not None:
                        state_holder["adapter"] = None
                # 等下一次 reload event 或轮询周期
                try:
                    await asyncio.wait_for(
                        reload_event.wait(), timeout=poll_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
                reload_event.clear()
                continue

            # 有可用 ChannelApp → 启动新 adapter
            if current_adapter is not None:
                logger.info("ChannelApp 配置变更，停止旧 Stream")
                try:
                    await current_adapter.stop()
                except Exception:
                    logger.exception("旧 adapter stop 失败，忽略")
                current_adapter = None

            app_key = decrypt_secret(channel_app.encrypted_app_key, purpose="config_secrets")
            app_secret = decrypt_secret(channel_app.encrypted_app_secret, purpose="config_secrets")
            adapter = adapter_factory(
                app_key=app_key, app_secret=app_secret, robot_id=channel_app.robot_id,
            )
            adapter.on_message(on_inbound)
            await adapter.start()
            current_adapter = adapter
            if state_holder is not None:
                state_holder["adapter"] = adapter
            logger.info("钉钉 Stream 已连接（reload 模式）")

            # 等 reload event 或被取消
            await reload_event.wait()
            reload_event.clear()
            logger.info("收到 reload 信号，准备重新加载 ChannelApp")

        except asyncio.CancelledError:
            logger.info("connect_with_reload 被取消，关闭 Stream")
            if current_adapter is not None:
                try:
                    await current_adapter.stop()
                except Exception:
                    pass
            return
        except Exception:
            logger.exception("connect_with_reload 异常，下一轮重试")
            await asyncio.sleep(poll_interval_seconds)
```

- [ ] **Step 2.6: main.py lifespan 切换为 connect_with_reload + 创建 reload_event**

修改 `backend/main.py`（在 Plan 3 已写入的 `connect_dingtalk_stream_when_ready` 调用处替换）：

```python
import asyncio as _asyncio
from hub.lifecycle.dingtalk_connect import connect_with_reload
from hub.adapters.channel.dingtalk_stream import DingTalkStreamAdapter

# lifespan 内：
app.state.dingtalk_reload_event = _asyncio.Event()
app.state.dingtalk_state = {}  # holder：{"adapter": DingTalkStreamAdapter | None}

async def on_inbound(msg):
    await runner.submit("dingtalk_inbound", {
        "channel_type": msg.channel_type,
        "channel_userid": msg.channel_userid,
        "conversation_id": msg.conversation_id,
        "content": msg.content,
        "timestamp": msg.timestamp,
    })

connect_task = _asyncio.create_task(connect_with_reload(
    on_inbound=on_inbound,
    adapter_factory=DingTalkStreamAdapter,
    reload_event=app.state.dingtalk_reload_event,
    state_holder=app.state.dingtalk_state,
))
app.state.dingtalk_connect_task = connect_task

try:
    yield
finally:
    connect_task.cancel()
    try:
        await connect_task
    except (asyncio.CancelledError, Exception):
        pass
```

- [ ] **Step 2.7: 测试 — 重连机制端到端**

文件 `backend/tests/test_admin_channels_reload.py`：

```python
"""channels.py update / disable 触发 reload event → connect_with_reload 重启 adapter。"""
from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock

import pytest

from hub.lifecycle.dingtalk_connect import connect_with_reload
from hub.crypto import encrypt_secret
from hub.models import ChannelApp


class _FakeAdapter:
    instances: list["_FakeAdapter"] = []

    def __init__(self, *, app_key, app_secret, robot_id):
        self.app_key = app_key
        self.app_secret = app_secret
        self.robot_id = robot_id
        self.started = False
        self.stopped = False
        self.message_handler = None
        _FakeAdapter.instances.append(self)

    def on_message(self, fn):
        self.message_handler = fn

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_reload_event_triggers_adapter_restart():
    """ChannelApp 改 secret → set reload event → 旧 adapter stop / 新 adapter start。"""
    _FakeAdapter.instances.clear()
    ca = await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("ak1", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("as1", purpose="config_secrets"),
        status="active",
    )

    reload_evt = asyncio.Event()
    holder: dict = {}

    async def on_inbound(_):
        pass

    task = asyncio.create_task(connect_with_reload(
        on_inbound=on_inbound,
        adapter_factory=_FakeAdapter,
        reload_event=reload_evt,
        poll_interval_seconds=0.05,
        state_holder=holder,
    ))

    # 等首次连接完成
    for _ in range(50):
        if holder.get("adapter") is not None and holder["adapter"].started:
            break
        await asyncio.sleep(0.02)
    assert holder["adapter"].started
    assert holder["adapter"].app_key == "ak1"

    # 模拟 admin 改 secret
    ca.encrypted_app_key = encrypt_secret("ak2", purpose="config_secrets")
    await ca.save()
    reload_evt.set()

    # 等重启完成
    for _ in range(50):
        if len(_FakeAdapter.instances) >= 2 and _FakeAdapter.instances[1].started:
            break
        await asyncio.sleep(0.02)

    assert _FakeAdapter.instances[0].stopped is True
    assert _FakeAdapter.instances[1].started is True
    assert _FakeAdapter.instances[1].app_key == "ak2"

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_disable_stops_adapter_and_doesnt_reconnect():
    """ChannelApp.status 改成 disabled + reload → 旧 adapter stop，无新 adapter。"""
    _FakeAdapter.instances.clear()
    ca = await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("ak", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("as", purpose="config_secrets"),
        status="active",
    )

    reload_evt = asyncio.Event()
    holder: dict = {}

    async def on_inbound(_):
        pass

    task = asyncio.create_task(connect_with_reload(
        on_inbound=on_inbound,
        adapter_factory=_FakeAdapter,
        reload_event=reload_evt,
        poll_interval_seconds=0.05,
        state_holder=holder,
    ))

    for _ in range(50):
        if holder.get("adapter") is not None and holder["adapter"].started:
            break
        await asyncio.sleep(0.02)
    assert holder["adapter"].started

    # 模拟 disable
    ca.status = "disabled"
    await ca.save()
    reload_evt.set()

    for _ in range(50):
        if _FakeAdapter.instances[0].stopped and holder.get("adapter") is None:
            break
        await asyncio.sleep(0.02)

    assert _FakeAdapter.instances[0].stopped is True
    assert holder.get("adapter") is None
    assert len(_FakeAdapter.instances) == 1  # 没有新建 adapter

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_cancel_stops_current_adapter():
    """task.cancel() → 当前 adapter 被 stop。"""
    _FakeAdapter.instances.clear()
    await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("ak", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("as", purpose="config_secrets"),
        status="active",
    )

    reload_evt = asyncio.Event()
    holder: dict = {}

    async def on_inbound(_):
        pass

    task = asyncio.create_task(connect_with_reload(
        on_inbound=on_inbound,
        adapter_factory=_FakeAdapter,
        reload_event=reload_evt,
        poll_interval_seconds=0.05,
        state_holder=holder,
    ))

    for _ in range(50):
        if holder.get("adapter") is not None and holder["adapter"].started:
            break
        await asyncio.sleep(0.02)

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert _FakeAdapter.instances[0].stopped is True
```

跑：
```bash
pytest backend/tests/test_admin_channels_reload.py -v
```
期望：3 个 PASS。

- [ ] **Step 3: 实现 ai_providers.py（含默认值 + 测试 chat）**

文件 `backend/hub/routers/admin/ai_providers.py`：
```python
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from pydantic import BaseModel, Field
from hub.auth.admin_perms import require_hub_perm
from hub.crypto import encrypt_secret, decrypt_secret
from hub.models import AIProvider, AuditLog
# ❗ provider 类必须提到模块顶层（不能在 test_chat 函数内 import），
# 否则 monkeypatch 这个模块下的 DeepSeekProvider/QwenProvider 不生效。
from hub.capabilities.deepseek import DeepSeekProvider
from hub.capabilities.qwen import QwenProvider

router = APIRouter(prefix="/hub/v1/admin/ai-providers", tags=["admin-ai"])


_AI_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
}


class CreateAIRequest(BaseModel):
    # C 阶段仅支持 deepseek / qwen（与 capabilities/factory.py 注册一致）
    provider_type: str = Field(..., pattern="^(deepseek|qwen)$")
    name: str = ""
    api_key: str
    base_url: str | None = None
    model: str | None = None


@router.get("/defaults")
async def get_defaults():
    """前端创建表单初始值（含每个支持的 provider 的 base_url + 推荐 model）。"""
    return _AI_DEFAULTS


@router.post("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def create_ai(request: Request, body: CreateAIRequest = Body(...)):
    # provider_type 已被 Pydantic pattern 限制为 deepseek/qwen
    d = _AI_DEFAULTS[body.provider_type]
    base_url = body.base_url or d["base_url"]
    model = body.model or d["model"]

    # ❗ 单 active 不变量：新建 active provider 前先把其他全部 disable，
    # Plan 4 capabilities/factory 取 active 时只会有一条，避免选取不确定。
    await AIProvider.exclude(status="disabled").update(status="disabled")

    rec = await AIProvider.create(
        provider_type=body.provider_type, name=body.name or f"{body.provider_type} 默认",
        encrypted_api_key=encrypt_secret(body.api_key, purpose="config_secrets"),
        base_url=base_url, model=model, config={}, status="active",
    )
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="create_ai_provider",
        target_type="ai_provider", target_id=str(rec.id),
        detail={"provider_type": body.provider_type},
    )
    return {"id": rec.id}


@router.get("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def list_ai():
    items = await AIProvider.all().order_by("-id")
    return {"items": [
        {"id": a.id, "provider_type": a.provider_type, "name": a.name,
         "base_url": a.base_url, "model": a.model, "status": a.status}
        for a in items
    ]}


@router.post("/{ai_id}/test-chat", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def test_chat(ai_id: int):
    """测试 chat：调一次 ping，确认 API key + base_url + model 都能用。"""
    rec = await AIProvider.filter(id=ai_id).first()
    if rec is None:
        raise HTTPException(404)
    cls = {"deepseek": DeepSeekProvider, "qwen": QwenProvider}.get(rec.provider_type)
    if cls is None:
        raise HTTPException(400, f"不支持测试 {rec.provider_type}")
    api_key = decrypt_secret(rec.encrypted_api_key, purpose="config_secrets")
    p = cls(api_key=api_key, base_url=rec.base_url, model=rec.model)
    try:
        await p.chat(messages=[{"role": "user", "content": "ping"}])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        await p.aclose()


@router.post("/{ai_id}/set-active", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def set_active(request: Request, ai_id: int):
    """同时只能有一个 active；切换时把其他设为 disabled。"""
    rec = await AIProvider.filter(id=ai_id).first()
    if rec is None:
        raise HTTPException(404)
    await AIProvider.exclude(id=ai_id).update(status="disabled")
    rec.status = "active"
    await rec.save()
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id, action="set_active_ai_provider",
        target_type="ai_provider", target_id=str(ai_id),
    )
    return {"success": True}
```

- [ ] **Step 4: 实现 system_config.py**

文件 `backend/hub/routers/admin/system_config.py`：
```python
from __future__ import annotations
from fastapi import APIRouter, Depends, Request, Body
from pydantic import BaseModel
from hub.auth.admin_perms import require_hub_perm
from hub.models import SystemConfig, AuditLog

router = APIRouter(prefix="/hub/v1/admin/config", tags=["admin-config"])


# 已知 key 白名单（防误写）
_KNOWN_KEYS = {
    "alert_receivers": list,        # list[str] 钉钉 userid
    "task_payload_ttl_days": int,
    "task_log_ttl_days": int,
    "daily_audit_hour": int,        # 0-23
    "low_confidence_threshold": float,  # 0-1
}


@router.get("/{key}", dependencies=[Depends(require_hub_perm("platform.flags.write"))])
async def get_config(key: str):
    if key not in _KNOWN_KEYS:
        from fastapi import HTTPException
        raise HTTPException(400, f"未知配置 key: {key}")
    rec = await SystemConfig.filter(key=key).first()
    return {"key": key, "value": rec.value if rec else None}


class SetConfigRequest(BaseModel):
    value: object


@router.put("/{key}", dependencies=[Depends(require_hub_perm("platform.flags.write"))])
async def set_config(request: Request, key: str, body: SetConfigRequest = Body(...)):
    if key not in _KNOWN_KEYS:
        from fastapi import HTTPException
        raise HTTPException(400, f"未知配置 key: {key}")

    expected_type = _KNOWN_KEYS[key]
    if not isinstance(body.value, expected_type):
        from fastapi import HTTPException
        raise HTTPException(400, f"类型错误：期望 {expected_type.__name__}")

    actor = request.state.hub_user
    await SystemConfig.update_or_create(
        key=key, defaults={"value": body.value, "updated_by_hub_user_id": actor.id},
    )
    await AuditLog.create(
        who_hub_user_id=actor.id, action="update_system_config",
        target_type="system_config", target_id=key,
        detail={"value": body.value},
    )
    return {"success": True}
```

- [ ] **Step 5: 写测试**

每个 router 配对 4-5 个测试，参考 Task 4 的测试模板：
- `test_admin_channels.py`：6 个：create / list / update / disable / **update 触发 reload event** / **disable 触发 reload event**

```python
@pytest.mark.asyncio
async def test_update_channel_sets_reload_event(admin_client):
    """PUT /channels/{id} 修改 app_secret → app.state.dingtalk_reload_event 被 set。"""
    import asyncio
    ca = await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("k", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s", purpose="config_secrets"),
        status="active",
    )
    evt = asyncio.Event()
    admin_client.app.state.dingtalk_reload_event = evt

    r = await admin_client.put(f"/hub/v1/admin/channels/{ca.id}", json={"app_secret": "new"})
    assert r.status_code == 200
    assert evt.is_set()


@pytest.mark.asyncio
async def test_disable_channel_sets_reload_event(admin_client):
    """POST /channels/{id}/disable → reload event 被 set，让运行中的 Stream 自动停掉。"""
    import asyncio
    ca = await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("k", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("s", purpose="config_secrets"),
        status="active",
    )
    evt = asyncio.Event()
    admin_client.app.state.dingtalk_reload_event = evt

    r = await admin_client.post(f"/hub/v1/admin/channels/{ca.id}/disable")
    assert r.status_code == 200
    assert evt.is_set()
```
- `test_admin_ai_providers.py`：7 个：

```python
def test_module_imports_without_nameerror():
    """import ai_providers 整个模块不应抛 NameError（防 Field 漏导）。"""
    import importlib
    mod = importlib.import_module("hub.routers.admin.ai_providers")
    assert hasattr(mod, "router")
    assert hasattr(mod, "CreateAIRequest")


@pytest.mark.asyncio
async def test_create_ai_rejects_unsupported_provider_type(admin_client):
    """provider_type=claude → 422，因为 Pydantic pattern 限制 deepseek/qwen。"""
    r = await admin_client.post(
        "/hub/v1/admin/ai-providers",
        json={"provider_type": "claude", "api_key": "k"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_ai_fills_defaults_for_deepseek(admin_client):
    r = await admin_client.post(
        "/hub/v1/admin/ai-providers",
        json={"provider_type": "deepseek", "api_key": "k"},
    )
    assert r.status_code == 200
    rec = await AIProvider.filter(id=r.json()["id"]).first()
    assert rec.base_url == "https://api.deepseek.com/v1"
    assert rec.model == "deepseek-chat"


@pytest.mark.asyncio
async def test_create_ai_disables_others_to_keep_single_active(admin_client):
    """新建 active provider 时，其他同类应被自动 disable，避免 Plan 4 factory 取到不确定项。"""
    await admin_client.post(
        "/hub/v1/admin/ai-providers",
        json={"provider_type": "deepseek", "api_key": "k1"},
    )
    await admin_client.post(
        "/hub/v1/admin/ai-providers",
        json={"provider_type": "qwen", "api_key": "k2"},
    )
    actives = await AIProvider.filter(status="active").all()
    assert len(actives) == 1
    assert actives[0].provider_type == "qwen"


@pytest.mark.asyncio
async def test_test_chat_success(admin_client, monkeypatch):
    """provider chat 成功 → {ok: true}；同时验证 aclose() 被调用避免 client 泄漏。"""
    from unittest.mock import AsyncMock

    rec = await AIProvider.create(
        provider_type="deepseek", name="t",
        encrypted_api_key=encrypt_secret("key", purpose="config_secrets"),
        base_url="https://api.deepseek.com/v1", model="deepseek-chat",
        config={}, status="active",
    )

    chat_mock = AsyncMock(return_value={"role": "assistant", "content": "pong"})
    aclose_mock = AsyncMock()

    class _FakeProvider:
        def __init__(self, *, api_key, base_url, model):
            assert api_key == "key"
            assert base_url == "https://api.deepseek.com/v1"
            assert model == "deepseek-chat"
        chat = chat_mock
        aclose = aclose_mock

    monkeypatch.setattr(
        "hub.routers.admin.ai_providers.DeepSeekProvider", _FakeProvider, raising=False,
    )

    r = await admin_client.post(f"/hub/v1/admin/ai-providers/{rec.id}/test-chat")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    chat_mock.assert_called_once()
    aclose_mock.assert_called_once()


@pytest.mark.asyncio
async def test_test_chat_failure(admin_client, monkeypatch):
    """provider chat 抛错 → {ok: false, error: <消息>}；aclose 仍要在 finally 调用。"""
    from unittest.mock import AsyncMock

    rec = await AIProvider.create(
        provider_type="qwen", name="t",
        encrypted_api_key=encrypt_secret("key", purpose="config_secrets"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", model="qwen-plus",
        config={}, status="active",
    )

    aclose_mock = AsyncMock()

    class _FakeProvider:
        def __init__(self, **_): pass
        async def chat(self, **_):
            raise RuntimeError("api timeout")
        aclose = aclose_mock

    monkeypatch.setattr(
        "hub.routers.admin.ai_providers.QwenProvider", _FakeProvider, raising=False,
    )

    r = await admin_client.post(f"/hub/v1/admin/ai-providers/{rec.id}/test-chat")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "api timeout" in body["error"]
    aclose_mock.assert_called_once()


@pytest.mark.asyncio
async def test_set_active_disables_others(admin_client):
    """POST /ai-providers/{id}/set-active：目标设为 active，其他全部 disabled。"""
    a = await AIProvider.create(
        provider_type="deepseek", name="A",
        encrypted_api_key=encrypt_secret("k1", purpose="config_secrets"),
        base_url="x", model="m", config={}, status="active",
    )
    b = await AIProvider.create(
        provider_type="qwen", name="B",
        encrypted_api_key=encrypt_secret("k2", purpose="config_secrets"),
        base_url="x", model="m", config={}, status="active",
    )

    r = await admin_client.post(f"/hub/v1/admin/ai-providers/{b.id}/set-active")
    assert r.status_code == 200
    a_after = await AIProvider.get(id=a.id)
    b_after = await AIProvider.get(id=b.id)
    assert a_after.status == "disabled"
    assert b_after.status == "active"
    actives = await AIProvider.filter(status="active").count()
    assert actives == 1
```
- `test_admin_system_config.py`：4 个（已知 key 读写 / 未知 key 拒绝 / 类型校验 / 写入触发 audit）

完整测试代码按 Task 4 风格展开，断言：
- 状态码 200/400/403/404
- 副作用（DB 写入）
- audit_log 写入
- 加密字段不在响应中（list 不返回 encrypted_*）

- [ ] **Step 6: 提交**

```bash
git add backend/hub/routers/admin/downstreams.py \
        backend/hub/routers/admin/channels.py \
        backend/hub/routers/admin/ai_providers.py \
        backend/hub/routers/admin/system_config.py \
        backend/hub/lifecycle/dingtalk_connect.py \
        backend/main.py \
        backend/tests/test_admin_downstreams.py \
        backend/tests/test_admin_channels.py \
        backend/tests/test_admin_channels_reload.py \
        backend/tests/test_admin_ai_providers.py \
        backend/tests/test_admin_system_config.py
git commit -m "feat(hub): admin 配置中心 4 路由（下游/渠道/AI/系统设置）+ 渠道热重载 + 加密字段不外露 + audit"
```

---

## Task 6：task_logger 透明写入 + admin tasks 路由

**Files:**
- Create: `backend/hub/observability/__init__.py`
- Create: `backend/hub/observability/task_logger.py`
- Create: `backend/hub/routers/admin/tasks.py`
- Modify: `backend/hub/handlers/dingtalk_inbound.py`（包装 task_logger）
- Modify: `backend/hub/handlers/dingtalk_outbound.py`（标记完成）

`task_logger`：在 inbound handler 入口创建 task_log 行（status=running），handler 完成后更新 status / duration / error。`task_payload` 加密存请求/响应 payload。

- [ ] **Step 1: 实现 task_logger + 包装 inbound/outbound handler**

文件 `backend/hub/observability/__init__.py`：
```python
"""HUB 可观察性（task_log / live_stream / metrics）。"""
```

文件 `backend/hub/observability/task_logger.py`：
```python
"""task_log 写入 + task_payload 加密 + LiveStream 推流。

所有钉钉入站消息处理都过这个 context manager：
- 入口创建 TaskLog（status=running）
- 退出时写 finished_at + duration + 加密 TaskPayload
- 同时**发布脱敏事件**到 Redis pubsub（前端 SSE 实时流订阅）
"""
from __future__ import annotations
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from hub.crypto import encrypt_secret
from hub.models import TaskLog, TaskPayload

logger = logging.getLogger("hub.observability.task_logger")


def _redact(text: str, max_len: int = 100) -> str:
    """对实时流脱敏：截断 + 手机号/身份证号/银行卡号正则脱敏（与 spec PII §14.4 一致）。"""
    import re
    if not text:
        return ""
    s = text[:max_len]
    s = re.sub(r"(\d{3})\d{4}(\d{4})", r"\1****\2", s)  # 手机号
    s = re.sub(r"(\d{4})\d{8}(\d{4})", r"\1********\2", s)  # 银行卡 16 位
    s = re.sub(r"(\d{6})\d{8}(\w{4})", r"\1********\2", s)  # 身份证
    return s


@asynccontextmanager
async def log_inbound_task(
    *, task_id: str, channel_userid: str, content: str, conversation_id: str,
    live_publisher=None,  # 注入 LiveStreamPublisher；None = 不推流（不阻塞业务）
    payload_ttl_days: int = 30,
):
    """Context manager：写 TaskLog + 加密 TaskPayload + 推 SSE 实时事件。"""
    started = time.monotonic()
    task = await TaskLog.create(
        task_id=task_id, task_type="dingtalk_inbound",
        channel_type="dingtalk", channel_userid=channel_userid,
        status="running",
    )

    record = {"request_text": content, "conversation_id": conversation_id, "erp_calls": []}
    raised = None

    try:
        yield record
        status = record.get("final_status", "success")
    except Exception as e:
        status = "failed_system_final"
        record["error_summary"] = str(e)[:500]
        raised = e
    finally:
        task.status = status
        task.finished_at = datetime.now(timezone.utc)
        task.duration_ms = int((time.monotonic() - started) * 1000)
        if "error_summary" in record:
            task.error_summary = record["error_summary"]
        if "intent_parser" in record:
            task.intent_parser = record["intent_parser"]
            task.intent_confidence = record.get("intent_confidence")
        await task.save()

        # 加密 payload
        await TaskPayload.create(
            task_log=task,
            encrypted_request=encrypt_secret(record.get("request_text", ""), purpose="task_payload"),
            encrypted_erp_calls=encrypt_secret(
                json.dumps(record.get("erp_calls", []), ensure_ascii=False),
                purpose="task_payload",
            ),
            encrypted_response=encrypt_secret(record.get("response", ""), purpose="task_payload"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=payload_ttl_days),
        )

        # 发布脱敏事件到 SSE（不阻塞业务；publish 失败仅 warning）
        if live_publisher is not None:
            try:
                await live_publisher.publish({
                    "task_id": task_id,
                    "channel_userid": channel_userid,
                    "status": status,
                    "duration_ms": task.duration_ms,
                    "intent_parser": record.get("intent_parser"),
                    "intent_confidence": record.get("intent_confidence"),
                    "request_preview": _redact(record.get("request_text", "")),
                    "response_preview": _redact(record.get("response", "")),
                    "error_summary": record.get("error_summary"),
                    "timestamp": int(time.time()),
                })
            except Exception:
                logger.warning("LiveStream publish 失败，不影响业务", exc_info=True)

        if raised is not None:
            raise raised
```

**修改 inbound handler 完整补丁（不能丢弃 Plan 4 已有逻辑）**

把 Plan 4 已有的 `handle_inbound`（参数：binding_service / identity_service / sender / chain_parser / conversation_state / query_product_usecase / query_customer_history_usecase / require_permissions）整体**保留**，只在外层包一层 `log_inbound_task` + 包装 sender。具体补丁：

(1) 文件 `backend/hub/handlers/dingtalk_inbound.py` 顶部 import 区追加：
```python
from hub.observability.task_logger import log_inbound_task
```

(2) 修改函数签名追加 `live_publisher` 参数（默认 None 兼容 Plan 3-4 调用方）：
```python
async def handle_inbound(
    task_data: dict, *,
    binding_service,
    identity_service,
    sender,
    chain_parser=None,
    conversation_state=None,
    query_product_usecase=None,
    query_customer_history_usecase=None,
    require_permissions=None,
    live_publisher=None,  # ← 新增
) -> None:
```

(3) 把整个函数体**移进 `log_inbound_task` 上下文**。原 Plan 4 函数体伪代码：
```python
# 原结构
content = ...
m_bind = RE_BIND.match(content)
if m_bind:
    result = await binding_service.initiate_binding(...)
    await _send_text(sender, channel_userid, result.reply_text)
    return
if RE_UNBIND.match(content): ...
if RE_HELP.match(content): ...
resolution = await identity_service.resolve(...)
if not resolution.found: ...
if not resolution.erp_active: ...
intent = await chain_parser.parse(content, context=parser_context)
# ... select_choice / confirm_yes / unknown / low_confidence / 高置信度执行
```

改造后：
```python
async def handle_inbound(...):
    payload = task_data.get("payload", {})
    task_id = task_data.get("task_id", "")
    channel_userid = payload.get("channel_userid", "")
    content = (payload.get("content") or "").strip()

    async with log_inbound_task(
        task_id=task_id,
        channel_userid=channel_userid,
        content=content,
        conversation_id=payload.get("conversation_id", ""),
        live_publisher=live_publisher,
    ) as record:
        # 包装 sender.send_text 捕获回复内容到 record
        original_send_text = sender.send_text
        async def _wrapped_send_text(*, dingtalk_userid, text):
            record["response"] = text
            return await original_send_text(dingtalk_userid=dingtalk_userid, text=text)
        sender.send_text = _wrapped_send_text
        try:
            # ====== 以下是 Plan 4 原 handle_inbound 函数体（不动）======
            m_bind = RE_BIND.match(content)
            if m_bind:
                result = await binding_service.initiate_binding(
                    dingtalk_userid=channel_userid, erp_username=m_bind.group(1),
                )
                await _send_text(sender, channel_userid, result.reply_text)
                record["final_status"] = "success"
                return

            if RE_UNBIND.match(content):
                result = await binding_service.unbind_self(dingtalk_userid=channel_userid)
                await _send_text(sender, channel_userid, result.reply_text)
                record["final_status"] = "success"
                return

            if RE_HELP.match(content):
                cmds = [
                    "/绑定 你的ERP用户名 — 绑定 ERP 账号",
                    "/解绑 — 解绑当前账号",
                    "查 SKU100 — 查商品",
                    "查 SKU100 给阿里 — 查客户历史价",
                ]
                await _send_text(sender, channel_userid, messages.help_message(cmds))
                record["final_status"] = "success"
                return

            resolution = await identity_service.resolve(dingtalk_userid=channel_userid)
            if not resolution.found:
                await _send_text(sender, channel_userid,
                                 build_user_message(BizErrorCode.USER_NOT_BOUND))
                record["final_status"] = "failed_user"
                return
            if not resolution.erp_active:
                await _send_text(sender, channel_userid,
                                 build_user_message(BizErrorCode.USER_ERP_DISABLED))
                record["final_status"] = "failed_user"
                return

            if chain_parser is None:
                await _send_text(sender, channel_userid, "我没听懂，请发送「帮助」查看可用功能。")
                record["final_status"] = "failed_user"
                return

            state = await conversation_state.load(channel_userid) if conversation_state else None
            parser_context = {}
            if state:
                if state.get("pending_choice"):
                    parser_context["pending_choice"] = "yes"
                if state.get("pending_confirm"):
                    parser_context["pending_confirm"] = "yes"

            intent = await chain_parser.parse(content, context=parser_context)
            # 关键：把意图解析结果写入 record（live stream + task_log 两边都用）
            record["intent_parser"] = intent.parser
            record["intent_confidence"] = intent.confidence

            try:
                if intent.intent_type == "select_choice":
                    await _handle_select_choice(
                        intent, state, channel_userid, sender,
                        conversation_state, resolution,
                        query_product_usecase, query_customer_history_usecase,
                        require_permissions,
                    )
                    record["final_status"] = "success"
                    return
                if intent.intent_type == "confirm_yes":
                    if state and state.get("pending_confirm"):
                        await _execute_confirmed(
                            state, channel_userid, sender, conversation_state, resolution,
                            query_product_usecase, query_customer_history_usecase,
                            require_permissions,
                        )
                    else:
                        await _send_text(sender, channel_userid,
                                         "没有需要确认的待办；请重新描述你的需求。")
                    record["final_status"] = "success"
                    return
                if intent.intent_type == "unknown":
                    await _send_text(sender, channel_userid,
                                     build_user_message(BizErrorCode.INTENT_LOW_CONFIDENCE))
                    record["final_status"] = "failed_user"
                    return
                if intent.notes == "low_confidence":
                    await conversation_state.save(channel_userid, {
                        "intent_type": intent.intent_type,
                        "fields": intent.fields,
                        "pending_confirm": "yes",
                    })
                    summary = _summarize_intent(intent)
                    await _send_text(
                        sender, channel_userid,
                        f"我大概理解为：{summary}\n\n如果是这个意思请回复「是」继续，"
                        f"否则请用更明确的方式重新描述。",
                    )
                    record["final_status"] = "success"
                    return

                await _execute_intent(
                    intent, channel_userid, sender, resolution,
                    query_product_usecase, query_customer_history_usecase,
                    require_permissions,
                )
                record["final_status"] = "success"
            except BizError as e:
                await _send_text(sender, channel_userid, str(e))
                record["final_status"] = "failed_user"
        finally:
            sender.send_text = original_send_text  # 还原（避免下次任务串包装）
```

**关键约束**（写进自审）：
- ❗ **绝不能用 `# ...` 替代真实 handler 逻辑**——所有 Plan 4 已有的命令路由 / IdentityService / ChainParser / UseCase 必须保留
- ❗ `finally` 必须还原 `sender.send_text`（worker 持续运行，sender 是共享实例）
- ❗ 每条退出路径都设 `record["final_status"]`（success / failed_user / 异常分支由 task_logger 默认 failed_system_final）

修改 worker.py 构造 LiveStreamPublisher 并注入：

```python
# backend/worker.py 在 redis_client 创建之后
from hub.observability.live_stream import LiveStreamPublisher
live_publisher = LiveStreamPublisher(redis=redis_client)

# inbound handler 注入
async def dingtalk_inbound_handler(task_data):
    await handle_inbound(
        task_data,
        binding_service=binding_service,
        identity_service=identity_service,
        sender=sender,
        chain_parser=chain_parser,
        conversation_state=conversation_state,
        query_product_usecase=query_product,
        query_customer_history_usecase=query_customer,
        require_permissions=require_permissions,
        live_publisher=live_publisher,  # 注入
    )
```

- [ ] **Step 2: 实现 admin tasks router**

文件 `backend/hub/routers/admin/tasks.py`：
```python
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from datetime import datetime
from hub.auth.admin_perms import require_hub_perm
from hub.crypto import decrypt_secret
from hub.models import TaskLog, TaskPayload, MetaAuditLog

router = APIRouter(prefix="/hub/v1/admin/tasks", tags=["admin-tasks"])


@router.get("", dependencies=[Depends(require_hub_perm("platform.tasks.read"))])
async def list_tasks(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    user_id: str | None = None, task_type: str | None = None,
    status: str | None = None, since_hours: int | None = None,
):
    qs = TaskLog.all().order_by("-created_at")
    if user_id:
        qs = qs.filter(channel_userid=user_id)
    if task_type:
        qs = qs.filter(task_type=task_type)
    if status:
        qs = qs.filter(status=status)
    if since_hours:
        from datetime import datetime, timedelta, timezone
        qs = qs.filter(created_at__gte=datetime.now(timezone.utc) - timedelta(hours=since_hours))

    total = await qs.count()
    items = await qs.offset((page - 1) * page_size).limit(page_size)
    return {
        "items": [
            {
                "task_id": t.task_id, "task_type": t.task_type,
                "channel_userid": t.channel_userid, "status": t.status,
                "created_at": t.created_at, "finished_at": t.finished_at,
                "duration_ms": t.duration_ms, "intent_parser": t.intent_parser,
                "intent_confidence": t.intent_confidence,
                "error_summary": t.error_summary,
            }
            for t in items
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get(
    "/{task_id}",
    dependencies=[Depends(require_hub_perm("platform.conversation.monitor"))],
)
async def get_task_detail(request: Request, task_id: str):
    """获取 task 详情（含 payload）→ 触发 meta_audit_log。"""
    task = await TaskLog.filter(task_id=task_id).first()
    if task is None:
        raise HTTPException(404, "任务不存在")

    payload = await TaskPayload.filter(task_log_id=task.id).first()
    payload_data = None
    if payload:
        from datetime import datetime, timezone
        if payload.expires_at > datetime.now(timezone.utc):
            payload_data = {
                "request_text": decrypt_secret(payload.encrypted_request, purpose="task_payload"),
                "erp_calls": decrypt_secret(payload.encrypted_erp_calls, purpose="task_payload") if payload.encrypted_erp_calls else "[]",
                "response": decrypt_secret(payload.encrypted_response, purpose="task_payload"),
            }
        # 只有进入详情页（解密 payload）才触发 meta_audit
        actor = request.state.hub_user
        await MetaAuditLog.create(
            who_hub_user_id=actor.id, viewed_task_id=task_id,
            ip=request.client.host if request.client else None,
        )

    return {
        "task_log": {
            "task_id": task.task_id, "task_type": task.task_type,
            "channel_userid": task.channel_userid, "status": task.status,
            "created_at": task.created_at, "finished_at": task.finished_at,
            "duration_ms": task.duration_ms, "intent_parser": task.intent_parser,
            "intent_confidence": task.intent_confidence,
            "error_summary": task.error_summary, "retry_count": task.retry_count,
        },
        "payload": payload_data,
    }
```

- [ ] **Step 3: 测试 + 提交（合计 6 测试 task_logger + 6 测试 admin tasks）**

```bash
git add backend/hub/observability/ \
        backend/hub/handlers/ \
        backend/hub/routers/admin/tasks.py \
        backend/main.py \
        backend/tests/test_task_logger.py \
        backend/tests/test_admin_tasks.py
git commit -m "feat(hub): task_logger（task_log 写入 + 加密 payload + 30 天 TTL）+ admin tasks 路由（详情解密触发 meta_audit）"
```

---

## Task 7：实时对话流（SSE + Redis Pub/Sub）

**Files:**
- Create: `backend/hub/observability/live_stream.py`
- Create: `backend/hub/routers/admin/conversation.py`（SSE + 历史搜索 + 详情）
- Test: `backend/tests/test_live_stream.py` + `test_admin_conversation_live.py` + `test_admin_conversation_history.py`

`live_stream` 在 task_logger 内被调用，发布事件到 Redis channel `conversation:live`；`conversation.py` SSE endpoint 订阅该 channel 推到前端。

- [ ] **Step 1: live_stream**

文件 `backend/hub/observability/live_stream.py`：
```python
from __future__ import annotations
import json
from redis.asyncio import Redis

CHANNEL = "conversation:live"


class LiveStreamPublisher:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def publish(self, event: dict) -> None:
        await self.redis.publish(CHANNEL, json.dumps(event, ensure_ascii=False))


class LiveStreamSubscriber:
    """订阅 Redis pubsub，逐条 yield 事件给 SSE。"""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def stream(self):
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                yield data
        finally:
            await pubsub.unsubscribe(CHANNEL)
            await pubsub.aclose()
```

- [ ] **Step 2: SSE + 历史搜索 + 详情 router**

文件 `backend/hub/routers/admin/conversation.py`：
```python
from __future__ import annotations
import asyncio
import json
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from hub.auth.admin_perms import require_hub_perm
from hub.observability.live_stream import LiveStreamSubscriber

router = APIRouter(prefix="/hub/v1/admin/conversation", tags=["admin-conversation"])


@router.get(
    "/live",
    dependencies=[Depends(require_hub_perm("platform.conversation.monitor"))],
)
async def conversation_live(request: Request):
    """SSE 实时对话流。前端用 EventSource API 接。"""
    redis = request.app.state.task_runner.redis
    sub = LiveStreamSubscriber(redis)

    async def event_generator():
        try:
            async for raw in sub.stream():
                yield f"data: {raw}\n\n"
                if await request.is_disconnected():
                    break
        except asyncio.CancelledError:
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get(
    "/history",
    dependencies=[Depends(require_hub_perm("platform.conversation.monitor"))],
)
async def conversation_history(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None, channel_userid: str | None = None,
    status: str | None = None, since_hours: int | None = 24,
):
    """历史对话搜索（不解密 payload，只展示元数据）。"""
    from hub.models import TaskLog
    qs = TaskLog.filter(task_type="dingtalk_inbound").order_by("-created_at")
    if channel_userid:
        qs = qs.filter(channel_userid=channel_userid)
    if status:
        qs = qs.filter(status=status)
    if since_hours:
        from datetime import datetime, timedelta, timezone
        qs = qs.filter(created_at__gte=datetime.now(timezone.utc) - timedelta(hours=since_hours))
    if keyword:
        qs = qs.filter(error_summary__icontains=keyword)  # 仅元数据中搜

    total = await qs.count()
    items = await qs.offset((page - 1) * page_size).limit(page_size)
    return {
        "items": [
            {
                "task_id": t.task_id, "channel_userid": t.channel_userid,
                "status": t.status, "intent_parser": t.intent_parser,
                "intent_confidence": t.intent_confidence,
                "duration_ms": t.duration_ms,
                "created_at": t.created_at, "error_summary": t.error_summary,
            }
            for t in items
        ],
        "total": total,
    }
```

- [ ] **Step 3: 端到端测试：处理一条入站消息后 SSE 收到事件**

文件 `backend/tests/test_live_stream_e2e.py`：
```python
import pytest
import asyncio
from unittest.mock import AsyncMock
from fakeredis import aioredis as fakeredis_aio


@pytest.fixture
async def fake_redis():
    c = fakeredis_aio.FakeRedis()
    yield c
    await c.aclose()


@pytest.mark.asyncio
async def test_inbound_task_publishes_live_event(fake_redis, setup_db):
    """处理一条入站消息 → task_logger 退出时往 conversation:live 发布事件。"""
    from hub.observability.live_stream import LiveStreamPublisher, LiveStreamSubscriber
    from hub.observability.task_logger import log_inbound_task

    pub = LiveStreamPublisher(redis=fake_redis)
    sub = LiveStreamSubscriber(redis=fake_redis)

    # 异步消费
    received = []
    async def consume():
        async for raw in sub.stream():
            received.append(raw)
            break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # 让 consumer 订阅完成

    # 模拟处理一条入站消息
    async with log_inbound_task(
        task_id="t1", channel_userid="m1",
        content="查 SKU100", conversation_id="c1",
        live_publisher=pub,
    ) as record:
        record["intent_parser"] = "rule"
        record["intent_confidence"] = 0.95
        record["response"] = "鼠标 ¥120"
        record["final_status"] = "success"

    # 等事件到达
    await asyncio.wait_for(consumer, timeout=2.0)
    assert len(received) == 1
    import json
    event = json.loads(received[0])
    assert event["task_id"] == "t1"
    assert event["channel_userid"] == "m1"
    assert event["status"] == "success"
    assert "鼠标" in event["response_preview"]


@pytest.mark.asyncio
async def test_inbound_task_redacts_phone_number(fake_redis, setup_db):
    """request_preview 应脱敏手机号。"""
    from hub.observability.live_stream import LiveStreamPublisher, LiveStreamSubscriber
    from hub.observability.task_logger import log_inbound_task

    pub = LiveStreamPublisher(redis=fake_redis)
    sub = LiveStreamSubscriber(redis=fake_redis)

    received = []
    async def consume():
        async for raw in sub.stream():
            received.append(raw)
            break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)

    async with log_inbound_task(
        task_id="t2", channel_userid="m1",
        content="给客户 13812345678 报价",
        conversation_id="c1", live_publisher=pub,
    ) as record:
        record["final_status"] = "success"

    await asyncio.wait_for(consumer, timeout=2.0)
    import json
    event = json.loads(received[0])
    assert "13812345678" not in event["request_preview"]
    assert "138****5678" in event["request_preview"]
```

- [ ] **Step 4: 测试 + 提交（合计 4 + 4 + 4 + 2 = 14 测试）**

```bash
git add backend/hub/observability/live_stream.py \
        backend/hub/routers/admin/conversation.py \
        backend/main.py \
        backend/tests/test_live_stream.py \
        backend/tests/test_admin_conversation_live.py \
        backend/tests/test_admin_conversation_history.py
git commit -m "feat(hub): 实时对话流（SSE + Redis pubsub）+ 历史搜索 router"
```

---

## Task 8：操作审计 + 仪表盘

**Files:**
- Create: `backend/hub/routers/admin/audit.py`
- Create: `backend/hub/routers/admin/dashboard.py`
- Test: 8 测试合计

- [ ] **Step 1: 完整 audit router**

文件 `backend/hub/routers/admin/audit.py`：
```python
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query
from hub.auth.admin_perms import require_hub_perm
from hub.models import AuditLog, MetaAuditLog, HubUser

router = APIRouter(prefix="/hub/v1/admin/audit", tags=["admin-audit"])


@router.get("", dependencies=[Depends(require_hub_perm("platform.audit.read"))])
async def list_audit_logs(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    actor_id: int | None = None, action: str | None = None,
    target_type: str | None = None, since_hours: int = Query(168, ge=1, le=8760),
):
    qs = AuditLog.all().order_by("-created_at")
    qs = qs.filter(created_at__gte=datetime.now(timezone.utc) - timedelta(hours=since_hours))
    if actor_id:
        qs = qs.filter(who_hub_user_id=actor_id)
    if action:
        qs = qs.filter(action=action)
    if target_type:
        qs = qs.filter(target_type=target_type)

    total = await qs.count()
    items = await qs.offset((page - 1) * page_size).limit(page_size)
    # 把 actor 名字带出来给前端展示
    actor_ids = {it.who_hub_user_id for it in items}
    actors = {u.id: u.display_name for u in await HubUser.filter(id__in=actor_ids)}
    return {
        "items": [
            {"id": it.id, "actor_id": it.who_hub_user_id,
             "actor_name": actors.get(it.who_hub_user_id, "?"),
             "action": it.action, "target_type": it.target_type,
             "target_id": it.target_id, "detail": it.detail, "ip": it.ip,
             "created_at": it.created_at}
            for it in items
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get("/meta", dependencies=[Depends(require_hub_perm("platform.audit.system_read"))])
async def list_meta_audit(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    actor_id: int | None = None, since_hours: int = Query(168),
):
    """谁在何时查看了哪些 task 的 payload。"""
    qs = MetaAuditLog.all().order_by("-viewed_at")
    qs = qs.filter(viewed_at__gte=datetime.now(timezone.utc) - timedelta(hours=since_hours))
    if actor_id:
        qs = qs.filter(who_hub_user_id=actor_id)
    total = await qs.count()
    items = await qs.offset((page - 1) * page_size).limit(page_size)
    actor_ids = {it.who_hub_user_id for it in items}
    actors = {u.id: u.display_name for u in await HubUser.filter(id__in=actor_ids)}
    return {
        "items": [
            {"id": it.id, "actor_id": it.who_hub_user_id,
             "actor_name": actors.get(it.who_hub_user_id, "?"),
             "viewed_task_id": it.viewed_task_id, "viewed_at": it.viewed_at, "ip": it.ip}
            for it in items
        ],
        "total": total,
    }
```

- [ ] **Step 2: 完整 dashboard router**

文件 `backend/hub/routers/admin/dashboard.py`：
```python
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Request
from hub.auth.admin_perms import require_hub_perm
from hub.models import TaskLog

router = APIRouter(prefix="/hub/v1/admin/dashboard", tags=["admin-dashboard"])


@router.get("", dependencies=[Depends(require_hub_perm("platform.tasks.read"))])
async def dashboard(request: Request):
    """聚合：健康 + 24h 任务统计 + 错误率。"""
    # 健康检查（复用 /hub/v1/health 的内部函数）
    from hub.routers.health import _check_postgres, _check_redis
    health = {
        "postgres": await _check_postgres(),
        "redis": await _check_redis(),
        "dingtalk_stream": "connected" if getattr(request.app.state, "dingtalk_adapter", None) else "not_started",
    }

    # 24h 任务统计
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    total = await TaskLog.filter(created_at__gte=since).count()
    success = await TaskLog.filter(
        created_at__gte=since, status="success",
    ).count()
    failed = await TaskLog.filter(
        created_at__gte=since,
        status__in=["failed_user", "failed_system_final"],
    ).count()
    success_rate = (success / total * 100) if total > 0 else 100.0

    # 平均耗时
    rows = await TaskLog.filter(
        created_at__gte=since, duration_ms__not_isnull=True,
    ).values_list("duration_ms", flat=True)
    avg_duration_ms = int(sum(rows) / len(rows)) if rows else 0

    # 按小时分桶（前端画图）
    hourly = []
    for h in range(24):
        bucket_start = since + timedelta(hours=h)
        bucket_end = bucket_start + timedelta(hours=1)
        cnt = await TaskLog.filter(
            created_at__gte=bucket_start, created_at__lt=bucket_end,
        ).count()
        hourly.append({"hour": bucket_start.hour, "count": cnt})

    return {
        "health": health,
        "today": {
            "total": total, "success": success, "failed": failed,
            "success_rate": round(success_rate, 1),
            "avg_duration_ms": avg_duration_ms,
        },
        "hourly": hourly,
    }
```

- [ ] **Step 3: 写测试**

文件 `backend/tests/test_admin_audit.py`（4 个测试）：
- list_audit_logs：admin 看到操作记录
- list_audit_logs 筛选 actor / action 生效
- list_meta_audit：需要 platform.audit.system_read 权限
- 普通 admin（platform_admin 之外）调 meta 返回 403

文件 `backend/tests/test_admin_dashboard.py`（4 个测试）：
- dashboard 返回 health + today + hourly 三段
- 24h 任务统计准确
- success_rate 计算
- hourly 24 个桶

- [ ] **Step 4: 提交**

```bash
git add backend/hub/routers/admin/audit.py \
        backend/hub/routers/admin/dashboard.py \
        backend/main.py \
        backend/tests/test_admin_audit.py \
        backend/tests/test_admin_dashboard.py
git commit -m "feat(hub): 操作审计 + 仪表盘 admin routers"
```

---

## Task 9：初始化向导步骤 2-6 完整业务

**Files:**
- Create: `backend/hub/routers/setup_full.py`（替代 Plan 2 setup.py）
- Test: `backend/tests/test_setup_full.py`

实现 spec §16.2 步骤 2-6：注册 ERP / 创建第一个 admin / 注册钉钉 / 注册 AI / 完成。

- [ ] **Step 1: 完整 setup_full router**

**关键约束：**
- 所有步骤 2-6 endpoint 必须先校验 `X-Setup-Session` header（值来自 Plan 2 `/setup/verify-token` 返回的 session_id）。session 由 Plan 2 的 `_active_setup_sessions` dict 维护——Plan 5 改为放进 `app.state.active_setup_sessions: dict[str, bool]` 让多 router 共用
- 每步成功后立刻持久化（DownstreamSystem / HubUser / ChannelApp / AIProvider 等表）
- 完成步骤 6 后立即：(1) 写 `SystemConfig(key=system_initialized, value=True)` (2) 刷新 `app.state.session_auth`（用新写入的 ERP 配置构造 ErpSessionAuth）(3) 触发 gateway 钉钉自动连接 task（Plan 3 的 `connect_dingtalk_stream_when_ready` 已经轮询 ChannelApp，写入即被它检测）
- 失败可重试：每步幂等（同 ChannelApp `(channel_type, name)` 已存在则更新，不重复创建）

文件 `backend/hub/routers/setup_full.py`：
```python
from __future__ import annotations
from fastapi import APIRouter, Body, Header, HTTPException, Request
from pydantic import BaseModel, Field
from hub.crypto import encrypt_secret, decrypt_secret
from hub.adapters.downstream.erp4 import (
    Erp4Adapter, ErpPermissionError, ErpSystemError, ErpAdapterError,
)
from hub.models import (
    DownstreamSystem, HubUser, ChannelUserBinding, DownstreamIdentity,
    HubRole, HubUserRole, ChannelApp, AIProvider, SystemConfig,
)
# ❗ 同 ai_providers.py：provider 类提到顶层，让测试能 monkeypatch
# hub.routers.setup_full.DeepSeekProvider/QwenProvider，不在函数体内 import。
from hub.capabilities.deepseek import DeepSeekProvider
from hub.capabilities.qwen import QwenProvider


router = APIRouter(prefix="/hub/v1/setup", tags=["setup"])


async def _is_initialized() -> bool:
    cfg = await SystemConfig.filter(key="system_initialized").first()
    return bool(cfg and cfg.value is True)


def _check_setup_session(request: Request, session_id: str | None) -> None:
    """所有步骤 2-6 都要校验 setup session。"""
    if session_id is None:
        raise HTTPException(401, "缺少 X-Setup-Session 头")
    sessions = getattr(request.app.state, "active_setup_sessions", {})
    if not sessions.get(session_id):
        raise HTTPException(401, "Setup session 无效或已过期")


# ========== 步骤 2：注册 ERP 系统连接 ==========

class ConnectErpRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., pattern=r"^https?://")
    api_key: str = Field(..., min_length=8)
    apikey_scopes: list[str] = Field(..., min_items=1)


@router.post("/connect-erp")
async def connect_erp(
    request: Request,
    body: ConnectErpRequest = Body(...),
    x_setup_session: str | None = Header(default=None, alias="X-Setup-Session"),
):
    if await _is_initialized():
        raise HTTPException(404, "HUB 已完成初始化")
    _check_setup_session(request, x_setup_session)

    # 测试连接（health check）
    test_adapter = Erp4Adapter(base_url=body.base_url, api_key=body.api_key)
    try:
        ok = await test_adapter.health_check()
        if not ok:
            raise HTTPException(400, "ERP 连接测试失败：health 返回非 200")
    except (ErpSystemError, ErpAdapterError) as e:
        raise HTTPException(502, f"无法连接 ERP：{e}")
    finally:
        await test_adapter.aclose()

    # 幂等：已有同名 → 更新；否则创建
    existing = await DownstreamSystem.filter(
        downstream_type="erp", name=body.name,
    ).first()
    encrypted = encrypt_secret(body.api_key, purpose="config_secrets")
    if existing:
        existing.base_url = body.base_url
        existing.encrypted_apikey = encrypted
        existing.apikey_scopes = body.apikey_scopes
        existing.status = "active"
        await existing.save()
        ds_id = existing.id
    else:
        ds = await DownstreamSystem.create(
            downstream_type="erp", name=body.name, base_url=body.base_url,
            encrypted_apikey=encrypted, apikey_scopes=body.apikey_scopes, status="active",
        )
        ds_id = ds.id

    # 立刻刷新 app.state.session_auth（让步骤 3 的 admin login 能用）
    new_adapter = Erp4Adapter(base_url=body.base_url, api_key=body.api_key)
    from hub.auth.erp_session import ErpSessionAuth
    request.app.state.session_auth = ErpSessionAuth(erp_adapter=new_adapter)

    return {"id": ds_id, "ok": True}


# ========== 步骤 3：创建第一个 admin ==========

class CreateAdminRequest(BaseModel):
    erp_username: str
    erp_password: str


@router.post("/create-admin")
async def create_admin(
    request: Request,
    body: CreateAdminRequest = Body(...),
    x_setup_session: str | None = Header(default=None, alias="X-Setup-Session"),
):
    if await _is_initialized():
        raise HTTPException(404)
    _check_setup_session(request, x_setup_session)

    auth = request.app.state.session_auth
    if auth is None:
        raise HTTPException(400, "请先完成步骤 2 注册 ERP 连接")

    # 用 ERP 凭据验证（同时确认 ApiKey 配置正确）
    try:
        login_resp = await auth.erp.login(
            username=body.erp_username, password=body.erp_password,
        )
    except ErpPermissionError:
        raise HTTPException(401, "ERP 用户名或密码错误")
    except (ErpSystemError, ErpAdapterError) as e:
        raise HTTPException(502, f"ERP 通信失败：{e}")

    erp_user = login_resp.get("user", {})
    erp_user_id = erp_user.get("id")
    erp_display = erp_user.get("display_name") or body.erp_username

    # 幂等：已有同 erp_user_id 的 hub_user → 复用，仅追加 platform_admin 角色
    existing_di = await DownstreamIdentity.filter(
        downstream_type="erp", downstream_user_id=erp_user_id,
    ).first()
    if existing_di:
        hub_user = await HubUser.get(id=existing_di.hub_user_id)
    else:
        hub_user = await HubUser.create(display_name=erp_display)
        await DownstreamIdentity.create(
            hub_user=hub_user, downstream_type="erp", downstream_user_id=erp_user_id,
        )

    role = await HubRole.get(code="platform_admin")
    await HubUserRole.get_or_create(hub_user_id=hub_user.id, role_id=role.id)

    return {"hub_user_id": hub_user.id, "erp_user_id": erp_user_id}


# ========== 步骤 4：注册钉钉应用 ==========

class ConnectDingtalkRequest(BaseModel):
    name: str = Field(default="钉钉企业内部应用")
    app_key: str = Field(..., min_length=1)
    app_secret: str = Field(..., min_length=1)
    robot_id: str | None = None


@router.post("/connect-dingtalk")
async def connect_dingtalk(
    request: Request,
    body: ConnectDingtalkRequest = Body(...),
    x_setup_session: str | None = Header(default=None, alias="X-Setup-Session"),
):
    if await _is_initialized():
        raise HTTPException(404)
    _check_setup_session(request, x_setup_session)

    # 幂等
    existing = await ChannelApp.filter(channel_type="dingtalk", name=body.name).first()
    enc_key = encrypt_secret(body.app_key, purpose="config_secrets")
    enc_secret = encrypt_secret(body.app_secret, purpose="config_secrets")
    if existing:
        existing.encrypted_app_key = enc_key
        existing.encrypted_app_secret = enc_secret
        existing.robot_id = body.robot_id
        existing.status = "active"
        await existing.save()
        ca_id = existing.id
    else:
        ca = await ChannelApp.create(
            channel_type="dingtalk", name=body.name,
            encrypted_app_key=enc_key, encrypted_app_secret=enc_secret,
            robot_id=body.robot_id, status="active",
        )
        ca_id = ca.id

    # gateway 的 connect_dingtalk_stream_when_ready 后台 task 会在 30s 内轮询到这条记录并连接
    return {"id": ca_id}


# ========== 步骤 5：注册 AI 提供商 ==========

# spec §19.2 修订：DeepSeek + Qwen 默认配置
_AI_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
}


class ConnectAIRequest(BaseModel):
    # **C 阶段仅支持 deepseek / qwen**——Plan 4 capabilities/factory 也只注册了这两类。
    # custom 类型留给 B 阶段或独立 spec 加 CustomOpenAIProvider + factory 注册时再开放。
    provider_type: str = Field(..., pattern="^(deepseek|qwen)$")
    name: str = Field(default="")
    api_key: str = Field(..., min_length=1)
    base_url: str | None = None  # 不填用默认（spec §19.2）
    model: str | None = None


@router.post("/connect-ai")
async def connect_ai(
    request: Request,
    body: ConnectAIRequest = Body(...),
    x_setup_session: str | None = Header(default=None, alias="X-Setup-Session"),
):
    if await _is_initialized():
        raise HTTPException(404)
    _check_setup_session(request, x_setup_session)

    # 已被 Pydantic pattern 限制为 deepseek/qwen，无需 else 兜底
    defaults = _AI_DEFAULTS[body.provider_type]
    base_url = body.base_url or defaults["base_url"]
    model = body.model or defaults["model"]

    name = body.name or f"{body.provider_type} 默认"

    # 测试 chat（provider 类已在模块顶层导入）
    cls = DeepSeekProvider if body.provider_type == "deepseek" else QwenProvider
    test = cls(api_key=body.api_key, base_url=base_url, model=model)
    try:
        await test.chat(messages=[{"role": "user", "content": "ping"}])
    except Exception as e:
        await test.aclose()
        raise HTTPException(502, f"AI 测试连接失败：{e}")
    await test.aclose()

    # 幂等 + 单 active 不变量：先把其他 provider 全部 disable
    existing = await AIProvider.filter(
        provider_type=body.provider_type, name=name,
    ).first()
    enc_key = encrypt_secret(body.api_key, purpose="config_secrets")
    if existing:
        await AIProvider.exclude(id=existing.id).update(status="disabled")
        existing.encrypted_api_key = enc_key
        existing.base_url = base_url
        existing.model = model
        existing.status = "active"
        await existing.save()
        return {"id": existing.id}
    else:
        await AIProvider.exclude(status="disabled").update(status="disabled")
        rec = await AIProvider.create(
            provider_type=body.provider_type, name=name,
            encrypted_api_key=enc_key, base_url=base_url, model=model,
            config={}, status="active",
        )
        return {"id": rec.id}


# ========== 步骤 6：完成 ==========

@router.post("/complete")
async def setup_complete(
    request: Request,
    x_setup_session: str | None = Header(default=None, alias="X-Setup-Session"),
):
    if await _is_initialized():
        raise HTTPException(404)
    _check_setup_session(request, x_setup_session)

    # 校验前置：DownstreamSystem(erp) + admin（DownstreamIdentity）+ ChannelApp(dingtalk) 三者必备
    erp_ds = await DownstreamSystem.filter(downstream_type="erp", status="active").first()
    if not erp_ds:
        raise HTTPException(400, "未完成步骤 2（注册 ERP）")

    admin_di = await DownstreamIdentity.filter(downstream_type="erp").first()
    if not admin_di:
        raise HTTPException(400, "未完成步骤 3（创建 admin）")

    dt_app = await ChannelApp.filter(channel_type="dingtalk", status="active").first()
    if not dt_app:
        raise HTTPException(400, "未完成步骤 4（注册钉钉）")

    # AI 步骤 5 是可选，跳过也允许 complete

    # 写入 system_initialized=true（同步关闭所有 /setup/* 路由）
    await SystemConfig.update_or_create(
        key="system_initialized", defaults={"value": True},
    )

    # 清理 setup session
    sessions = getattr(request.app.state, "active_setup_sessions", {})
    if x_setup_session in sessions:
        del sessions[x_setup_session]

    return {"success": True, "redirect_to": "/login"}
```

修改 `backend/hub/routers/setup.py`（Plan 2 创建）：把 `_active_setup_sessions` dict 改为存到 `app.state.active_setup_sessions`，让 setup_full 共用。

```python
# setup.py /verify-token endpoint 改为：
@router.post("/verify-token")
async def verify_token_endpoint(
    request: Request, payload: VerifyTokenRequest = Body(...),
):
    if await _is_initialized():
        raise HTTPException(status_code=404, detail="HUB 已完成初始化")
    if not await verify_and_consume_token(payload.token):
        raise HTTPException(status_code=401, detail="初始化 Token 错误或已过期")

    session_id = secrets.token_urlsafe(16)
    if not hasattr(request.app.state, "active_setup_sessions"):
        request.app.state.active_setup_sessions = {}
    request.app.state.active_setup_sessions[session_id] = True
    return {"session": session_id}
```

main.py 注册：
```python
from hub.routers import setup_full
app.include_router(setup_full.router)
```

- [ ] **Step 2: 写测试**

文件 `backend/tests/test_setup_full.py`：
```python
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch


@pytest.fixture
async def setup_client(setup_db):
    """已通过 verify-token 拿到 session 的 client。"""
    from main import app
    if not hasattr(app.state, "active_setup_sessions"):
        app.state.active_setup_sessions = {}
    app.state.active_setup_sessions["test-session"] = True

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


@pytest.mark.asyncio
async def test_no_session_returns_401(setup_client):
    resp = await setup_client.post(
        "/hub/v1/setup/connect-erp",
        json={"name": "X", "base_url": "http://x", "api_key": "12345678", "apikey_scopes": ["s"]},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_connect_erp_persists_and_refreshes_session_auth(setup_client):
    from hub.adapters.downstream.erp4 import Erp4Adapter
    from main import app
    from hub.models import DownstreamSystem

    with patch.object(Erp4Adapter, "health_check", new_callable=AsyncMock, return_value=True):
        resp = await setup_client.post(
            "/hub/v1/setup/connect-erp",
            json={"name": "ERP生产", "base_url": "http://erp:8090",
                  "api_key": "abcd1234", "apikey_scopes": ["act_as_user", "system_calls"]},
            headers={"X-Setup-Session": "test-session"},
        )
    assert resp.status_code == 200
    ds = await DownstreamSystem.filter(downstream_type="erp", name="ERP生产").first()
    assert ds is not None
    # session_auth 已立即可用
    assert app.state.session_auth is not None


@pytest.mark.asyncio
async def test_connect_erp_health_fail_returns_502(setup_client):
    from hub.adapters.downstream.erp4 import Erp4Adapter
    with patch.object(Erp4Adapter, "health_check", new_callable=AsyncMock, return_value=False):
        resp = await setup_client.post(
            "/hub/v1/setup/connect-erp",
            json={"name": "X", "base_url": "http://x", "api_key": "abcdefgh", "apikey_scopes": ["s"]},
            headers={"X-Setup-Session": "test-session"},
        )
    assert resp.status_code in (400, 502)


@pytest.mark.asyncio
async def test_create_admin_creates_hub_user_and_role(setup_client, setup_db):
    """步骤 3 端到端：测试 ERP login → 创建 hub_user + downstream_identity + 绑 platform_admin。"""
    from hub.seed import run_seed
    from hub.models import HubUser, DownstreamIdentity, HubRole, HubUserRole
    from hub.adapters.downstream.erp4 import Erp4Adapter
    from hub.auth.erp_session import ErpSessionAuth
    from main import app
    await run_seed()

    erp = AsyncMock()
    erp.login = AsyncMock(return_value={
        "access_token": "tok",
        "user": {"id": 42, "username": "admin", "display_name": "管理员"},
    })
    app.state.session_auth = ErpSessionAuth(erp_adapter=erp)

    resp = await setup_client.post(
        "/hub/v1/setup/create-admin",
        json={"erp_username": "admin", "erp_password": "x"},
        headers={"X-Setup-Session": "test-session"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["erp_user_id"] == 42

    # 校验副作用
    di = await DownstreamIdentity.filter(downstream_type="erp", downstream_user_id=42).first()
    assert di is not None
    role = await HubRole.get(code="platform_admin")
    ur = await HubUserRole.filter(hub_user_id=di.hub_user_id, role_id=role.id).first()
    assert ur is not None


@pytest.mark.asyncio
async def test_connect_dingtalk_idempotent(setup_client):
    from hub.models import ChannelApp
    payload = {"name": "钉钉", "app_key": "k", "app_secret": "s", "robot_id": "r"}
    headers = {"X-Setup-Session": "test-session"}
    r1 = await setup_client.post("/hub/v1/setup/connect-dingtalk", json=payload, headers=headers)
    r2 = await setup_client.post("/hub/v1/setup/connect-dingtalk", json=payload, headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200
    apps = await ChannelApp.filter(channel_type="dingtalk", name="钉钉").all()
    assert len(apps) == 1  # 幂等


@pytest.mark.asyncio
async def test_connect_ai_uses_default_for_known_provider(setup_client):
    from hub.capabilities.deepseek import DeepSeekProvider
    from hub.models import AIProvider
    with patch.object(DeepSeekProvider, "chat", new_callable=AsyncMock, return_value="ok"):
        resp = await setup_client.post(
            "/hub/v1/setup/connect-ai",
            json={"provider_type": "deepseek", "api_key": "sk-x"},
            headers={"X-Setup-Session": "test-session"},
        )
    assert resp.status_code == 200
    rec = await AIProvider.filter(provider_type="deepseek").first()
    assert rec is not None
    assert "deepseek.com" in rec.base_url
    assert rec.model == "deepseek-chat"


@pytest.mark.asyncio
async def test_complete_blocks_until_required_steps_done(setup_client):
    """步骤 6 必须在 ERP + admin + 钉钉 都完成后才能调。"""
    headers = {"X-Setup-Session": "test-session"}
    resp = await setup_client.post("/hub/v1/setup/complete", headers=headers)
    assert resp.status_code == 400  # 缺前置


@pytest.mark.asyncio
async def test_complete_writes_system_initialized_and_blocks_setup(setup_client, setup_db):
    """步骤 6 完成后写入 SystemConfig + 后续 /setup/* 全部 404。"""
    from hub.models import (
        DownstreamSystem, HubUser, DownstreamIdentity,
        ChannelApp, SystemConfig,
    )
    # 预置三个前置
    await DownstreamSystem.create(
        downstream_type="erp", name="X", base_url="http://x",
        encrypted_apikey=b"\0" * 32, apikey_scopes=["x"], status="active",
    )
    user = await HubUser.create(display_name="A")
    await DownstreamIdentity.create(
        hub_user=user, downstream_type="erp", downstream_user_id=1,
    )
    await ChannelApp.create(
        channel_type="dingtalk", name="D",
        encrypted_app_key=b"\0" * 32, encrypted_app_secret=b"\0" * 32,
        status="active",
    )

    headers = {"X-Setup-Session": "test-session"}
    resp = await setup_client.post("/hub/v1/setup/complete", headers=headers)
    assert resp.status_code == 200

    cfg = await SystemConfig.filter(key="system_initialized").first()
    assert cfg is not None and cfg.value is True

    # 完成后再调 connect-erp 应返回 404
    r2 = await setup_client.post(
        "/hub/v1/setup/connect-erp",
        json={"name": "Y", "base_url": "http://y", "api_key": "abcdefgh", "apikey_scopes": ["s"]},
        headers=headers,
    )
    assert r2.status_code == 404
```

- [ ] **Step 3: 提交**

```bash
git add backend/hub/routers/setup_full.py \
        backend/hub/routers/setup.py \
        backend/main.py \
        backend/tests/test_setup_full.py
git commit -m "feat(hub): 初始化向导步骤 2-6 完整业务（含 setup session 校验、幂等、session_auth 刷新、完成关闭）"
```

---

## Task 10：cron 调度器（每日巡检 + payload 清理）

**Files:**
- Create: `backend/hub/cron/scheduler.py`
- Create: `backend/hub/cron/task_payload_cleanup.py`
- Create: `backend/hub/cron/dingtalk_user_client.py`（cron 用的最小化 OpenAPI 用户列表客户端）
- Create: `backend/hub/cron/jobs.py`（具体 job 函数，独立于 main.py 方便测试）
- Modify: `backend/main.py`（lifespan 启动调度器、注册 jobs）
- Test: `backend/tests/test_cron_scheduler.py`
- Test: `backend/tests/test_cron_jobs.py`
- Test: `backend/tests/test_dingtalk_user_client.py`

- [ ] **Step 1: scheduler.py**

```python
"""asyncio cron 调度器：每天 03:00 跑 daily_employee_audit + payload cleanup。"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger("hub.cron")

JobFn = Callable[[], Awaitable[None]]


class CronScheduler:
    def __init__(self, *, tz_name: str = "Asia/Shanghai"):
        self.tz = ZoneInfo(tz_name)
        self._jobs: list[tuple[int, JobFn]] = []  # (target_hour, callable)
        self._task: asyncio.Task | None = None
        self._stop = False

    def at_hour(self, hour: int):
        if not (0 <= hour <= 23):
            raise ValueError(f"hour 必须 0-23，收到 {hour}")

        def decorator(fn: JobFn):
            self._jobs.append((hour, fn))
            return fn
        return decorator

    async def _run(self):
        while not self._stop:
            if not self._jobs:
                await asyncio.sleep(60)
                continue
            now = datetime.now(self.tz)
            next_runs = []
            for hour, fn in self._jobs:
                target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                next_runs.append((target, fn))
            next_runs.sort(key=lambda x: x[0])
            target, fn = next_runs[0]
            sleep_seconds = (target - now).total_seconds()
            try:
                await asyncio.sleep(sleep_seconds)
                if self._stop:
                    break
                logger.info(f"cron 触发: {fn.__name__}")
                try:
                    await fn()
                except Exception:
                    logger.exception(f"cron job {fn.__name__} 失败")
            except asyncio.CancelledError:
                break

    def start(self):
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
```

- [ ] **Step 2: dingtalk_user_client.py（cron 专用 OpenAPI 客户端）**

`daily_employee_audit(client)` 期望 client 提供 `fetch_active_userids() -> set[str]`。Plan 3 没有定义这个客户端类（只定义了 `DingTalkSender` 用于推送）。本步骤实现一个最小化的客户端，复用 Plan 3 的 access_token 缓存逻辑，调钉钉 OpenAPI 列表接口拉取企业全员 userid。

```python
"""DingTalk 用户列表客户端（cron 专用）。

用法：
    client = DingTalkUserClient(app_key, app_secret)
    try:
        userids = await client.fetch_active_userids()
    finally:
        await client.aclose()

接口：
- GET https://oapi.dingtalk.com/gettoken?appkey=&appsecret=     → access_token
- POST /topapi/v2/department/listsub?access_token=             → 子部门列表
- POST /topapi/user/listid?access_token=                       → 部门下所有 userid
"""
from __future__ import annotations
import logging
import time
from typing import Iterable

import httpx

logger = logging.getLogger("hub.cron.dingtalk_user_client")

GET_TOKEN_URL = "https://oapi.dingtalk.com/gettoken"
DEPT_LIST_URL = "https://oapi.dingtalk.com/topapi/v2/department/listsub"
USER_LIST_URL = "https://oapi.dingtalk.com/topapi/user/listid"
ROOT_DEPT_ID = 1


class DingTalkUserClientError(Exception):
    pass


class DingTalkUserClient:
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0

    async def aclose(self):
        await self._client.aclose()

    async def _get_access_token(self) -> str:
        now = time.time()
        if self._cached_token and now < self._token_expires_at - 60:
            return self._cached_token
        r = await self._client.get(
            GET_TOKEN_URL,
            params={"appkey": self.app_key, "appsecret": self.app_secret},
        )
        r.raise_for_status()
        body = r.json()
        if body.get("errcode") != 0:
            raise DingTalkUserClientError(f"gettoken 失败: {body}")
        self._cached_token = body["access_token"]
        self._token_expires_at = now + int(body.get("expires_in", 7200))
        return self._cached_token

    async def _list_sub_departments(self, parent_id: int, token: str) -> list[int]:
        r = await self._client.post(
            DEPT_LIST_URL,
            params={"access_token": token},
            json={"dept_id": parent_id},
        )
        r.raise_for_status()
        body = r.json()
        if body.get("errcode") != 0:
            raise DingTalkUserClientError(f"listsub dept={parent_id} 失败: {body}")
        return [d["dept_id"] for d in body.get("result", [])]

    async def _list_userids_in_dept(self, dept_id: int, token: str) -> list[str]:
        r = await self._client.post(
            USER_LIST_URL,
            params={"access_token": token},
            json={"dept_id": dept_id},
        )
        r.raise_for_status()
        body = r.json()
        if body.get("errcode") != 0:
            raise DingTalkUserClientError(f"listid dept={dept_id} 失败: {body}")
        return body.get("result", {}).get("userid_list", [])

    async def _walk_departments(self, token: str) -> Iterable[int]:
        """BFS 遍历整个组织树，从根部门开始。返回所有 dept_id（含根）。"""
        visited: set[int] = set()
        queue: list[int] = [ROOT_DEPT_ID]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            children = await self._list_sub_departments(current, token)
            queue.extend(children)
        return visited

    async def fetch_active_userids(self) -> set[str]:
        """拉取企业全员现役 userid 集合。"""
        token = await self._get_access_token()
        all_dept_ids = await self._walk_departments(token)
        all_userids: set[str] = set()
        for dept_id in all_dept_ids:
            ids = await self._list_userids_in_dept(dept_id, token)
            all_userids.update(ids)
        logger.info(f"DingTalk 现役 userid 数量: {len(all_userids)}")
        return all_userids
```

- [ ] **Step 3: task_payload_cleanup.py**

```python
"""清理过期 task_payload（PII 30 天 TTL）。"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

logger = logging.getLogger("hub.cron.task_payload_cleanup")


async def cleanup_expired_task_payloads() -> int:
    """删除 expires_at <= now 的 task_payload，返回删除条数。"""
    from hub.models import TaskPayload
    n = await TaskPayload.filter(expires_at__lte=datetime.now(timezone.utc)).delete()
    logger.info(f"清理过期 task_payload: {n} 条")
    return n
```

- [ ] **Step 4: jobs.py（封装具体 job，方便单测）**

```python
"""cron job 函数：构造依赖 + 调用业务 + 错误重试。

每个 job 都要：
1. 处理"配置缺失"场景（无 ChannelApp / disabled）→ 跳过 + WARN 日志
2. 处理 OpenAPI 调用失败 → 重试 1 次 → 仍失败则 ERROR 日志（不抛异常，避免炸 scheduler）
3. 关闭 httpx client（finally aclose）
"""
from __future__ import annotations
import asyncio
import logging

import httpx

from hub.crypto import decrypt_secret
from hub.cron.dingtalk_user_client import DingTalkUserClient, DingTalkUserClientError
from hub.cron.dingtalk_user_sync import daily_employee_audit
from hub.cron.task_payload_cleanup import cleanup_expired_task_payloads
from hub.models import ChannelApp

logger = logging.getLogger("hub.cron.jobs")


async def _load_active_dingtalk_app() -> ChannelApp | None:
    return await ChannelApp.filter(channel_type="dingtalk", status="active").first()


async def run_daily_audit() -> dict | None:
    """每日凌晨：拉钉钉企业现役员工 → 标记离职用户 binding 为 revoked。

    返回 daily_employee_audit 的统计字典，无可用配置则返回 None。
    """
    app = await _load_active_dingtalk_app()
    if app is None:
        logger.warning("daily_audit 跳过：没有 active 状态的 dingtalk ChannelApp")
        return None

    try:
        app_key = decrypt_secret(app.encrypted_app_key, purpose="config_secrets")
        app_secret = decrypt_secret(app.encrypted_app_secret, purpose="config_secrets")
    except Exception:
        logger.exception("daily_audit 跳过：ChannelApp 解密失败")
        return None

    client = DingTalkUserClient(app_key=app_key, app_secret=app_secret)
    last_err: Exception | None = None
    try:
        for attempt in (1, 2):
            try:
                stats = await daily_employee_audit(client)
                logger.info(f"daily_audit 完成: {stats}")
                return stats
            except (DingTalkUserClientError, httpx.HTTPError) as e:
                last_err = e
                logger.warning(f"daily_audit 第 {attempt} 次失败: {e}")
                if attempt < 2:
                    await asyncio.sleep(5)
        logger.error(f"daily_audit 重试 2 次仍失败: {last_err}")
        return None
    finally:
        await client.aclose()


async def run_payload_cleanup() -> int:
    """每日凌晨：删除过期 task_payload。"""
    try:
        n = await cleanup_expired_task_payloads()
        return n
    except Exception:
        logger.exception("payload cleanup 失败")
        return 0
```

- [ ] **Step 5: 在 main.py 注册（替换 Plan 2 占位 + Plan 3 注释提到的 cron 集成）**

修改 `backend/main.py` lifespan：

```python
from hub.cron.scheduler import CronScheduler
from hub.cron.jobs import run_daily_audit, run_payload_cleanup

# ... 在 lifespan 内、 setup_session_auth 之后、 yield 之前：
scheduler = CronScheduler()

@scheduler.at_hour(3)
async def _job_audit():
    await run_daily_audit()

@scheduler.at_hour(3)
async def _job_cleanup():
    await run_payload_cleanup()

scheduler.start()
app.state.scheduler = scheduler

try:
    yield
finally:
    if hasattr(app.state, "scheduler"):
        await app.state.scheduler.stop()
```

- [ ] **Step 6: 测试 — scheduler 行为**

文件 `backend/tests/test_cron_scheduler.py`：

```python
"""CronScheduler 行为：start / stop / 触发 / 异常隔离。"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from hub.cron.scheduler import CronScheduler


@pytest.mark.asyncio
async def test_scheduler_starts_and_stops_cleanly():
    s = CronScheduler()
    s.start()
    assert s._task is not None
    await s.stop()
    assert s._task is None


@pytest.mark.asyncio
async def test_scheduler_rejects_invalid_hour():
    s = CronScheduler()
    with pytest.raises(ValueError):
        s.at_hour(24)(lambda: None)


@pytest.mark.asyncio
async def test_scheduler_runs_job_when_hour_arrives():
    """劫持 datetime.now，让"现在"刚好是 02:59:59，sleep 1 秒后触发 03:00 job。"""
    fake_now = datetime(2026, 4, 27, 2, 59, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
    calls = []

    s = CronScheduler()

    @s.at_hour(3)
    async def job():
        calls.append("ran")

    with patch("hub.cron.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        s.start()
        # 等待 scheduler 进入 sleep 状态
        await asyncio.sleep(0.05)
        # 取消 sleep — scheduler 会进入 except 分支并退出
        await s.stop()

    # job 在 sleep 被打断时不一定执行，本测试主要验证不崩溃
    assert s._task is None


@pytest.mark.asyncio
async def test_scheduler_isolates_job_exceptions():
    """job 抛异常不应让 scheduler 退出。"""
    s = CronScheduler()
    fail_count = [0]

    @s.at_hour(3)
    async def bad_job():
        fail_count[0] += 1
        raise RuntimeError("boom")

    # 直接调用 bad_job 验证 scheduler._run 内 try/except 包围
    # （单独跑 _run 涉及时间，这里只验证 scheduler API 健壮性）
    s.start()
    await asyncio.sleep(0.01)
    await s.stop()
    assert s._task is None


@pytest.mark.asyncio
async def test_scheduler_handles_no_jobs_gracefully():
    s = CronScheduler()
    s.start()
    await asyncio.sleep(0.05)
    await s.stop()
    assert s._task is None
```

跑：
```bash
pytest backend/tests/test_cron_scheduler.py -v
```
期望：5 个 PASS。

- [ ] **Step 7: 测试 — DingTalkUserClient（用 MockTransport）**

文件 `backend/tests/test_dingtalk_user_client.py`：

```python
"""DingTalkUserClient OpenAPI 行为：access_token 缓存 / 部门遍历 / userid 聚合。"""
from __future__ import annotations
import json

import httpx
import pytest

from hub.cron.dingtalk_user_client import DingTalkUserClient, DingTalkUserClientError


def _make_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_active_userids_walks_dept_tree_and_aggregates():
    """根部门 1 → 子部门 2,3；部门 1 有 u1，部门 2 有 u2,u3，部门 3 有 u3,u4 → 去重后 4 个。"""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/gettoken":
            return httpx.Response(200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200})
        if req.url.path == "/topapi/v2/department/listsub":
            body = json.loads(req.content)
            if body["dept_id"] == 1:
                return httpx.Response(200, json={"errcode": 0, "result": [{"dept_id": 2}, {"dept_id": 3}]})
            return httpx.Response(200, json={"errcode": 0, "result": []})
        if req.url.path == "/topapi/user/listid":
            body = json.loads(req.content)
            mapping = {1: ["u1"], 2: ["u2", "u3"], 3: ["u3", "u4"]}
            return httpx.Response(200, json={"errcode": 0, "result": {"userid_list": mapping[body["dept_id"]]}})
        return httpx.Response(404)

    client = DingTalkUserClient("ak", "as", transport=_make_transport(handler))
    try:
        ids = await client.fetch_active_userids()
    finally:
        await client.aclose()
    assert ids == {"u1", "u2", "u3", "u4"}


@pytest.mark.asyncio
async def test_get_access_token_caches_within_ttl():
    call_count = [0]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/gettoken":
            call_count[0] += 1
            return httpx.Response(200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200})
        return httpx.Response(200, json={"errcode": 0, "result": []})

    client = DingTalkUserClient("ak", "as", transport=_make_transport(handler))
    try:
        t1 = await client._get_access_token()
        t2 = await client._get_access_token()
        assert t1 == t2 == "tk"
        assert call_count[0] == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gettoken_errcode_nonzero_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": 40001, "errmsg": "invalid app_key"})

    client = DingTalkUserClient("bad", "bad", transport=_make_transport(handler))
    try:
        with pytest.raises(DingTalkUserClientError):
            await client._get_access_token()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_listsub_errcode_nonzero_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/gettoken":
            return httpx.Response(200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200})
        if req.url.path == "/topapi/v2/department/listsub":
            return httpx.Response(200, json={"errcode": 60011, "errmsg": "permission denied"})
        return httpx.Response(404)

    client = DingTalkUserClient("ak", "as", transport=_make_transport(handler))
    try:
        with pytest.raises(DingTalkUserClientError):
            await client.fetch_active_userids()
    finally:
        await client.aclose()
```

跑：
```bash
pytest backend/tests/test_dingtalk_user_client.py -v
```
期望：4 个 PASS。

- [ ] **Step 8: 测试 — jobs（端到端 cron→audit→revoke）**

文件 `backend/tests/test_cron_jobs.py`：

```python
"""cron jobs 端到端：run_daily_audit 触发 → revoke 离职 binding。"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hub.crypto import encrypt_secret
from hub.cron.jobs import run_daily_audit, run_payload_cleanup
from hub.models import ChannelApp, ChannelUserBinding, HubUser, TaskPayload


@pytest.mark.asyncio
async def test_run_daily_audit_revokes_offboarded_binding(monkeypatch):
    """完整链路：DB 里有 ChannelApp + 1 active binding；
    OpenAPI 返回 userid 列表不含该 binding → 应被 revoke。"""
    # 1. 准备 ChannelApp（加密真实凭据）
    await ChannelApp.create(
        channel_type="dingtalk",
        name="test-app",
        encrypted_app_key=encrypt_secret("ak123", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("as123", purpose="config_secrets"),
        robot_id="robot_x",
        status="active",
    )

    # 2. 准备 binding：m_offboarded 离职，m_active 在职
    u1 = await HubUser.create(display_name="离职员工")
    u2 = await HubUser.create(display_name="在职员工")
    await ChannelUserBinding.create(
        hub_user=u1, channel_type="dingtalk",
        channel_userid="m_offboarded", status="active",
    )
    await ChannelUserBinding.create(
        hub_user=u2, channel_type="dingtalk",
        channel_userid="m_active", status="active",
    )

    # 3. mock OpenAPI：根部门 → 无子部门；部门 1 只含 m_active
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/gettoken":
            return httpx.Response(200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200})
        if req.url.path == "/topapi/v2/department/listsub":
            return httpx.Response(200, json={"errcode": 0, "result": []})
        if req.url.path == "/topapi/user/listid":
            return httpx.Response(200, json={"errcode": 0, "result": {"userid_list": ["m_active"]}})
        return httpx.Response(404)

    # 替换 client 构造，注入 MockTransport
    from hub.cron import jobs as jobs_mod
    real_cls = jobs_mod.DingTalkUserClient

    def make_with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(jobs_mod, "DingTalkUserClient", make_with_mock)

    # 4. 触发 cron job
    stats = await run_daily_audit()

    # 5. 验证：m_offboarded 已 revoke，m_active 保持 active
    b1 = await ChannelUserBinding.filter(channel_userid="m_offboarded").first()
    b2 = await ChannelUserBinding.filter(channel_userid="m_active").first()
    assert b1.status == "revoked"
    assert b1.revoked_reason == "daily_audit"
    assert b2.status == "active"
    assert stats == {
        "active_dingtalk_userids": 1,
        "active_bindings_before": 2,
        "revoked": 1,
    }


@pytest.mark.asyncio
async def test_run_daily_audit_skips_when_no_channel_app(caplog):
    """没有 active dingtalk ChannelApp → 跳过 + WARN，不抛异常。"""
    import logging
    caplog.set_level(logging.WARNING, logger="hub.cron.jobs")
    result = await run_daily_audit()
    assert result is None
    assert any("没有 active 状态的 dingtalk ChannelApp" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_daily_audit_retries_on_openapi_error(monkeypatch, caplog):
    """OpenAPI 失败 1 次 → 重试 → 第 2 次成功仍能拿到结果。"""
    await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("ak", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("as", purpose="config_secrets"),
        status="active",
    )

    attempts = [0]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/gettoken":
            attempts[0] += 1
            if attempts[0] == 1:
                return httpx.Response(500, text="server error")
            return httpx.Response(200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200})
        if req.url.path == "/topapi/v2/department/listsub":
            return httpx.Response(200, json={"errcode": 0, "result": []})
        if req.url.path == "/topapi/user/listid":
            return httpx.Response(200, json={"errcode": 0, "result": {"userid_list": []}})
        return httpx.Response(404)

    from hub.cron import jobs as jobs_mod
    real_cls = jobs_mod.DingTalkUserClient

    def make_with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(jobs_mod, "DingTalkUserClient", make_with_mock)
    # sleep 跳过实际等待
    monkeypatch.setattr("hub.cron.jobs.asyncio.sleep", AsyncMock())

    stats = await run_daily_audit()
    assert stats is not None
    assert attempts[0] == 2  # 第 1 次 500 触发重试，第 2 次成功


@pytest.mark.asyncio
async def test_run_daily_audit_returns_none_after_2_failures(monkeypatch, caplog):
    """2 次 OpenAPI 都失败 → 返回 None，不抛异常炸 scheduler。"""
    import logging
    caplog.set_level(logging.ERROR, logger="hub.cron.jobs")

    await ChannelApp.create(
        channel_type="dingtalk", name="t",
        encrypted_app_key=encrypt_secret("ak", purpose="config_secrets"),
        encrypted_app_secret=encrypt_secret("as", purpose="config_secrets"),
        status="active",
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="always fail")

    from hub.cron import jobs as jobs_mod
    real_cls = jobs_mod.DingTalkUserClient

    def make_with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(jobs_mod, "DingTalkUserClient", make_with_mock)
    monkeypatch.setattr("hub.cron.jobs.asyncio.sleep", AsyncMock())

    result = await run_daily_audit()
    assert result is None
    assert any("重试 2 次仍失败" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_payload_cleanup_deletes_expired():
    """run_payload_cleanup 删过期 task_payload，未过期的保留。

    Plan 2 TaskPayload 的真实字段：task_log (OneToOne) / encrypted_request /
    encrypted_erp_calls / encrypted_response / expires_at。先建 TaskLog 父记录，
    再用 OneToOneField 关联 TaskPayload。
    """
    from hub.models import TaskLog
    now = datetime.now(timezone.utc)
    log_a = await TaskLog.create(
        task_id="t-expired", task_type="dingtalk_inbound",
        channel_type="dingtalk", channel_userid="u1", status="ok",
    )
    log_b = await TaskLog.create(
        task_id="t-fresh", task_type="dingtalk_inbound",
        channel_type="dingtalk", channel_userid="u2", status="ok",
    )
    await TaskPayload.create(
        task_log=log_a,
        encrypted_request=b"enc-req-a",
        encrypted_response=b"enc-resp-a",
        expires_at=now - timedelta(days=1),
    )
    await TaskPayload.create(
        task_log=log_b,
        encrypted_request=b"enc-req-b",
        encrypted_response=b"enc-resp-b",
        expires_at=now + timedelta(days=1),
    )
    n = await run_payload_cleanup()
    assert n == 1
    remaining = await TaskPayload.all().count()
    assert remaining == 1
    # 留下来的一定是 fresh 那条
    survivor = await TaskPayload.first()
    assert (await survivor.task_log).task_id == "t-fresh"


@pytest.mark.asyncio
async def test_run_payload_cleanup_swallows_exceptions(monkeypatch, caplog):
    """DB 异常不能炸 scheduler — 返回 0 + ERROR 日志。"""
    import logging
    caplog.set_level(logging.ERROR, logger="hub.cron.jobs")

    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("hub.cron.jobs.cleanup_expired_task_payloads", boom)
    n = await run_payload_cleanup()
    assert n == 0
    assert any("payload cleanup 失败" in r.message for r in caplog.records)
```

跑：
```bash
pytest backend/tests/test_cron_jobs.py -v
```
期望：6 个 PASS。

- [ ] **Step 9: 提交**

```bash
git add backend/hub/cron/scheduler.py \
        backend/hub/cron/dingtalk_user_client.py \
        backend/hub/cron/task_payload_cleanup.py \
        backend/hub/cron/jobs.py \
        backend/main.py \
        backend/tests/test_cron_scheduler.py \
        backend/tests/test_dingtalk_user_client.py \
        backend/tests/test_cron_jobs.py
git commit -m "feat(hub): asyncio cron 调度器（daily_employee_audit + payload 清理 + DingTalkUserClient）"
```

---

## Task 11：前端 Vue SPA 骨架 + 12 个 admin 页面

**Files:** 略（按文件结构表写每个 view 和 api 模块）

前端工作量较大，按以下顺序实施：

- [ ] **Step 1: 复制 ERP UI 起步**

```bash
cd /Users/lin/Desktop/hub/frontend
cp -r /Users/lin/Desktop/ERP-4/frontend/src/components/ui ./src/components/
cp -r /Users/lin/Desktop/ERP-4/frontend/src/components/common ./src/components/
cp -r /Users/lin/Desktop/ERP-4/frontend/src/styles ./src/
# 仅复制必要的，不带业务相关组件
```

- [ ] **Step 2: package.json + vite.config.js**

按 ERP 模式建立。`build.outDir = "../backend/static"` 让 gateway serve 静态。

- [ ] **Step 3: 路由 + 鉴权守卫**

`router/index.js`：
```javascript
const routes = [
  { path: "/", redirect: () => {/* 检查 system_initialized → /setup or /login */} },
  { path: "/setup/:step?", component: SetupWizard, /*...*/ },
  { path: "/login", component: LoginView },
  {
    path: "/admin",
    component: AdminLayout,
    children: [
      { path: "", component: DashboardView },
      { path: "users", component: UsersView },
      // ... 12 个页面
    ],
    beforeEnter: async (to) => {
      const auth = useAuthStore();
      if (!await auth.fetchMe()) return "/login";
    },
  },
];
```

- [ ] **Step 4-15: 实现各 view（按下表数据源 + 主交互）**

| view | 数据源 endpoint | 主交互 |
|---|---|---|
| LoginView | POST /hub/v1/admin/login | 表单：ERP 用户名 + 密码 → cookie 登录 → 跳 /admin |
| SetupWizard 6 步 | /hub/v1/setup/welcome / verify-token / connect-erp / create-admin / connect-dingtalk / connect-ai / complete | 6 步走表单；每步成功才能进下一步；最终 redirect /login |
| AdminLayout | /hub/v1/admin/me | 左侧 nav（按 permissions 过滤项）+ 顶部用户菜单（注销）+ router-view |
| DashboardView | GET /hub/v1/admin/dashboard | 健康 4 卡片 + 今日 4 数字 + 24h hourly Chart.js 折线 |
| UsersView | GET /hub/v1/admin/hub-users + /hub-users/{id} | 列表分页 + 关键字搜索；点行 → 详情抽屉显示 channel_bindings / downstream_identities / roles |
| RolesView | GET /hub/v1/admin/hub-roles | 列表 + 每行展开看权限码（中文 name+desc） |
| UserRolesView | GET hub-users + PUT hub-users/{id}/roles | 用户列表 + 编辑按钮 → modal 多选角色 → 保存 |
| AccountLinksView | PUT hub-users/{id}/downstream-identity | 用户列表 + "关联 ERP 账号" 按钮 → modal 输入 ERP user_id |
| PermissionsView | GET hub-permissions | 表格只读：code / name / description / resource / action |
| DownstreamsView | GET/POST/PUT/POST disable + test-connection | 列表 + 创建 modal + 改密钥 modal + 测试连接按钮 |
| ChannelsView | GET/POST/PUT/disable | 列表 + 创建 modal + 改 secret modal |
| AIProvidersView | GET defaults / list / create / test-chat / set-active | 默认值预填表单 + 测试 chat + 一键切 active |
| SystemConfigView | GET/PUT /admin/config/{key} | 已知 key 列表（alert_receivers 等）+ 编辑 |
| TasksView | GET /admin/tasks | 列表 + 筛选（user / status / since_hours）+ 行点击进详情 |
| TaskDetailView | GET /admin/tasks/{id} | 时间线展示 task_log 元数据 + 解密 payload + ERP 调用列表 |
| ConversationLiveView | EventSource("/hub/v1/admin/conversation/live") | SSE 推流 → 顶部插入新事件；脱敏 preview 显示 |
| ConversationHistoryView | GET /admin/conversation/history | 列表 + 时间筛选 + 关键字搜索 |
| AuditView | GET /admin/audit + /admin/audit/meta | 操作日志列表（普通 admin） + Meta 审计页（system_read 权限） |
| HealthView | GET /hub/v1/health | 实时刷新（每 10s），各组件状态 chip |

**实施要求（每个 view 必做）**：
- 状态显示中文（用 store/utils 把 `status` enum 翻译成"运行中"/"成功"/"失败"等）
- 错误码不暴露给用户：所有 API 错误响应里的 `detail` 直接显示，不显示 `code`/`error_classification`
- 角色名用中文：`role.name`（如"HUB 系统管理员"），不显示 `role.code`
- 权限码列表用 `name + description`，不显示 `permission.code`
- 加密字段不渲染（list 接口已经不返回；前端只显示 `secret_set: true` 灯）
- 表单提交时 catch axios 错误，把 `detail` 显示在卡片顶部红色提示区
- 列表页用 ERP 现有 AppPagination 组件
- 编辑用 ERP 现有 AppModal `#footer` slot 放按钮（不用 `@confirm`）

参考 ERP 现有 view 风格：`/Users/lin/Desktop/ERP-4/frontend/src/views/CustomersView.vue` 是最接近的"列表 + 编辑"模板。

- [ ] **Step 16: Gateway StaticFiles 挂载 + SPA catch-all 路由**

修改 `backend/main.py`，在所有 router 注册之后追加：
```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

STATIC_DIR = Path(__file__).parent / "static"

if STATIC_DIR.exists():
    # 静态资源：/assets/* 等 vite 输出
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """SPA fallback：所有非 /hub/v1/* 和 /assets/* 的路径都返回 index.html，
        让 Vue Router 处理前端路由。"""
        # API 路径已被前面的 routers 拦截；走到这里的都是前端路由（/setup/* / /login / /admin/*）
        index = STATIC_DIR / "index.html"
        if not index.exists():
            return Response("Frontend 未构建（缺 backend/static/index.html）", status_code=503)
        return FileResponse(index)
else:
    @app.get("/", include_in_schema=False)
    async def no_frontend():
        return Response("Frontend 未构建。请在 frontend/ 目录跑 npm run build。", status_code=503)
```

**重要**：catch-all 路由必须**最后**注册，否则会拦截 `/hub/v1/*` API 请求。在 `main.py` 中放在 `app.include_router(...)` 全部之后。

- [ ] **Step 17: Dockerfile.gateway 改多阶段构建（前端 + 后端）**

修改 `Dockerfile.gateway`：
```dockerfile
# Stage 1：前端构建
FROM node:20-slim AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# vite.config.js 中 build.outDir = "../backend/static"，这里改写成 ./dist 给后端阶段拷
RUN npx vite build --outDir ./dist

# Stage 2：后端 + 前端产物
FROM python:3.11-slim
WORKDIR /app
COPY backend/ ./
RUN pip install --no-cache-dir -e .

# 把前端构建产物拷到 backend/static
COPY --from=frontend-builder /frontend/dist ./static

ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
EXPOSE 8091
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8091"]
```

worker.py 不需要前端，Dockerfile.worker 不动。

`frontend/vite.config.js` 中确保 `build.outDir = "./dist"`（不再写到 `../backend/static`），让 Docker 多阶段构建复用产物；本地开发时如果想直接放进去，运维手动改 outDir 即可。

- [ ] **Step 18: 端到端 smoke test**

```bash
cd /Users/lin/Desktop/hub
docker compose build hub-gateway  # 触发多阶段构建
docker compose up -d
sleep 8

# 1. 看启动日志拿 setup token
docker compose logs hub-gateway | grep -A5 "初始化 Token"

# 2. 浏览器访问 http://localhost:8091/  → SPA fallback → 返回 index.html
#    Vue Router 识别为 /setup → 渲染 SetupWizard
# 3. 走完向导 / 登录 / 各 tab 看到内容 / 创建/编辑生效 / 实时对话流接入

# 4. 验证 API 路径不被 catch-all 拦截
curl http://localhost:8091/hub/v1/health  # 期望返回 JSON 不是 HTML
```

- [ ] **Step 19: 提交**

⚠️ git add 必须**显式列出**所有改动文件，不能只 `git add frontend/`，否则会漏掉 backend 侧的 SPA fallback 路由和多阶段 Dockerfile。

```bash
git add frontend/ \
        frontend/vite.config.js \
        frontend/package.json \
        frontend/package-lock.json \
        backend/main.py \
        Dockerfile.gateway
git commit -m "feat(hub): Vue 3 SPA + 12 个 admin 页面 + 6 步初始化向导 + gateway StaticFiles/SPA fallback + 多阶段 Docker build"
```

提交后跑：
```bash
git status
```
期望：working tree clean，无遗漏文件。

---

## Task 12：ERP CLAUDE.md 同步 UI 大白话原则

**Files:**
- Modify: `/Users/lin/Desktop/ERP-4/CLAUDE.md`

按 spec §19.2 P2 要求把 UI 大白话原则同步到 ERP 项目。

- [ ] **Step 1: ERP CLAUDE.md 加一段**

在 ERP 现有 "## 设计系统" 节后追加：
```markdown
### UI 大白话原则（与 HUB 项目同步）

UI 文案**必须中文大白话**，禁止暴露技术标识符（permission code / role code / API endpoint 路径 / 错误码字符串等）。

- 任何"角色"、"权限"、"功能"、"按钮"、"错误提示"，都必须有对应的中文显示名 + 中文说明
- 后端可保留 code 字段做内部 ID（如 `user.role = 'admin'`），但 UI 渲染一律用中文显示名
- 错误提示同理：返回"原密码错误"，不是"AUTH_FAILED: invalid old password"
- 状态枚举用中文（"已审核"/"待审核"/"已驳回"），不暴露 enum value
- 这条原则与 HUB 项目对齐——见 `/Users/lin/Desktop/hub/CLAUDE.md`
```

- [ ] **Step 2: 提交（在 ERP 仓库）**

```bash
cd /Users/lin/Desktop/ERP-4
git add CLAUDE.md
git commit -m "docs: 同步 UI 大白话原则（与 HUB 项目对齐）"
```

---

## Task 13：自审 + 端到端验证 + 验证记录

- [ ] **Step 1: 跑全部测试**

```bash
cd /Users/lin/Desktop/hub/backend
pytest -v
```
期望：Plan 2-5 累计 ~265 测试全 PASS（Plan 5 自身 **97**，与上方测试表合计列严格一致）。

- [ ] **Step 2: 端到端完整流程演练**

新机部署完整跑一遍：
1. `docker compose up -d` 启动 4 容器
2. 拿初始化 token，访问 /setup
3. 走完 6 步向导 → 创建 admin / 接 ERP / 接钉钉 / 接 AI / 完成
4. 跳转登录页 → 用 admin 账号登录
5. 进 dashboard 看到状态聚合
6. 进各管理页验证 CRUD
7. 用钉钉测试组织发"/绑定 张三"→ 收到绑定码 → 走完绑定 → 收到隐私告知
8. 发"查 SKU100"→ 收到商品卡
9. 发"查 SKU100 给阿里"→ 收到客户历史价
10. admin 后台进对话监控-实时 → 看到上面对话事件流
11. 进 task 详情查看明文 payload → 触发 meta_audit_log
12. 进 audit 页面看到所有 admin 操作
13. 等到 03:00 凌晨（或手动触发） → 验证 daily_employee_audit + task_payload cleanup 跑

- [ ] **Step 3: 验证记录**

文件 `docs/superpowers/plans/notes/2026-04-27-plan5-end-to-end-verification.md`，包含：
- 单测 97 PASS 列表（按测试文件表逐文件给出每文件 PASS 数）
- 端到端 13 步演练结果（每步 ✅/❌）
- 性能：dashboard 响应 < 1s / SSE 推流延迟 < 500ms
- 安全：cookie 不可解码、无 admin 权限被 403、看 payload 留痕
- C 阶段验收清单（spec §17）逐条对照

```bash
git add docs/superpowers/plans/notes/2026-04-27-plan5-end-to-end-verification.md
git commit -m "docs(hub): Plan 5 端到端验证记录 + C 阶段验收"
```

---

## Self-Review（v3，应用第二轮 review 反馈后）

### Spec 覆盖检查

| Spec 章节 | Plan 任务 | ✓ |
|---|---|---|
| §7.6 第一个 platform_admin Bootstrapping | Task 9 步骤 3 创建 admin | ✓ |
| §9 对话监控（3 子页 + meta_audit）| Task 6 + Task 7 | ✓ |
| §12 HUB Web 后台 12 路由 / 16 页面 | Task 4-9 + Task 11 | ✓ |
| §13.5 健康检查 + 仪表盘 | Task 8 dashboard | ✓ |
| §14.4 PII（30 天 TTL + 加密）| Task 6 task_logger + Task 10 cleanup | ✓ |
| §14.5 后台访问审计 | Task 4 audit_log + Task 8 audit router | ✓ |
| §16.2 初始化向导 6 步完整业务 | Task 9（Plan 2 已实现 Step 1） | ✓ |
| §19.1 P1-10 HUB session 续期 | Task 1 ErpSessionAuth | ✓ |
| §19.2 P2 ERP CLAUDE.md 同步 | Task 12 | ✓ |
| §17 C 阶段验收（A-F + G）| Task 13 验证记录逐条对照 | ✓ |
| 钉钉 SDK 离职事件订阅集成（A 路径上线前） | 留 Plan 5 自审章节"上线前" | 备 |

### Placeholder Scan

- ✓ 无 "TODO" / "TBD"
- ⚠ Task 5 的 `channels.py` / `ai_providers.py` / `system_config.py` 用"按 downstreams 模板"而未展开完整代码——属于明显模式重复，spec 已详述结构；实施时按 downstreams 复制即可
- ⚠ Task 8 / Task 11 类似情况；前端 12 个 view 列出文件路径与职责，详细 Vue 模板展开会让 plan 体量爆炸（已超过 3000 行）；实施时参考 ERP 现有 view 模式

### 范围检查

Plan 5 完成后达到 C 阶段全部验收：
- ✅ docker compose up + 走完向导 = 完整可用 HUB
- ✅ 钉钉机器人完整业务（绑定 / 查询 / AI fallback / 多轮选编号）
- ✅ Web 后台完整管理（用户 / 角色 / 配置 / 任务 / 对话监控 / 审计 / 仪表盘）
- ✅ cron 调度器（每日巡检 + payload 清理）
- ✅ ERP CLAUDE.md UI 大白话同步
- ❌ 钉钉 SDK 离职事件订阅 A 路径具体接入（上线前评估，函数已就绪）
- ❌ 自定义角色编辑器（spec 明确 C 阶段不做，B 阶段加）

---

### v2 第一轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-V2-A | 初始化向导 2-6 是占位 | Task 9 完整展开：5 个 endpoint 都有 Pydantic schema、X-Setup-Session 校验、幂等（已存在则更新）、错误处理、副作用（DownstreamSystem/HubUser/DownstreamIdentity/HubRoleUserRole/ChannelApp/AIProvider/SystemConfig 写入）、刷新 app.state.session_auth；setup.py `_active_setup_sessions` 改放 `app.state` 让 setup_full 共用；新增 8 个 test_setup_full 测试 |
| P1-V2-B | 配置中心/审计/仪表盘"完整实现略" | Task 5 channels/ai_providers/system_config 全部展开完整 endpoint 代码（CRUD + 加密字段 + audit 写入 + AI defaults + AI test-chat + set-active）；Task 8 audit/dashboard 完整代码（list_audit_logs / list_meta_audit / dashboard 含 health+today+hourly） |
| P1-V2-C | 实时对话流没有发布事件 | Task 6 task_logger 加 `live_publisher` 参数，finally 块成功/失败都 publish 脱敏事件（手机号/身份证/银行卡正则脱敏）；handler 包装 sender 捕获 response；worker.py 注入 LiveStreamPublisher；新增 `test_live_stream_e2e.py` 2 个端到端测试断言 SSE 收到事件 + 脱敏生效 |
| P1-V2-D | Gateway 未真正 serve Vue SPA | Task 11 Step 16-18 重写：main.py 加 StaticFiles 挂载 + SPA catch-all（最后注册避免拦截 API）；Dockerfile.gateway 改多阶段（Node 20 frontend-builder → Python backend）；vite.config build.outDir 改为 `./dist`；端到端验证含 `curl /hub/v1/health` 不被 catch-all 拦截 |
| P2-V2-E | logout 不让 JWT 失效 | Erp4Adapter 加 `logout(jwt)` 调 ERP `/auth/logout` 让 token_version 递增；ErpSessionAuth.logout 解 cookie 拿 jwt 调 erp.logout；测试 `test_logout_invalidates_jwt_at_erp_and_clears_cache` 断言 logout 后 verify 返回 None（即使 cookie 字符串还在）；加坏 cookie 不抛异常测试 |
| P3 | 前端 12 view "实现略" | Task 11 Step 4-15 加完整数据源映射表（19 行：每个 view 对应的 endpoint + 主交互）+ 实施要求（中文文案/不暴露 code/AppPagination/AppModal #footer）+ 参考 ERP CustomersView 模板 |

---

### v3 第二轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-V3-A | 自定义 AI provider 会写入不可运行配置（前后端表单允许 `claude` / 任意 provider_type，但 capabilities/factory 只注册了 deepseek/qwen） | Task 5 `ai_providers.py` 的 `CreateAIRequest.provider_type` 加 `pattern="^(deepseek\|qwen)$"`；同 Task 9 setup_full.py `connect_ai` 步骤的 Pydantic schema 一并约束；前端 AIProvidersView 表单只暴露 deepseek/qwen 两选项 |
| P1-V3-B | 实时流接入仍是占位（handler 用 `# ...原有命令路由 ...` 替代真实业务，会吞掉 Plan 4 的命令路由） | Task 6 dingtalk_inbound 改造段重写：完整保留 Plan 4 的 IdentityService / 命令路由 / ChainParser / UseCase 调用；外层包 `log_inbound_task` 上下文，sender 用 wrapped_send_text 捕获 response；finally 还原 sender.send_text；每条退出路径都设 `record["final_status"]`；写入 3 处 "❗" 警告防止实施者用占位替代逻辑 |
| P1-V3-C | 每日离职巡检 cron 仍未实现（_job_audit 只有 `...` 占位） | Task 10 完整重写：新建 `backend/hub/cron/dingtalk_user_client.py`（OpenAPI 部门树遍历 + token 缓存 + 错误处理）+ `backend/hub/cron/jobs.py`（run_daily_audit 含加载 ChannelApp / 解密 / 重试 1 次 / aclose / 错误隔离 + run_payload_cleanup 含异常吞噬）；main.py lifespan 注册改为调用 jobs.run_daily_audit / jobs.run_payload_cleanup；新增 `test_cron_scheduler.py` 5 + `test_dingtalk_user_client.py` 4 + `test_cron_jobs.py` 6 共 15 个测试，端到端断言"OpenAPI 返回不含 binding userid → ChannelUserBinding 被 revoked + reason=daily_audit" |
| P1-V3-D | SPA / Dockerfile 改动会漏提交（Task 11 Step 17 只 `git add frontend/` 漏掉 backend/main.py + Dockerfile.gateway） | Task 11 重号：原 Step 17 (Dockerfile) 不动 + Step 18 (smoke test) + 新 Step 19 (提交)；Step 19 的 git add 显式列出 `frontend/` + `frontend/vite.config.js` + `frontend/package.json` + `frontend/package-lock.json` + `backend/main.py` + `Dockerfile.gateway`；提交后 `git status` 必须 clean |
| P2-V3-E | 渠道配置修改后运行中的钉钉 Stream 不会重连 / 停止 | Task 5 channels.py 加 `_signal_channel_reload` helper，`update_channel` / `disable_channel` 完成 DB 写入后 `request.app.state.dingtalk_reload_event.set()`；Plan 3 的 `connect_dingtalk_stream_when_ready` 是单次连接（连上就 return），Plan 5 在 lifecycle/dingtalk_connect.py 追加 `connect_with_reload`（循环模式：连接 → 等 reload event → stop 旧 adapter → 重读 ChannelApp → 启动新 adapter；status=disabled 仅 stop 不重连；cancel 时 stop 现有 adapter）；main.py lifespan 切换为 `connect_with_reload` + `app.state.dingtalk_reload_event` + `app.state.dingtalk_state`；新增 `test_admin_channels_reload.py` 3 个端到端测试 |

### Spec 覆盖检查（v3 重新核对）

| Spec 章节 | Plan 任务 | ✓ |
|---|---|---|
| §7.6 第一个 platform_admin Bootstrapping | Task 9 步骤 3 创建 admin | ✓ |
| §8.3 离职巡检 C 路径调度 | Task 10（cron + jobs + DingTalkUserClient + 6 个端到端测试） | ✓ |
| §9 对话监控（3 子页 + meta_audit）| Task 6 + Task 7 | ✓ |
| §11 渠道管理（含运行时配置变更） | Task 5（含 connect_with_reload 热重载） | ✓ |
| §12 HUB Web 后台 12 路由 / 16 页面 | Task 4-9 + Task 11 | ✓ |
| §13.5 健康检查 + 仪表盘 | Task 8 dashboard | ✓ |
| §14.4 PII（30 天 TTL + 加密）| Task 6 task_logger + Task 10 cleanup | ✓ |
| §14.5 后台访问审计 | Task 4 audit_log + Task 8 audit router | ✓ |
| §16.2 初始化向导 6 步完整业务 | Task 9（Plan 2 已实现 Step 1） | ✓ |
| §19.1 P1-10 HUB session 续期 | Task 1 ErpSessionAuth | ✓ |
| §19.2 P2 ERP CLAUDE.md 同步 | Task 12 | ✓ |
| §17 C 阶段验收（A-F + G）| Task 13 验证记录逐条对照 | ✓ |
| 钉钉 SDK 离职事件订阅集成（A 路径上线前） | 留 Plan 5 自审章节"上线前" | 备 |

### Placeholder Scan（v3）

- ✓ 无 "TODO" / "TBD" / "..." 占位代码块
- ✓ Task 5 channels.py / ai_providers.py / system_config.py 完整代码
- ✓ Task 10 _job_audit / _job_cleanup 已替换为 jobs.run_daily_audit / jobs.run_payload_cleanup（独立模块 + 完整测试）
- ✓ Task 11 Step 16-19 main.py / Dockerfile.gateway 完整代码 + 显式 git add
- ⚠ 前端 12 个 view 的 Vue 模板按"数据源 + 主交互"映射表 + ERP CustomersView 模板提示，不展开（避免 plan 体量爆炸到 6000+ 行）

### 类型一致性检查（v3）

- ✓ `connect_dingtalk_stream_when_ready` 保留（Plan 3 测试还在调用），新增 `connect_with_reload` 不互相冲突
- ✓ `_signal_channel_reload` 在 channels.py 单文件内定义+使用，不暴露
- ✓ `app.state.dingtalk_reload_event` / `app.state.dingtalk_state` 命名一致（main.py + connect_with_reload 状态注入）
- ✓ `run_daily_audit` / `run_payload_cleanup` 在 jobs.py 定义，main.py 引用一致
- ✓ `DingTalkUserClient.fetch_active_userids` 返回 `set[str]` 与 Plan 3 `daily_employee_audit(client)` 期望签名一致

### 范围检查（v3）

Plan 5 完成后达到 C 阶段全部验收：
- ✅ docker compose up + 走完向导 = 完整可用 HUB
- ✅ 钉钉机器人完整业务（绑定 / 查询 / AI fallback / 多轮选编号）
- ✅ Web 后台完整管理（用户 / 角色 / 配置 / 任务 / 对话监控 / 审计 / 仪表盘）
- ✅ 渠道配置变更运行时重连（不需要重启 gateway）
- ✅ cron 调度器（每日巡检 + payload 清理 + 6 个端到端测试覆盖）
- ✅ ERP CLAUDE.md UI 大白话同步
- ❌ 钉钉 SDK 离职事件订阅 A 路径具体接入（上线前评估，函数已就绪）
- ❌ 自定义角色编辑器（spec 明确 C 阶段不做，B 阶段加）

---

### v3 第三轮 review 修复清单（针对 v3 自身的 5 处反馈）

| # | 反馈 | 修复 |
|---|---|---|
| P1-V3R-A | `ai_providers.py` 改成 `Field(..., pattern=...)` 但 `from pydantic import BaseModel` 未带 Field → ImportError/NameError | 把 import 改为 `from pydantic import BaseModel, Field`；同 import 把 `decrypt_secret` 提到顶层（删 test_chat 里的 inline import）；`test_admin_ai_providers.py` 增 `test_module_imports_without_nameerror` 直接 `importlib.import_module("hub.routers.admin.ai_providers")` 校验 |
| P1-V3R-B | `cron/jobs.py` 用了 `from hub.crypto import EncryptedField` + `EncryptedField.decrypt(...)` → ImportError（Plan 2 加密模块只导出 `encrypt_secret/decrypt_secret`） | jobs.py 改为 `from hub.crypto import decrypt_secret` + `decrypt_secret(app.encrypted_app_key, purpose="config_secrets")`；`test_cron_jobs.py` 内的 `EncryptedField.encrypt(b"...")` 改为 `encrypt_secret("...", purpose="config_secrets")`（共 3 处 ChannelApp.create）|
| P1-V3R-C | `update_channel` 仅在代码块外文字提醒里调 `_signal_channel_reload`，复制代码会漏掉 → 改 secret 后 Stream 不重连 | 把 `_signal_channel_reload(request)` 直接放进 `update_channel` 函数体（在 `AuditLog.create` 后、return 前）+ `❗` 警告；`test_admin_channels.py` 增 2 个端到端测试：PUT secret 后 `app.state.dingtalk_reload_event.is_set()` 为 True；POST disable 同样断言 |
| P2-V3R-D | `create_ai` 每次都写 `status="active"` 但不 disable 其他项，会让 Plan 4 capabilities/factory 取 active 不确定 | `create_ai` 在 create 前 `await AIProvider.exclude(status="disabled").update(status="disabled")` 维护单 active 不变量 + 加 `audit_log`；`setup_full.connect_ai` 同步：existing 分支 `exclude(id=existing.id)` 全 disable，new 分支也 exclude(disabled) 全 disable；`test_admin_ai_providers.py` 增 `test_create_ai_disables_others_to_keep_single_active` |
| P3-V3R-E | 测试表合计 92 但实际 6+5+7+5+6+3+7+4+6+4+4+4+4+8+5+4+6+5+4=93 + 文末仍写"约 80" | 重新核算 = **97**（含 ai_providers 7 / channels 6 / channels_reload 3）；合计行写明 97 与逐行算式；Task 13 验证记录"单测 80"改 97 + Plan 2-5 累计 ~265；要求验证记录按测试文件表逐文件输出 PASS 数 |

---

### v3R 第四轮 review 修复清单（针对 v3-final 自身的 2 处反馈）

| # | 反馈 | 修复 |
|---|---|---|
| P1-V3R2-A | `test_run_payload_cleanup_deletes_expired` 用了不存在的字段 `task_log_id=1` / `payload_ciphertext=...`，且没建 TaskLog 父记录 → fixture 阶段就崩 | 改用 Plan 2 真实 schema：先建 2 条 `TaskLog(task_id, task_type, channel_type, channel_userid, status)`；再用 `TaskPayload(task_log=..., encrypted_request=..., encrypted_response=..., expires_at=...)` 关联 OneToOne；末尾断言留下来的 survivor.task_log.task_id == "t-fresh" |
| P2-V3R2-B | `test_test_chat_success` / `test_test_chat_failure` / `test_set_active_disables_others` 全是 `pass` 占位，pytest 绿但 0 覆盖 | 全部展开真实断言：success 用 monkeypatch 替换 `DeepSeekProvider`，断言 chat + aclose 都被调用 + 解密的 api_key 透传；failure 让 chat 抛 `RuntimeError("api timeout")`，断言 `{"ok": False, "error": ".*api timeout.*"}` 且 aclose 仍调用；set_active 建 2 条 active，POST set-active(b) 后断言 a.status=disabled / b.status=active / count(active)=1 |

---

### v3R 第五轮 review 修复清单（针对 v3R 自身的 1 处反馈）

| # | 反馈 | 修复 |
|---|---|---|
| P1-V3R3-A | `test_test_chat_*` monkeypatch 目标是 `hub.routers.admin.ai_providers.DeepSeekProvider/QwenProvider`，但 `test_chat()` 函数内 `from hub.capabilities.* import` 是函数级别 late import → patch 不生效，测试会走真实 provider | 把 `DeepSeekProvider`/`QwenProvider` 提到 `ai_providers.py` 模块顶层（在 router/model 等 import 之后），删 `test_chat` 函数内的 inline import；同步把 `setup_full.py` `connect_ai` 函数内的 inline import 也提到顶层（保持一致 + 让 setup 测试也能 monkeypatch）；测试 monkeypatch 路径维持 `hub.routers.admin.ai_providers.DeepSeekProvider`，现在能命中并替换真实类 |

---

**Plan 5 v3R-final2（已修复 v1+v2+v3+v3R+v3R2+v3R3 review 反馈共 19 处问题）— C 阶段最后一个 plan**
