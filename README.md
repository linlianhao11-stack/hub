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

### 本地开发

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
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
