# Plan 2 端到端验证记录

**日期：** 2026-04-28
**执行人：** 林炼豪 + Claude

## 验证步骤

### 1. 测试套件
```bash
cd /Users/lin/Desktop/hub/backend
pytest -v
```
**结果：** ✅ **52 passed in 7.53s**

测试覆盖（按文件分组）：
| 测试文件 | 数量 | 覆盖 |
|---|---|---|
| test_config.py | 4 | Settings 必填校验 / master_key 64 hex / 完整加载 / setup_token 可选 |
| test_crypto_aes_gcm.py | 5 | round_trip / 篡改拒 / 错钥拒 / nonce 唯一 / key 长度校验 |
| test_crypto_hkdf.py | 3 | purpose 区分 / deterministic / 不同 master |
| test_models_smoke.py | 6 | 16 模型字段 + 唯一约束 + M2M + OneToOne CASCADE |
| test_ports_protocol.py | 7 | 6 Protocol 接口 import + Mock 鸭子类型 |
| test_redis_streams_runner.py | 4 | submit / 消费 / ACK / 死信流（fakeredis） |
| test_bootstrap_token.py | 5 | 生成 / 错 token / 过期 / 已用 / .env 显式覆盖 |
| test_health.py | 2 | 200 + 包含 postgres/redis 组件 |
| test_admin_key_auth.py | 3 | valid 200 / missing 401 / wrong 403 |
| test_seed.py | 4 | 6 角色 / 14 权限码 / 幂等 / platform_admin ≥ 8 perms |
| test_setup_wizard_skeleton.py | 6 | 自检 / 错 token / 正确 + session / 已初始化 404 / 一次性 / 并发 |
| test_worker_runtime.py | 3 | handler+ACK / unknown→dead / 空流=False |
| **合计** | **52** | |

### 2. docker compose 配置校验
```bash
docker compose config 2>&1 | head -10
# 输出 4 服务（postgres / redis / migrate / gateway / worker）+ depends_on chain
```
**结果：** ✅ 配置语法合法

### 3. 关键能力验证（已通过单测覆盖）
- ✅ Tortoise ORM 16 模型 schema 正确（generate_schemas + 唯一约束生效）
- ✅ AES-256-GCM 加解密（含篡改 / 错钥防护）
- ✅ HKDF 派生 + lru_cache(8) 高阶 API
- ✅ Bootstrap token 一次性消费 + 并发原子性
- ✅ Redis Streams 队列 submit/消费/ACK/死信
- ✅ FastAPI lifespan 含 init_db + run_seed + 首启动 token 自动生成
- ✅ Setup wizard /welcome /verify-token /status 三接口
- ✅ Worker handler 路由 + 未知 task 死信兜底

## 已知缺口（留 Plan 3-5 处理）

- HUB_ADMIN_KEY 自动生成：当前必须运维显式 .env 注入；未来可在首启动自动生成并打印（Plan 5）
- ERP session 鉴权（基于 ERP /auth/me 包装 HUB session）：Plan 5
- /setup/connect-erp / connect-dingtalk / connect-ai / complete 等步骤 2-6 业务逻辑：Plan 5
- 钉钉 Stream 实际连接（DingTalkStreamAdapter / Sender 实现）：Plan 3
- 完整 Web 后台 UI（用户 / 角色 / 配置 / 任务流水 / 对话监控）：Plan 5
- Plan 4 各 UseCase handler 注册到 worker.py：Plan 4

## 已在 Plan 2 实现（不再列为缺口）

- ✅ 自动生成 setup token 并打印日志：Task 9 lifespan 内置（数据库为空 + 无未使用 token 时触发）
- ✅ 启动时跑迁移：hub-migrate 一次性容器（gateway/worker depends_on completed_successfully）
- ✅ 启动时跑种子：lifespan 内调用 run_seed（幂等，6 角色 + 14 权限码）
- ✅ 4 容器 docker-compose（postgres/redis/migrate/gateway/worker）+ AOF + 资源限额 + 健康检查 depends_on chain

## Plan 2 验收

