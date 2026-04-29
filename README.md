# HUB 数据中台

HUB 是"上接多渠道、下连多业务系统"的业务中台。详见 [设计文档](docs/superpowers/specs/2026-04-27-hub-middleware-design.md)。

## 快速开始

### 前置
- Docker + OrbStack
- Python 3.11+（仅本地开发用）
- Node 20+（仅前端开发用）

### 部署

```bash
# 1. 配置 .env
cp .env.example .env
# 编辑 .env，至少填好 HUB_MASTER_KEY（用 openssl rand -hex 32 生成）

# 2. 启动 OrbStack（如未启动）
orb start

# 3. **首次部署或拉新代码后**：先 rebuild 镜像，再起容器
#    （docker compose up -d 默认复用本地缓存镜像；如果代码新增了路由/迁移
#    而镜像未重新 build，会出现 confirm-final 路由 404 或迁移表缺失等问题）
docker compose build hub-gateway hub-worker hub-migrate
docker compose up -d

# 4. 看启动日志拿初始化 token
docker compose logs hub-gateway | grep -A5 "初始化 Token"

# 5. 浏览器访问 http://<host>:8091/setup
#    粘贴上面的 token，按向导走完

# 拉新代码升级
git pull
docker compose build hub-gateway hub-worker hub-migrate
docker compose up -d  # 自动重建有变化的容器
```

### 后台改了配置之后

worker 进程在启动时一次性读取 ERP ApiKey、AI Provider 等配置构造客户端，**运行中不会自动重载**。
所以以下 admin 后台操作之后必须重启 worker 才能让机器人立即用上新配置：

| admin 后台操作 | 需要重启 |
|---|---|
| 改 ERP ApiKey / 改 base_url / 停用 ERP | ✅ `docker compose restart hub-worker` |
| 切换 active AI Provider / 改 AI ApiKey | ✅ `docker compose restart hub-worker` |
| 钉钉应用改 AppKey/AppSecret/robot_id | ❌ gateway 后台 task 自动 reload，无需重启 |
| 用户/角色/权限/账号关联 | ❌ 即时生效（每次请求查 DB） |
| 系统设置 key-value（告警接收人 / TTL 等） | ❌ 调用方读 DB 时即时生效 |

gateway 进程不持有 ERP/AI 凭证（鉴权 cookie 走 ERP /auth/me 即时校验），所以这两类改动不影响 gateway。

### 本地开发

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .   # 全量 lint（含 main.py / worker.py）
```

### 项目结构

```
hub/
├── backend/         # FastAPI gateway + worker
├── frontend/        # Vue 3 SPA（setup wizard + admin）
├── docs/            # 设计文档与实施计划
├── docker-compose.yml
└── .env.example
```

详细文档见 `docs/`。
