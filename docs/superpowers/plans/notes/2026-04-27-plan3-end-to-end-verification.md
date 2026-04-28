# Plan 3 端到端验证记录

日期：2026-04-28
执行人：Claude Opus 4.7（自动模式）

## 验证项

### 1. 单元测试（Plan 3 共 49 PASS）

```
$ cd backend && .venv/bin/pytest -q
......................................................................   [69%]
................................                                         [100%]
104 passed in 10.81s
```

| 测试文件 | 数量 | 状态 |
|---|---|---|
| `tests/test_erp4_adapter.py` | 5 | ✅ |
| `tests/test_erp_active_cache.py` | 4 | ✅ |
| `tests/test_identity_service.py` | 5 | ✅ |
| `tests/test_dingtalk_stream_adapter.py` | 3 | ✅ |
| `tests/test_dingtalk_sender.py` | 3 | ✅ |
| `tests/test_binding_service.py` | 11 | ✅ |
| `tests/test_dingtalk_inbound_handler.py` | 6 | ✅ |
| `tests/test_dingtalk_outbound_handler.py` | 3 | ✅ |
| `tests/test_internal_callbacks.py` | 4 | ✅ |
| `tests/test_dingtalk_user_sync.py` | 2 | ✅ |
| `tests/test_dingtalk_connect.py` | 3 | ✅ |
| **Plan 3 合计** | **49** | ✅ |
| Plan 2 既有测试 | 55 | ✅ |
| **总计** | **104** | ✅ |

### 2. docker compose 4 容器全绿

```
$ docker compose ps
NAME                 STATUS
hub-hub-gateway-1    Up
hub-hub-postgres-1   Up (healthy)
hub-hub-redis-1      Up (healthy)
hub-hub-worker-1     Up
```

`hub-migrate` 跑完 0_init.py + 1_plan3 后 Exited（OK，restart=no）。

### 3. /hub/v1/health 200

```
$ curl http://localhost:8091/hub/v1/health
{"status":"healthy","components":{"postgres":"ok","redis":"unknown",
  "dingtalk_stream":"not_started","erp_default":"not_configured"},
  "uptime_seconds":29,"version":"0.1.0"}
```

### 4. /hub/v1/internal/binding/confirm-final 端到端

**鉴权层**：
- 401（缺 X-ERP-Secret 头）：`{"detail":"缺少 X-ERP-Secret 头"}` ✅
- 403（X-ERP-Secret 不匹配）：`{"detail":"X-ERP-Secret 不匹配"}` ✅

**业务层**（共享密钥正确）：
- 200 首次成功创建：`{"success":true,"hub_user_id":1,"note":"created"}` ✅
- 200 同 token replay：`{"success":true,"hub_user_id":1,"note":"already_consumed"}` ✅
- 409 dingtalk B 试绑同一 ERP 用户：
  ```
  HTTP 409
  {"detail":{"error":"conflict_erp_user_owned",
    "message":"该 ERP 用户已被另一个钉钉账号占用，请联系管理员解绑后再绑。"}}
  ```
  ✅（事务回滚，token 99 未消费，用户解绑后可重试）

**DB 一致性**（confirm-final 成功后）：
```
channel_user_binding: id=1 channel_userid=m_user_a status=active
consumed_binding_token: erp_token_id=42 → hub_user_id=1
downstream_identity:    erp/100 → hub_user_id=1
```
冲突的 token 99 不在 consumed_binding_token（事务回滚验证 ✅）。

### 5. Worker 双轮询行为

```
$ docker compose logs hub-worker
INFO:hub.worker:等待初始化向导完成 [钉钉应用, ERP 下游] 配置 ...（30 秒后重试）
INFO:hub.worker:等待初始化向导完成 [钉钉应用, ERP 下游] 配置 ...（30 秒后重试）
```

未配置 ChannelApp + DownstreamSystem 时 worker 持续轮询、不进 run()、不静默 ACK
入站消息（plan v5-V5-C 修复点验证 ✅）。

### 6. Gateway 钉钉 Stream 后台轮询

ChannelApp 未配置时 gateway 启动正常，`dingtalk_stream` 组件健康检查显示 `not_started`，
不阻塞其他路由。后台 task `dingtalk_connect` 每 30 秒重试，向导写入 ChannelApp 后下次
轮询自动连接（plan v4-V4-C 修复点；具体连接落地需真实钉钉应用 AppKey/AppSecret，
依赖 dingtalk-stream SDK 的 WebSocket 长连接，无线下钉钉测试组织无法在本环境完整跑）。

### 7. 端到端钉钉绑定全流程（依赖真实钉钉测试组织 + ERP staging）

| 项目 | 状态 |
|---|---|
| `/绑定 zhangsan` → 收到 6 位绑定码 | 🟡 依赖真实钉钉应用 |
| ERP 输码 + 二次确认 → 钉钉收"绑定成功"+隐私告知 | 🟡 依赖真实钉钉应用 |
| `/解绑` → 钉钉收"已解绑" | 🟡 依赖真实钉钉应用 |
| ERP 用户禁用 → 机器人拒绝服务（10 分钟内生效） | 🟡 依赖真实钉钉应用 |
| 冲突场景（v6 已通过 confirm-final 端到端验证 409） | ✅ |
| 首次部署 docker compose up 未配钉钉 → 走向导写入 ChannelApp → gateway 自动连 Stream | 🟡 SDK 长连接需真实凭证 |

**🟡 项**：单元测试已完整覆盖（test_dingtalk_inbound_handler / test_dingtalk_connect /
test_dingtalk_user_sync），但闭环需要真实钉钉测试组织 + 钉钉机器人 AppKey/AppSecret，
本地无法静态完成。生产联调时按 Plan 8 验收清单跑。

## 已知缺口（Plan 4-5 处理）

- 自然语言意图解析（IntentParser 实现）：Plan 4
- 商品查询 / 历史价业务用例：Plan 4
- AI fallback：Plan 4
- 完整 Web 后台对话监控：Plan 5
- cron 调度器集成（每日巡检定时跑）：Plan 5
- 钉钉离职事件 SDK 订阅集成（A 路径具体接入 SDK 调用）：Plan 5（实际线上启用前）

## 期间发现 + 修复

- **第一次重启 docker compose 时 hub-gateway 跑老镜像**：未注册
  `/hub/v1/internal/binding/confirm-final`。原因：gateway/worker 镜像在
  Plan 3 路由加入前已 build，docker compose up -d 默认复用旧镜像。修复：
  `docker compose build hub-gateway hub-worker hub-migrate` 后重新 up，
  routes 全部就位。**部署 SOP 须强调 Plan 3 改完后必须重新 build 三个镜像**。

- **hub-migrate 镜像未含 Plan 3 手写迁移**：confirm-final 报
  `relation "consumed_binding_token" does not exist`。同一原因：旧镜像没有
  `1_20260428190000_plan3_consumed_token_and_downstream_unique.py` 文件。
  rebuild 后 `aerich upgrade` 输出 `Success upgrade
  1_20260428190000_plan3_consumed_token_and_downstream_unique.py`。