| 项 | 状态 |
|---|---|
| 测试套件 52/52 PASS | ✅ |
| 数据模型 16 表（spec §6.2）| ✅ |
| 6 端口 Protocol（spec §5）| ✅ |
| 6 预设角色 + 14 权限码（spec §7.4）| ✅ |
| 加密分级（基础/业务，AES-256-GCM + HKDF）| ✅ |
| Setup wizard 1-1.5 步骨架 + 防抢跑 token | ✅ |
| Docker 4 容器 + 资源限额 + AOF + healthcheck | ✅ |
| 端到端 docker compose up（实际启动 + 迁移 + 种子 + health + token + setup flow）| ✅ |

## 端到端 docker compose 实测记录（2026-04-28 P2 review 反馈后补）

### 步骤 0：环境
```bash
$ cat .env
HUB_MASTER_KEY=<redacted-32-byte-hex>
HUB_POSTGRES_PASSWORD=hub
```

### 步骤 1：起栈
```bash
$ docker compose down -v && docker compose up -d
[+] Running ... Container hub-hub-postgres-1 Healthy
                Container hub-hub-redis-1 Healthy
                Container hub-hub-migrate-1 Exited (0)
                Container hub-hub-gateway-1 Started
                Container hub-hub-worker-1 Started
```

### 步骤 2：4 容器状态
```
hub-hub-worker-1     Up
hub-hub-gateway-1    Up
hub-hub-postgres-1   Up (healthy)
hub-hub-redis-1      Up (healthy)
```

### 步骤 3：DB 18 张表（16 业务 + 1 M2M + 1 aerich 元数据）
```
ai_provider  audit_log  bootstrap_token  channel_app  channel_user_binding
downstream_identity  downstream_system  erp_user_state_cache  hub_permission
hub_role  hub_role_permission  hub_user  hub_user_role  meta_audit_log
system_config  task_log  task_payload  aerich
```

### 步骤 4：种子数据
```
SELECT COUNT(*) FROM hub_role        → 6
SELECT COUNT(*) FROM hub_permission  → 14
```

### 步骤 5：health endpoint
```bash
$ curl http://localhost:8091/hub/v1/health
{
  "status": "healthy",
  "components": {
    "postgres": "ok",
    "redis": "unknown",
    "dingtalk_stream": "not_started",
    "erp_default": "not_configured"
  },
  "uptime_seconds": 15,
  "version": "0.1.0"
}
```

### 步骤 6：自动生成 + 打印 setup token
```
docker logs hub-hub-gateway-1 | grep -A8 初始化
======================================================
  HUB 首次启动 - 初始化模式
  请用浏览器打开：http://<HUB-host>:8091/setup
  一次性初始化 Token（30 分钟内有效）：
      <redacted-43-char-token>
======================================================
```

### 步骤 7：setup wizard 完整 flow
```bash
$ curl http://localhost:8091/hub/v1/setup/welcome
{"checks":{"master_key":"ok","postgres":"ok","redis":"configured"},"next_step":"verify-token"}

$ curl -X POST http://localhost:8091/hub/v1/setup/verify-token \
       -H "Content-Type: application/json" \
       -d '{"token":"<redacted-43-char-token>"}'
{"session":"Lr0dhxjdFXK1EQp74pSatA"}
```

### 步骤 8：ruff 全过
```bash
$ ruff check .
All checks passed!
```

### 已修补丁（review 反馈触发）
| # | 反馈 | 修复 |
|---|---|---|
| P2-A | docker compose up 端到端未实操 | 本节完整跑通 4 容器 + 18 表 + 6 角色 + 14 权限 + health 200 + token 打印 + setup flow + verify-token 拿 session |
| P2-B | ruff 81 问题未修 | `ruff check --fix --unsafe-fixes` 修 90 处（import 排序 + 未用 import + datetime.UTC + StrEnum），重跑 52/52 PASS，0 残留 |
| P3 | 文档 12 vs 14 权限口径不一致 | 测试覆盖表统一为 14 |

### 修复发现的代码 bug
- `tortoise.exceptions.ConfigurationError: Unknown DB scheme: postgresql`
  - 根因：tortoise 不识别 `postgresql://`，要 `postgres://`
  - 修复：docker-compose.yml 3 处 + .env.example 1 处 全替换为 `postgres://`

Plan 2 真正收官，可进入 Plan 3（钉钉绑定）。
