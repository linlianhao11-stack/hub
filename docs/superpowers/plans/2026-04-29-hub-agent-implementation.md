# HUB Agent（Plan 6）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 HUB 钉钉机器人从 Plan 4 的"RuleParser+LLMParser 单步意图解析"升级成 LLM-driven Agent + tool calling，覆盖档 3 完整能力（16 tool / 合同生成 / 凭证审批 / 调价审批 / 数据分析）。

**Architecture:** ChainAgent 替代 ChainParser；ToolRegistry 自动从 Erp4Adapter 方法签名生成 LLM function schema；ChatGPT 模式三层 memory（Redis 会话 + Postgres 用户/客户/商品）；写 ERP 操作走草稿 → 业务角色 inbox 审批 → 落 ERP；失败 fallback RuleParser。Plan 1-5 代码 90% 不动，仅替换 ChainParser → ChainAgent 这一层。

**Tech Stack:** OpenAI 兼容 function calling（DeepSeek/Qwen）+ python-docx（合同模板）+ openpyxl（Excel 导出）+ Postgres（memory + 草稿）+ Redis（会话）+ Plan 5 已有的 admin / SSE / cron 基础设施。

**前置阅读：**
- [Plan 6 Design Spec](/Users/lin/Desktop/hub/docs/superpowers/specs/2026-04-29-hub-agent-design.md)
- [HUB Spec](/Users/lin/Desktop/hub/docs/superpowers/specs/2026-04-27-hub-middleware-design.md)
- [Plan 4-5（C 阶段闭环）](/Users/lin/Desktop/hub/docs/superpowers/plans/)

**前置依赖：**
- ✅ Plan 1-5 全部完成，315 单测全绿
- ⏳ ERP 仓库改 2 个 endpoint（customer-price-rules / inventory/aging，约 1.5-2.5 天）

**估时：** 5-7 周

---

## 文件结构

### 后端新增（HUB 仓库）

| 文件 | 职责 |
|---|---|
| `backend/hub/agent/__init__.py` | agent 包入口 |
| `backend/hub/agent/chain_agent.py` | 主 agent loop（5 round / 20K token / 30s timeout）|
| `backend/hub/agent/tools/__init__.py` | tool 包入口 |
| `backend/hub/agent/tools/registry.py` | ToolRegistry（自动从 fn 签名生成 schema + 权限过滤 + 调用统计）|
| `backend/hub/agent/tools/erp_tools.py` | 11 个 ERP 读 tool（search_products / get_customer_history 等）|
| `backend/hub/agent/tools/generate_tools.py` | 3 个生成型 tool（合同 / 报价 / Excel）|
| `backend/hub/agent/tools/draft_tools.py` | 3 个写草稿 tool（凭证 / 调价 / 库存调整）|
| `backend/hub/agent/tools/analyze_tools.py` | 2 个聚合分析 tool（top 客户 / 滞销商品）|
| `backend/hub/agent/memory/__init__.py` | memory 包入口 |
| `backend/hub/agent/memory/loader.py` | MemoryLoader（三层加载注入）|
| `backend/hub/agent/memory/writer.py` | MemoryWriter（异步抽事实回写）|
| `backend/hub/agent/memory/session.py` | 会话层（Redis 30min）|
| `backend/hub/agent/memory/persistent.py` | 用户/客户/商品三层（Postgres）|
| `backend/hub/agent/prompt/__init__.py` | prompt 包入口 |
| `backend/hub/agent/prompt/builder.py` | PromptBuilder（schema + 业务词典 + few-shots + memory 组装）|
| `backend/hub/agent/prompt/business_dict.py` | 业务术语词典（参考 ERP）|
| `backend/hub/agent/prompt/synonyms.py` | 同义词映射（参考 ERP）|
| `backend/hub/agent/prompt/few_shots.py` | tool calling 范例 |
| `backend/hub/agent/eval/__init__.py` | LLM eval 框架包 |
| `backend/hub/agent/eval/gold_set.yaml` | 30 条标注样本 |
| `backend/hub/agent/eval/runner.py` | eval 跑分器 |
| `backend/hub/agent/document/__init__.py` | 文档生成包 |
| `backend/hub/agent/document/contract.py` | 合同 docx 渲染（python-docx）|
| `backend/hub/agent/document/excel.py` | Excel 导出（openpyxl）|
| `backend/hub/agent/document/storage.py` | 加密存储抽象（合同 docx 文件）|
| `backend/hub/models/conversation.py` | conversation_log + tool_call_log + agent_feedback |
| `backend/hub/models/memory.py` | user_memory + customer_memory + product_memory |
| `backend/hub/models/contract.py` | contract_template + contract_draft |
| `backend/hub/models/draft.py` | voucher_draft + price_adjustment_request + stock_adjustment_request |
| `backend/hub/routers/admin/approvals.py` | 凭证 / 调价 / 库存调整 三个审批 inbox 子路由 |
| `backend/hub/routers/admin/contract_templates.py` | 合同模板管理路由 |
| `backend/hub/routers/admin/agent_feedback.py` | AI 反馈统计路由 |
| `backend/hub/cron/draft_reminder.py` | 7 天未审批草稿钉钉催促 job |
| `backend/migrations/models/2_xxx_plan6_agent_tables.py` | aerich 手写迁移（10 张表）|

### 后端修改（HUB 仓库）

| 文件 | 修改 |
|---|---|
| `backend/hub/handlers/dingtalk_inbound.py` | 把 chain_parser 替换为 chain_agent；保留 RuleParser 命令路由 + 失败兜底降级 |
| `backend/worker.py` | 注入 ChainAgent + MemoryLoader + ToolRegistry |
| `backend/hub/observability/task_logger.py` | 升级为 conversation_log + tool_call_log 双表写入 |
| `backend/hub/seed.py` | 新增 14 个权限码 + 2 个新角色（sales_lead / finance_lead） + 业务词典默认数据 |
| `backend/hub/routers/admin/dashboard.py` | 加 4 个新指标（LLM 调用 / token / 成本 / 预算） |
| `backend/main.py` | 注册 3 个新 router；启动 cron draft_reminder job |
| `backend/hub/adapters/downstream/erp4.py` | 加新 endpoint 包装：search_orders / get_order_detail / get_customer_balance / check_inventory_detail / get_inventory_aging / patch_customer_price_rule |

### 前端修改（HUB 仓库）

| 文件 | 修改 |
|---|---|
| `frontend/src/views/admin/ApprovalsView.vue` | 新增审批 inbox 主页（tabs：凭证 / 调价 / 库存调整） |
| `frontend/src/views/admin/ContractTemplatesView.vue` | 新增合同模板管理页 |
| `frontend/src/views/admin/AgentFeedbackView.vue` | 新增 AI 反馈统计页 |
| `frontend/src/views/admin/TaskDetailView.vue` | 升级：时间线显示 LLM round + tool call + 决策 thought |
| `frontend/src/views/admin/AdminLayout.vue` | 加 3 个菜单项 |
| `frontend/src/api/{approvals,contract_templates,agent_feedback}.js` | 3 个新 API 模块 |

### 后端新增（ERP 仓库）

| 文件 | 修改 |
|---|---|
| `backend/app/routers/customer_price_rules.py` | POST/PATCH 客户专属定价规则 |
| `backend/app/routers/inventory.py` | GET /inventory/aging |
| `backend/app/migrations/...` | customer_price_rule 表 |

### 测试

| 文件 | 数量 | 职责 |
|---|---|---|
| `tests/test_tool_registry.py` | 8 | schema 自动生成 / 权限过滤 / 调用统计 |
| `tests/test_chain_agent.py` | 12 | 单 round / 多 round / max rounds / clarification / fallback / token 上限 |
| `tests/test_memory_loader.py` | 8 | 三层加载 / TTL 过期 / token 截断 |
| `tests/test_memory_writer.py` | 6 | 异步抽事实 / 写库幂等 / 失败容错 |
| `tests/test_prompt_builder.py` | 6 | schema 注入 / 词典 / few-shots / 历史限制 |
| `tests/test_erp_tools.py` | 11 × 4 = 44 | 每个 tool：成功 / 权限拒 / ERP 错 / 输出 schema |
| `tests/test_generate_tools.py` | 12 | 合同模板渲染 / Excel / 占位符校验 / 文件发送 |
| `tests/test_draft_tools.py` | 9 | 三个草稿创建 / 金额上限 / 必填字段 |
| `tests/test_analyze_tools.py` | 6 | 聚合 tool 内部组合 |
| `tests/test_admin_approvals.py` | 12 | 三个 inbox 子路由：列表 / 批量通过 / 批量拒绝 / 写 ERP 错 |
| `tests/test_admin_contract_templates.py` | 8 | 上传 / 占位符校验 / 启用禁用 |
| `tests/test_admin_agent_feedback.py` | 5 | 列表 / 按时段聚合 / 按 tool 聚合 |
| `tests/test_inbound_handler_with_agent.py` | 8 | rule 命令路由 / agent 多 round / fallback / 反馈收集 |
| `tests/test_dashboard_cost_metrics.py` | 4 | LLM 调用统计 / token / 成本 / 预算预警 |
| `tests/test_eval_gold_set.py` | gold_set 30 条 | 集成 CI（满意度 < 80% 阻塞 merge） |

合计：**~150 单元测试 + 30 条 eval gold set**。

---

## Task 1：数据模型迁移 + Models 层 + tool_logger

**Files:**
- Create: `backend/hub/models/conversation.py`
- Create: `backend/hub/models/memory.py`
- Create: `backend/hub/models/contract.py`
- Create: `backend/hub/models/draft.py`
- Create: `backend/hub/observability/tool_logger.py` （Plan 5 task_logger 的姊妹模块）
- Modify: `backend/hub/models/__init__.py`（注册 9 张新表）
- Create: `backend/migrations/models/2_xxx_plan6_agent_tables.py`（手写）
- Test: `tests/test_tool_logger.py`（5 case）

- [ ] **Step 1: 写 9 张表的 ORM models**

按 spec §5 的 schema 写 Tortoise model（**已剔除 agent_feedback 表**，用户决策不做反馈）：
- `conversation_log`（PK conversation_id TEXT，索引 hub_user_id+started_at）
- `tool_call_log`（FK conversation_log，round_idx + tool_name 索引）
- `user_memory` / `customer_memory` / `product_memory`（各自的 JSONB facts 字段）
- `contract_template` / `contract_draft`
- `voucher_draft` / `price_adjustment_request` / `stock_adjustment_request`

- [ ] **Step 1.5: 写 `observability/tool_logger.py`**

参考 Plan 5 已有 `observability/task_logger.py` 的 context manager pattern。**不复用 task_logger**（task 是入站消息级，tool_call 是 round 内 tool 级；二者结构不同），但风格对齐：

```python
# hub/observability/tool_logger.py
from contextlib import asynccontextmanager
from datetime import datetime, UTC
import time
import logging
from hub.models.conversation import ToolCallLog

logger = logging.getLogger("hub.observability.tool_logger")

@asynccontextmanager
async def log_tool_call(*, conversation_id: str, round_idx: int,
                        tool_name: str, args: dict):
    """Context manager：进入时无操作，出口写一行 tool_call_log。

    用法：
        async with log_tool_call(conversation_id=..., round_idx=..., tool_name=..., args=...) as ctx:
            result = await tool.fn(...)
            ctx.set_result(result)
    """
    started = time.monotonic()
    ctx = _ToolCallContext()
    raised = None
    try:
        yield ctx
    except Exception as e:
        raised = e
        ctx._error = str(e)[:500]
    finally:
        try:
            await ToolCallLog.create(
                conversation_id=conversation_id,
                round_idx=round_idx,
                tool_name=tool_name,
                args_json=args,
                result_json=ctx._result,
                duration_ms=int((time.monotonic() - started) * 1000),
                error=ctx._error,
                called_at=datetime.now(UTC),
            )
        except Exception:
            logger.exception("tool_call_log 写入失败（不阻塞业务）")
        if raised is not None:
            raise raised


class _ToolCallContext:
    def __init__(self):
        self._result = None
        self._error = None

    def set_result(self, result):
        # truncate result_json 防 JSONB 写过大；保留 schema + 数量级
        self._result = _truncate_for_log(result, max_size_kb=10)
```

测试：
- 成功调用 → 写一行 tool_call_log
- tool 抛异常 → tool_call_log.error 有值且异常上抛
- 大 result（> 10KB）→ result_json 被截断（保留 keys + first N items）
- conversation_id 不存在 → tool_call_log 写入仍成功（无 FK 约束依赖；FK 用 conversation_id 字符串而非数字 ID）
- 并发同 conversation 多 tool → 各自独立行

- [ ] **Step 2: 写手写迁移**

aerich 不能自动检测 JSONB index / GIN，所以手写：
- 9 张表 CREATE（已剔除 agent_feedback）
- conversation_log idx_user_started
- tool_call_log idx_conv
- voucher_draft / price_adjustment_request / stock_adjustment_request idx_pending（status, created_at）

- [ ] **Step 3: 更新 conftest.py**

`TABLES_TO_TRUNCATE` 加 9 张新表（顺序：先草稿、后引用、最后 conversation_log）。

- [ ] **Step 4: 提交**

```bash
git add backend/hub/models/ backend/migrations/ backend/hub/observability/tool_logger.py \
        backend/tests/conftest.py backend/tests/test_tool_logger.py
git commit -m "feat(hub): Plan 6 Task 1（9 张新表 + 手写迁移 + tool_logger）"
```

---

## Task 2：ToolRegistry + Schema 自动生成 + 写门禁 + 实体写入

**Files:**
- Create: `backend/hub/agent/__init__.py`
- Create: `backend/hub/agent/tools/__init__.py`
- Create: `backend/hub/agent/tools/registry.py`
- Create: `backend/hub/agent/tools/types.py`（ToolType / UnconfirmedWriteToolError 等）
- Create: `backend/hub/agent/tools/confirm_gate.py`（confirmation_token 计算 / Redis 已确认动作存储）
- Create: `backend/hub/agent/tools/entity_extractor.py`（tool result 中提取 customer_id / product_id 写回 session）
- Test: `backend/tests/test_tool_registry.py`

ToolRegistry 是整个 agent 的核心。Plan 6 review v1 强化项：写门禁前置硬校验 + 实体引用写入路径。从 Python 函数签名 + docstring + type hints 自动生成 OpenAI function schema。参考 ERP `schema_registry.py` 的思路。

- [ ] **Step 1: 写测试（先定义 contract）**

```python
# tests/test_tool_registry.py
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import ToolType, UnconfirmedWriteToolError

# === schema 自动生成 + 权限过滤（4 case）===
async def test_register_extracts_openai_schema():
    """注册后 schema_for_user 返回 OpenAI function schema 格式。"""
    async def search_products(query: str, limit: int = 10) -> list[dict]:
        """搜索商品。

        Args:
            query: 搜索关键字
            limit: 最大返回数量
        """
        return []

    reg = ToolRegistry()
    reg.register("search_products", search_products,
                 perm="usecase.query_product.use",
                 tool_type=ToolType.READ,
                 description="搜索商品列表")

    schema = await reg.schema_for_user(hub_user_id=1)  # mock has_permission=True
    assert schema[0]["function"]["name"] == "search_products"
    assert schema[0]["function"]["parameters"]["required"] == ["query"]


async def test_schema_for_user_filters_by_permission():
    """没权限的 tool 不在返回列表里。"""

async def test_call_checks_permission():
    """call() 调用前先 require_permissions，缺权限抛 BizError。"""

async def test_call_handles_tool_exception():
    """tool 抛错 → tool_call_log 记 error，向上抛。"""

# === 写门禁硬校验（4 case，对应 review P1-#1）===
async def test_write_tool_without_confirmation_token_raises():
    """无 confirmation_token 调用 WRITE_DRAFT/WRITE_ERP tool → UnconfirmedWriteToolError。"""
    reg = ToolRegistry()
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT,
                 description="创建凭证草稿")
    with pytest.raises(UnconfirmedWriteToolError):
        await reg.call("create_voucher_draft", {"amount": 1000},
                       hub_user_id=1, acting_as=2,
                       conversation_id="c1", round_idx=0)

async def test_write_tool_with_wrong_token_raises():
    """confirmation_token 不匹配 → 拦截。"""
    reg = ToolRegistry()
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT,
                 description="...")
    # 先确认一个不同 args 的动作
    await reg.confirm_gate.mark_confirmed("c1", "create_voucher_draft",
                                          {"amount": 999})
    with pytest.raises(UnconfirmedWriteToolError):
        await reg.call("create_voucher_draft", {"amount": 1000},
                       hub_user_id=1, acting_as=2,
                       conversation_id="c1", round_idx=0,
                       _hidden_token="token-for-amount-999")

async def test_write_tool_with_args_changed_after_confirm_raises():
    """用户确认 args A → LLM 偷偷改成 args B 调用 → 拦截（防 args 篡改攻击）。"""

async def test_write_tool_with_correct_token_passes():
    """正确 token + 一致 args → 通过。"""
    reg = ToolRegistry()
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="...")
    args = {"amount": 1000}
    await reg.confirm_gate.mark_confirmed("c1", "create_voucher_draft", args)
    token = reg.confirm_gate.compute_token("c1", "create_voucher_draft", args)
    result = await reg.call("create_voucher_draft", {**args, "confirmation_token": token},
                            hub_user_id=1, acting_as=2,
                            conversation_id="c1", round_idx=0)
    assert result is not None

# === 实体写入路径（2 case，对应 review P2-#8）===
async def test_call_extracts_customer_id_from_result():
    """tool 返回含 customer_id → 写回 session.referenced_entities。"""
    reg = ToolRegistry()
    async def fake_search(): return {"items": [{"id": 9, "name": "阿里"}, {"id": 10}]}
    reg.register("search_customers", fake_search,
                 perm=..., tool_type=ToolType.READ, description="...")
    await reg.call("search_customers", {}, hub_user_id=1, acting_as=2,
                   conversation_id="c1", round_idx=0)
    refs = await reg.session_memory.get_entity_refs("c1")
    assert refs.customer_ids == {9, 10}

async def test_call_extracts_product_id_from_nested_result():
    """从嵌套 items[].product_id 提取。"""

# === 其他基础测试 ===
async def test_register_validates_signature():
async def test_call_validates_args_against_schema():
async def test_schema_for_user_caches_per_user():
```

合计 **13 case**。

- [ ] **Step 2: 实现 ToolRegistry**

```python
# hub/agent/tools/registry.py
from __future__ import annotations
import inspect
from typing import Callable, get_type_hints
from hub.permissions import require_permissions, has_permission
from hub.observability.tool_logger import log_tool_call

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
        self._user_schema_cache: dict[int, tuple[float, list[dict]]] = {}  # 5min TTL

    def register(self, name: str, fn: Callable, *, perm: str, description: str):
        """从签名自动抽 schema。"""
        sig = inspect.signature(fn)
        hints = get_type_hints(fn)
        params = self._build_json_schema(sig, hints)

        self._tools[name] = ToolDef(
            name=name, fn=fn, perm=perm,
            description=description,
            schema={
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": params,
                },
            },
        )

    def _build_json_schema(self, sig, hints):
        properties = {}
        required = []
        for pname, param in sig.parameters.items():
            if pname in ("self", "ctx"):
                continue
            ptype = hints.get(pname, str)
            properties[pname] = {"type": self._py_to_json_type(ptype)}
            if param.default == inspect.Parameter.empty:
                required.append(pname)
        return {"type": "object", "properties": properties, "required": required}

    def _py_to_json_type(self, t):
        # int → integer / str → string / float → number / list → array / dict → object
        ...

    async def schema_for_user(self, hub_user_id: int) -> list[dict]:
        """权限过滤后的 tool schema list。"""
        # 检 cache
        # 否则：for tool in self._tools: if has_permission(hub_user_id, tool.perm): include
        # 注意：必须 select_for_update 防并发刷 cache 出错

    async def call(self, name: str, args: dict, *, hub_user_id: int,
                   acting_as: int, conversation_id: str, round_idx: int) -> Any:
        """统一入口：require_permissions → 调 fn → 记 tool_call_log。"""
        tool = self._tools.get(name)
        if not tool:
            raise ToolNotFoundError(name)

        await require_permissions(hub_user_id, [tool.perm])

        # JSON schema validate args
        self._validate_args(args, tool.schema)

        # 调 tool（注入 acting_as 等 context）
        async with log_tool_call(
            conversation_id=conversation_id, round_idx=round_idx,
            tool_name=name, args=args,
        ) as logger:
            result = await tool.fn(**args, acting_as_user_id=acting_as)
            logger.set_result(result)
            return result
```

- [ ] **Step 3: 跑测试 + 提交**

期望：8 测试 PASS。

```bash
git add backend/hub/agent/ backend/tests/test_tool_registry.py
git commit -m "feat(hub): Plan 6 Task 2（ToolRegistry：自动 schema + 权限过滤 + 调用统计）"
```

---

## Task 3：ERP 读 tool（11 个）+ erp_tools.py

**Files:**
- Create: `backend/hub/agent/tools/erp_tools.py`
- Modify: `backend/hub/adapters/downstream/erp4.py`（加 5 个新 endpoint 包装）
- Test: `backend/tests/test_erp_tools.py`（44 case）

把 Erp4Adapter 现有 + 新加的方法包装成 11 个 tool。

- [ ] **Step 1: Erp4Adapter 加 5 个新方法**

```python
# erp4.py 追加
async def search_orders(self, *, customer_id=None, since=None, status=None,
                        acting_as_user_id, limit=20):
    return await self._act_as_get("/api/v1/orders",
                                   acting_as_user_id, params={...})

async def get_order_detail(self, order_id, *, acting_as_user_id):
    return await self._act_as_get(f"/api/v1/orders/{order_id}",
                                   acting_as_user_id)

async def get_customer_balance(self, customer_id, *, acting_as_user_id):
    return await self._act_as_get(
        f"/api/v1/finance/customer-statement/{customer_id}",
        acting_as_user_id)

async def get_inventory_aging(self, *, product_id=None, warehouse_id=None,
                              acting_as_user_id):
    """⏳ 等 ERP 加完 /api/v1/inventory/aging 后启用。"""
    return await self._act_as_get("/api/v1/inventory/aging",
                                   acting_as_user_id, params={...})

async def patch_customer_price_rule(self, *, customer_id, product_id,
                                     new_price, acting_as_user_id):
    """⏳ 等 ERP 加完 PATCH /api/v1/customer-price-rules 后启用。"""
    return await self._act_as_patch("/api/v1/customer-price-rules", ...)
```

- [ ] **Step 2: 写 erp_tools.py 11 个 tool**

```python
# hub/agent/tools/erp_tools.py
from hub.agent.tools.registry import tool_registry  # 全局 registry

# 每个 tool 是模块级 async 函数 + 显式 type hints + docstring
# registry 通过装饰器或显式 register 都行；推荐显式 register

async def search_products(query: str, *, acting_as_user_id: int,
                          limit: int = 10) -> dict:
    """搜索商品。

    Args:
        query: 关键字（自动中英分词）
        limit: 最多返回多少个
    """
    erp = current_erp_adapter()
    return await erp.search_products(query=query, acting_as_user_id=acting_as_user_id)


async def search_customers(...) -> dict: ...
async def get_product_detail(...) -> dict: ...
async def get_customer_history(...) -> dict: ...
async def check_inventory(...) -> dict: ...
async def search_orders(...) -> dict: ...
async def get_order_detail(...) -> dict: ...
async def get_customer_balance(...) -> dict: ...
async def get_inventory_aging(...) -> dict: ...

def register_all(registry: ToolRegistry):
    registry.register("search_products", search_products,
                       perm="usecase.query_product.use",
                       description="按关键字搜索商品")
    # ... 11 个 tool
```

- [ ] **Step 3: 写测试 44 case**

每个 tool 4 个 case：
- ✅ 成功调用 + 返回正确格式
- ❌ 权限不足 → BizError
- ❌ ERP 4xx → ErpPermissionError → tool 透传
- ❌ ERP 5xx → ErpSystemError → 经熔断器

- [ ] **Step 4: 提交**

```bash
git add backend/hub/agent/tools/erp_tools.py \
        backend/hub/adapters/downstream/erp4.py \
        backend/tests/test_erp_tools.py
git commit -m "feat(hub): Plan 6 Task 3（11 个 ERP 读 tool + Erp4Adapter 5 个新 endpoint）"
```

---

## Task 4：Memory 三层（会话 + 用户 + 客户 + 商品）

**Files:**
- Create: `backend/hub/agent/memory/{session,persistent,loader,writer}.py`
- Test: `tests/test_memory_loader.py`（8 case）+ `test_memory_writer.py`（6 case）

按 spec §3.3：会话层 Redis 30min，三层 Postgres，token 上限分别 4K/1K/500/200。

- [ ] **Step 1: 会话层（Redis）**

```python
# memory/session.py
class SessionMemory:
    KEY_PREFIX = "hub:agent:conv:"
    TTL = 1800  # 30 min

    async def append(self, conversation_id: str, role: str, content: str): ...
    async def referenced_entities(self, conversation_id: str) -> EntityRefs: ...
    async def load(self, conversation_id: str) -> ConversationHistory: ...
```

- [ ] **Step 2: 用户/客户/商品三层（Postgres）**

```python
# memory/persistent.py
class UserMemory:
    async def load(self, hub_user_id: int) -> dict: ...
    async def upsert_facts(self, hub_user_id: int, facts: list[dict]): ...

class CustomerMemory:
    async def load_referenced(self, customer_ids: list[int]) -> dict[int, dict]: ...
    async def upsert_facts(self, customer_id: int, facts: list[dict]): ...

class ProductMemory: ...  # 同上
```

- [ ] **Step 3: MemoryLoader（组装注入）**

```python
class MemoryLoader:
    async def load(self, hub_user_id: int, conversation_id: str) -> Memory:
        session = await self.session.load(conversation_id)
        user = await self.user.load(hub_user_id)
        ent_refs = session.referenced_entities()
        customers = await self.customer.load_referenced(ent_refs.customer_ids)
        products = await self.product.load_referenced(ent_refs.product_ids)

        # 严格 token 上限：超出截断（保留最新）
        return Memory(
            session=session.truncate_to(token_budget=4000),
            user=user.truncate_to(token_budget=1000),
            customers={cid: m.truncate_to(500) for cid, m in customers.items()},
            products={pid: m.truncate_to(200) for pid, m in products.items()},
        )
```

- [ ] **Step 4: MemoryWriter（异步抽事实，带 should_extract gate）**

```python
class MemoryWriter:
    async def extract_and_write(self, conversation_id: str,
                                conversation_log_id: int,
                                tool_call_logs: list,
                                ai_provider) -> None:
        """对话结束后异步触发：先 should_extract gate，再 LLM mini round 抽事实回写。"""
        if not self.should_extract(tool_call_logs):
            return  # 闲聊 / 失败查询 / 无业务对话 → 跳过抽取，省成本

        prompt = build_extraction_prompt(...)
        result = await ai_provider.parse_intent(
            text=prompt,
            schema={
                "user_facts": [{"fact": "string", "confidence": "float"}],
                "customer_facts": [{"customer_id": "int", "fact": "string"}],
                "product_facts": [{"product_id": "int", "fact": "string"}],
            }
        )
        # 三个 layer 分别 upsert

    @staticmethod
    def should_extract(tool_call_logs: list) -> bool:
        """重要性 gate（review P2-#7）。任一满足即抽：
        1. ≥ 1 次 search_*/get_* tool 命中实体（result 含 customer_id/product_id）
        2. ≥ 1 次 write_draft tool 调用（凭证 / 调价 / 库存调整）
        3. ≥ 1 次 generate_* tool 调用（合同 / 报价 / Excel）
        4. round_count ≥ 4
        否则跳过抽取。"""
        if len(tool_call_logs) >= 4:
            return True
        for log in tool_call_logs:
            if log.tool_name.startswith(("create_", "generate_")):
                return True
            result = log.result_json or {}
            if "customer_id" in str(result) or "product_id" in str(result):
                return True
        return False
```

测试覆盖：
- 闲聊单 round 无 tool → `should_extract=False`，不调 LLM
- search_customers 命中 1 个客户 → `should_extract=True`，抽取并写库
- 写 tool（create_voucher_draft）→ `should_extract=True`
- 5 round 但全是 unknown intent → `should_extract=True`（长对话兜底）

- [ ] **Step 5: 引用实体写入路径（review P2-#8）**

`MemoryLoader.load_referenced` 依赖 session 中已有的 `referenced_entities`。这个写入由 **Task 2 的 ToolRegistry.call** 在 tool 返回后统一提取（见 Task 2 entity_extractor.py）。Task 4 这边的工作：

```python
# memory/session.py
class SessionMemory:
    async def add_entity_refs(self, conversation_id: str, *,
                              customer_ids: set[int] = (),
                              product_ids: set[int] = ()):
        """ToolRegistry 在 call() 后调用，把本次 result 中的实体引用写回。"""
        # 用 Redis SADD `hub:agent:conv:<id>:refs:customers` / refs:products

    async def get_entity_refs(self, conversation_id: str) -> EntityRefs:
        """MemoryLoader 加载客户/商品 memory 时调用。"""
```

- [ ] **Step 5: 测试 + 提交**

测试用 fakeredis + Tortoise testcase。

```bash
git commit -m "feat(hub): Plan 6 Task 4（Memory 三层：会话 Redis + 用户/客户/商品 Postgres + 异步抽事实回写）"
```

---

## Task 5：PromptBuilder + 业务词典 + few-shots

**Files:**
- Create: `backend/hub/agent/prompt/{builder,business_dict,synonyms,few_shots}.py`
- Test: `tests/test_prompt_builder.py`（6 case）

参考 ERP `prompt_builder.py` 的组装思路；HUB 这边因为是 tool calling 不是 SQL 生成，结构略不同。

- [ ] **Step 1: business_dict.py 业务词典**

50 条核心术语：
```python
DEFAULT_DICT = {
    "压货": "库龄高的商品（查 inventory_aging）",
    "周转": "商品周转率",
    "回款": "客户应付未付的款项（查 customer_balance）",
    "上次价格": "客户最近一次成交价",
    # ... 50 条
}
```

- [ ] **Step 2: synonyms.py 同义词**

```python
DEFAULT_SYNONYMS = {
    "销售额": ["营业额", "总销售", "销售总额"],
    "客户": ["顾客", "甲方"],
    "商品": ["产品", "货品", "SKU"],
    # ... 30 条
}
```

- [ ] **Step 3: few_shots.py tool calling 范例**

```python
DEFAULT_FEW_SHOTS = [
    {
        "user": "查讯飞x5的库存",
        "expected_calls": [{"tool": "search_products", "args": {"query": "讯飞x5"}}],
    },
    {
        "user": "给阿里写讯飞x5 50 台合同 按上次价",
        "expected_calls": [
            {"tool": "search_customers", "args": {"query": "阿里"}},
            {"tool": "get_customer_history", "args": {"customer_id": "<from prev>", "product_id": "<TBD>"}},
            {"tool": "check_inventory", "args": {"product_id": "<TBD>"}},
            {"tool": "generate_contract_draft", "args": "<...>"}
        ],
    },
    # ... 8-10 条
]
```

- [ ] **Step 4: PromptBuilder 组装**

```python
class PromptBuilder:
    def build(self, *, memory: Memory, tools: list[dict],
              business_dict: dict, synonyms: dict,
              few_shots: list) -> str:
        return f"""你是 HUB 业务 Agent...

[业务词典]
{render_dict(business_dict)}

[同义词]
{render_synonyms(synonyms)}

[Few-shot 例子]
{render_few_shots(few_shots)}

[当前用户偏好]
{render_user_memory(memory.user)}

[当前对话提及的客户]
{render_customer_memory(memory.customers)}

[当前对话提及的商品]
{render_product_memory(memory.products)}

[行为准则]
1. 不确定先反问澄清
2. 写操作 generate confirm 后再调
3. 工具结果与期望不符要说出来
"""
```

- [ ] **Step 5: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 5（PromptBuilder + 业务词典 + 同义词 + few-shots）"
```

---

## Task 6：ChainAgent 主循环

**Files:**
- Create: `backend/hub/agent/chain_agent.py`
- Test: `tests/test_chain_agent.py`（12 case）

Plan 6 核心。

- [ ] **Step 1: 写测试（define behavior）**

```python
async def test_single_round_text_response():
    """LLM 直接回 text，不调 tool。"""

async def test_multi_round_tool_calls():
    """LLM 调 search_products → 拿结果 → 调 get_customer_history → 综合回。"""

async def test_max_rounds_exceeded_raises():
    """5 round 后还要调 tool → 抛 AgentMaxRoundsExceeded。"""

async def test_clarification_response():
    """LLM 输出 clarification → AgentResult.clarification。"""

async def test_token_budget_exceeded_raises():
    """单轮 LLM token 超 20K → 抛。"""

async def test_llm_timeout_raises():
    """30s 超时。"""

async def test_llm_returns_invalid_json_raises():
    """LLM 输出非法 tool call 格式 → 抛 LLMParseError。"""

async def test_tool_call_permission_denied_propagates():
    """tool 权限不足 → BizError 上抛由 handler 翻译。"""

async def test_tool_exception_returns_to_llm_for_recovery():
    """tool 调用 ERP 5xx → 把错误注入 message → LLM 再 round 重试或反问。"""

async def test_writes_conversation_log():
    """完成后写 conversation_log。"""

async def test_records_tokens_used():
    """conversation_log.tokens_used 等于 LLM 返回的 usage 累加。"""

async def test_concurrent_calls_use_separate_conversation_ids():
    """并发用户的对话不串。"""
```

- [ ] **Step 2: 实现 ChainAgent + ContextBuilder（调用前裁剪）**

按 spec §3.2 实现。**review P2-#5 关键**：必须有 ContextBuilder 在每 round 调 LLM 前估算 + 裁剪，不能等响应回来才发现超 context。

```python
# hub/agent/context_builder.py
import tiktoken  # 用 cl100k_base 做估算（DeepSeek/Qwen 都接近这个 tokenizer）

class ContextBuilder:
    def __init__(self, budget_token: int = 18_000, encoding="cl100k_base"):
        self._enc = tiktoken.get_encoding(encoding)
        self.budget = budget_token

    async def build_round(self, *, round_idx, base_memory, tools_schema,
                          conversation_history, latest_user_message,
                          budget_token=None) -> list[dict]:
        budget = budget_token or self.budget

        # 1. 必保层（固定塞入）
        sections = []
        sections.append(("system_prompt", build_system_prompt(base_memory.user, tools_schema), priority=10))

        # 2. 必保 + 当前 round 上下文
        if latest_user_message:
            sections.append(("user_msg", latest_user_message, priority=10))
        # 最近 1 round LLM 输出 + tool result
        sections.append(("recent_round", conversation_history[-2:], priority=10))

        # 3. 高优先级：客户 / 商品 memory
        sections.append(("entity_memory", base_memory.customers + base_memory.products, priority=7))

        # 4. 中优先级：3 round 之前 tool result（摘要后注入）
        old_results = self._summarize_old_tool_results(conversation_history[:-2])
        sections.append(("old_results_summary", old_results, priority=5))

        # 5. 低优先级：4 round 之前对话历史（压成 1 句/round）
        old_history = self._summarize_old_history(conversation_history[:-4])
        sections.append(("old_history_summary", old_history, priority=2))

        # 6. 估算 + 裁剪
        return self._fit_to_budget(sections, budget)

    def _count_tokens(self, content: str | list) -> int:
        if isinstance(content, str):
            return len(self._enc.encode(content))
        # list of messages
        return sum(self._count_tokens(m.get("content", "")) for m in content)

    def _fit_to_budget(self, sections, budget):
        """从低优先级开始裁，直到 total ≤ budget。"""
        sections.sort(key=lambda x: x[2])  # 低优先级排前
        while sum(self._count_tokens(c) for _, c, _ in sections) > budget:
            if not sections:
                raise PromptTooLargeError("即使裁完所有可裁内容仍超出 budget")
            # 裁掉优先级最低的
            sections.pop(0)
        return self._compose_messages(sections)

    def _summarize_old_tool_results(self, history):
        """旧 tool result 压缩：单条 > 500 token 的，保留 type/count，删 items 明细。"""

    def _summarize_old_history(self, history):
        """4 round 前的 tool calls 压成 1 句：'调了 search_customers 拿到 3 个候选'。"""
```

ChainAgent 主循环要点：
- 每 round 入口先调 `ContextBuilder.build_round` 拿裁剪后的 messages
- LLM 调用 `asyncio.wait_for(timeout=30)`
- tool call 抛 LLMServiceError / 5xx → 注入 error 让 LLM 重试或放弃
- tool call 抛 UnconfirmedWriteToolError → error message 注入 LLM（让它走"先用 text 预览给用户"路径）
- max_rounds=5 / budget_token=18000 / timeout=30 都从 system_config 读，可调

- [ ] **Step 3: 测试新增**

补 Task 6 测试（P2-#5 review 要求的"大 tool result 测试"）：

```python
async def test_large_tool_result_summarized_before_next_round():
    """tool 返 1000 个商品 → 下 round prompt 中的 result 被摘要（不全量塞）。"""

async def test_token_overflow_truncates_low_priority_first():
    """memory 多客户 / 长历史 → 优先裁 old_history_summary，必保最近 round。"""

async def test_prompt_too_large_after_max_truncation_raises():
    """裁完仍超 budget（罕见）→ PromptTooLargeError → fallback rule。"""
```

测试合计：原 12 + 新 4 = **16 case**。

- [ ] **Step 4: 提交**

```bash
git add backend/hub/agent/chain_agent.py backend/hub/agent/context_builder.py \
        backend/tests/test_chain_agent.py
git commit -m "feat(hub): Plan 6 Task 6（ChainAgent + ContextBuilder 调用前 token 裁剪）"
```

---

## Task 7：生成型 tool（合同 / Excel / 报价）+ DingTalkSender.send_file

**Files:**
- Create: `backend/hub/agent/tools/generate_tools.py`
- Create: `backend/hub/agent/document/{contract,excel,storage}.py`
- Modify: `backend/hub/adapters/channel/dingtalk_sender.py`（新增 send_file + 媒体上传）
- Test: `tests/test_generate_tools.py`（10 case）+ `tests/test_dingtalk_sender_file.py`（5 case）

- [ ] **Step 1: contract.py 合同模板渲染**

```python
class ContractRenderer:
    async def render(self, *, template_id: int, customer: dict,
                     items: list[dict], extras: dict) -> bytes:
        """从合同模板 docx + 数据 → 渲染 docx 字节流。"""
        # 1. 从 contract_template 表查 template
        # 2. 解 template 文件（python-docx）
        # 3. 替换 {{customer_name}} {{items_table}} 等占位符
        # 4. 返回 docx 字节流
```

- [ ] **Step 2: excel.py Excel 导出**

参考 ERP `ai_export` 的 openpyxl 模式。

- [ ] **Step 3: storage.py 加密存储**

合同 docx 文件**加密存到 Postgres LargeObject 或外部对象存储**：
- 第一版：写到 `contract_draft.rendered_file_bytes`（bytea，AES-GCM 加密）
- 后续可以换 S3 / OSS

- [ ] **Step 4: DingTalkSender 新增 send_file（review P1-#3）**

钉钉文件下发是**两步**：媒体上传拿 media_id → batchSend 用 sampleFile：

```python
# hub/adapters/channel/dingtalk_sender.py 追加
class DingTalkSender:
    async def send_file(self, *, dingtalk_userid: str, file_bytes: bytes,
                        file_name: str, file_type: str = "docx") -> None:
        """发文件给单个用户（合同 docx / Excel / PDF 等）。"""
        media_id = await self._upload_media(file_bytes, file_name, file_type)
        await self._send_oto(
            user_ids=[dingtalk_userid],
            msg_key="sampleFile",
            msg_param={"mediaId": media_id, "fileName": file_name, "fileType": file_type},
        )

    async def _upload_media(self, file_bytes: bytes, file_name: str,
                             file_type: str, *, max_retry: int = 1) -> str:
        """调 https://oapi.dingtalk.com/media/upload 拿 media_id。

        - 5xx → 重试 max_retry 次
        - 4xx（鉴权失败 / 文件类型不支持）→ 立即抛 DingTalkSendError
        - 文件大小 > 20MB → 立即抛（钉钉硬上限）
        """
        if len(file_bytes) > 20 * 1024 * 1024:
            raise DingTalkSendError("文件超过钉钉 20MB 上限")
        token = await self._get_access_token()
        url = "https://oapi.dingtalk.com/media/upload"
        for attempt in range(max_retry + 1):
            try:
                files = {"media": (file_name, file_bytes,
                                    f"application/{file_type}")}
                r = await self._client.post(
                    url, params={"access_token": token, "type": "file"},
                    files=files,
                )
                if r.status_code == 200:
                    body = r.json()
                    if body.get("errcode") == 0:
                        return body["media_id"]
                    raise DingTalkSendError(f"上传失败: {body}")
                if r.status_code >= 500 and attempt < max_retry:
                    continue  # 5xx 重试
                raise DingTalkSendError(f"上传 {r.status_code}: {r.text[:200]}")
            except httpx.RequestError as e:
                if attempt < max_retry:
                    continue
                raise DingTalkSendError(f"网络错误: {e}") from e
```

测试（5 case）：
- 上传 + batchSend 全成功（mock httpx）
- 上传 5xx → 重试 1 次成功
- 上传 4xx（鉴权失败）→ 立即抛错不重试
- 文件 > 20MB → 立即抛 DingTalkSendError
- batchSend 失败 → 抛 DingTalkSendError

- [ ] **Step 5: generate_tools.py 三个 tool**

```python
async def generate_contract_draft(template_id: int, customer_id: int,
                                  items: list[dict], extras: dict = None,
                                  *, acting_as_user_id, hub_user_id, conversation_id) -> dict:
    """生成合同草稿 docx，发钉钉给请求人。"""
    customer = await erp.get_customer(customer_id, ...)
    docx_bytes = await ContractRenderer().render(...)
    storage_key = await storage.put(docx_bytes, encrypted=True)

    draft = await ContractDraft.create(
        template_id=template_id, requester_hub_user_id=hub_user_id,
        customer_id=customer_id, items=items,
        rendered_file_storage_key=storage_key,
        conversation_id=conversation_id,
    )
    # 通过 DingTalkSender.send_file 发文件给用户（注意 await）
    binding = await ChannelUserBinding.filter(hub_user_id=hub_user_id, status="active").first()
    await sender.send_file(
        dingtalk_userid=binding.channel_userid,
        file_bytes=docx_bytes,
        file_name=f"销售合同_{customer['name']}_{date.today()}.docx",
        file_type="docx",
    )
    return {"draft_id": draft.id, "file_sent": True}

async def generate_price_quote(...) -> dict: ...

async def export_to_excel(table_data: list[dict], file_name: str,
                          *, hub_user_id, ...) -> dict: ...
```

测试覆盖 generate_contract_draft 端到端：
- 模板渲染成功 + send_file 调用 + 文件名规范
- send_file 失败（钉钉宕机）→ 草稿仍持久化，但抛错让 worker 转死信
- 模板渲染失败（占位符缺）→ 提前拒绝，不调 send_file

- [ ] **Step 5: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 7（生成型 tool：合同 docx / 报价 / Excel；加密存储）"
```

---

## Task 8：写草稿 tool（凭证 / 调价 / 库存）+ 审批 inbox

**Files:**
- Create: `backend/hub/agent/tools/draft_tools.py`
- Create: `backend/hub/routers/admin/approvals.py`
- Test: `tests/test_draft_tools.py`（9）+ `test_admin_approvals.py`（12）

- [ ] **Step 1: draft_tools.py 三个写草稿 tool**

```python
async def create_voucher_draft(voucher_data: dict, rule_matched: str,
                                *, hub_user_id, conversation_id, ...) -> dict:
    """生成凭证草稿，挂会计审批 inbox。"""
    # 1. 校验 voucher_data 必填字段
    # 2. 校验金额 ≤ 金额硬上限（system_config.max_voucher_amount，默认 ¥1M）
    # 3. 创建 VoucherDraft 记录
    # 4. 钉钉发提醒给会计："今天有 N 张凭证待审"（用 cron 每天 EOD 汇总）
    # 5. 返回 {draft_id, approval_url, message_for_user}

async def create_price_adjustment_request(...) -> dict: ...
async def create_stock_adjustment_request(...) -> dict: ...
```

- [ ] **Step 2: admin/approvals.py 三个 inbox 子路由（review P1-#4 用 ERP batch-approve 单调用）**

ERP 已有 `POST /api/v1/vouchers/batch-approve` body=`{voucher_ids: [int]}`，返
`{success: [int], failed: [{id, reason}]}`，事务内逐条审。**HUB 一次调用即可**，不要循环 N 次。

流程：
```
HUB voucher_draft 表（状态 pending）
   ↓ admin 在 inbox 勾选 N 张 → 点"批量通过"
HUB POST /admin/approvals/voucher/batch-approve { draft_ids: [...] }
   ↓
HUB 内部：
  1. 取出对应 draft 的 voucher_data，先批量 POST /api/v1/vouchers
     创建 ERP voucher（取得 erp_voucher_ids）
  2. 一次性调 ERP /api/v1/vouchers/batch-approve { voucher_ids: erp_voucher_ids }
  3. 把 ERP 返的 success/failed 反向写回 HUB voucher_draft.status
     （成功 → approved；失败 → 仍 pending + rejection_reason）
  4. 返 admin UI: { approved: [...], failed: [{ draft_id, reason }] }
```

```python
@router.get("/voucher", deps=[require_hub_perm("usecase.create_voucher.approve")])
async def list_voucher_drafts(status: str = "pending", ...): ...

@router.post("/voucher/batch-approve",
             deps=[require_hub_perm("usecase.create_voucher.approve")])
async def batch_approve_vouchers(req: BatchApproveRequest, request: Request):
    """批量通过：先 POST /vouchers 批量创建，再调 ERP batch-approve（单次调用，事务）。"""
    drafts = await VoucherDraft.filter(id__in=req.draft_ids, status="pending").all()
    if len(drafts) != len(req.draft_ids):
        raise HTTPException(400, "包含已审或不存在的 draft_id")

    # Step 1: 批量创建 ERP voucher（每条单独 POST，因为 ERP 暂没批量创建接口）
    # 后续可优化：在 ERP 加 POST /api/v1/vouchers/batch-create
    erp_voucher_ids = []
    creation_failures = []
    for d in drafts:
        try:
            r = await erp.create_voucher(d.voucher_data, acting_as_user_id=...)
            erp_voucher_ids.append({"draft_id": d.id, "erp_id": r["id"]})
        except (ErpAdapterError, ErpSystemError) as e:
            creation_failures.append({"draft_id": d.id, "reason": str(e)})

    # Step 2: 一次性调 ERP batch-approve（事务）
    if erp_voucher_ids:
        result = await erp.batch_approve_vouchers(
            voucher_ids=[x["erp_id"] for x in erp_voucher_ids],
            acting_as_user_id=...,
        )
        # result = {success: [...], failed: [{id, reason}]}
        approved_set = set(result["success"])
        failed_map = {f["id"]: f["reason"] for f in result["failed"]}
        for x in erp_voucher_ids:
            d = next(d for d in drafts if d.id == x["draft_id"])
            if x["erp_id"] in approved_set:
                d.status = "approved"
                d.erp_voucher_id = x["erp_id"]
                d.approved_by_hub_user_id = request.state.hub_user.id
                d.approved_at = now_utc()
            else:
                d.rejection_reason = failed_map.get(x["erp_id"], "ERP 拒绝（未知原因）")
            await d.save()

    # 写 audit_log
    await AuditLog.create(...)

    return {
        "approved_count": len([x for x in erp_voucher_ids if x["erp_id"] in approved_set]),
        "failed": creation_failures + [
            {"draft_id": x["draft_id"], "reason": failed_map.get(x["erp_id"])}
            for x in erp_voucher_ids if x["erp_id"] not in approved_set
        ],
    }


@router.post("/voucher/batch-reject")
async def batch_reject_vouchers(...): ...

# /price /stock 类似（这两个先不用 ERP batch endpoint，因 ERP 暂无对应 batch；
#  Plan 6 Task 18 ERP 改动里加 customer-price-rules 时考虑加 batch）
```

**幂等性**：
- HUB voucher_draft.status 用乐观锁（filter status="pending" 同时 update）
- 同一个 draft_id batch 两次 → 第二次 filter 返 0（status 已变）→ 400 拒绝
- ERP voucher 创建失败时 voucher_draft.status 保持 pending（不写 erp_voucher_id），下次审批仍可重试

- [ ] **Step 3: 测试 12 case**

涵盖：
- 创建草稿（3 种）+ 金额硬上限拦截 + 必填字段校验
- 批量通过：ERP 全成功 / ERP 部分失败 / ERP 5xx 全失败
- 批量通过含已审 draft → 400
- 批量拒绝
- 无审批权限 → 403
- 同 draft_id 并发批量审批 → 第二次 400

- [ ] **Step 4: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 8（凭证/调价/库存调整草稿 tool + 审批 inbox 三路由 + 批量审批）"
```

---

## Task 9：聚合分析 tool

**Files:**
- Create: `backend/hub/agent/tools/analyze_tools.py`
- Test: `tests/test_analyze_tools.py`（6）

档 3 高级能力：内部组合多个读 tool，HUB 端做聚合，**不直连 ERP DB**。

- [ ] **Step 1: 写两个聚合 tool（review P2-#6 bounded pagination）**

聚合 tool 不能无限拉 ERP 订单（1000+ 单聚合慢且部分页）。加硬上限 + partial_result 标记：

```python
async def analyze_top_customers(period: str = "last_month", top_n: int = 10,
                                 *, acting_as_user_id, ...) -> dict:
    """top N 客户销售排行（bounded）。"""
    MAX_ORDERS = 1000
    MAX_PERIOD_DAYS = 90
    PER_PAGE = 200

    days = min(parse_period_days(period), MAX_PERIOD_DAYS)
    partial_period = (parse_period_days(period) > MAX_PERIOD_DAYS)

    orders = []
    page = 1
    truncated = False
    while len(orders) < MAX_ORDERS:
        resp = await erp.search_orders(
            since=now_utc() - timedelta(days=days),
            page=page, page_size=PER_PAGE,
            acting_as_user_id=acting_as_user_id,
        )
        orders.extend(resp["items"])
        if len(resp["items"]) < PER_PAGE:
            break
        page += 1
    if len(orders) >= MAX_ORDERS:
        # 还有更多 → 标记 truncated
        truncated = (resp.get("total", len(orders)) > MAX_ORDERS)
        orders = orders[:MAX_ORDERS]

    # 聚合 group by customer
    aggregated = sorted(
        [{"customer_id": cid, "total": sum(o["total"] for o in orders if o["customer_id"] == cid),
          "order_count": ...} for cid in set(o["customer_id"] for o in orders)],
        key=lambda x: x["total"], reverse=True,
    )[:top_n]

    return {
        "items": aggregated,
        "partial_result": truncated or partial_period,
        "data_window": f"近 {days} 天，{len(orders)} 单",
        "notes": (
            "结果不完整：实际订单超 1000 单，仅基于最近 1000 单聚合"
            if truncated else
            f"实际请求 period 超 {MAX_PERIOD_DAYS} 天，已截断到最近 {MAX_PERIOD_DAYS} 天"
            if partial_period else
            None
        ),
    }


async def analyze_slow_moving_products(threshold_days: int = 90,
                                        *, acting_as_user_id, ...) -> dict:
    """库龄超 N 天的滞销商品（依赖 ERP /api/v1/inventory/aging，Task 18 加）。"""
    aging = await erp.get_inventory_aging(
        threshold_days=threshold_days,
        acting_as_user_id=acting_as_user_id,
    )
    # ERP /aging 端点本身已聚合，HUB 只是过滤 + 排序
    items = sorted(
        [p for p in aging["items"] if p["age_days"] >= threshold_days],
        key=lambda p: p["stock_value"], reverse=True,
    )[:50]
    return {"items": items, "partial_result": False}
```

agent system prompt 加：当 tool 返 `partial_result=True` 时，**必须**在最终回复中明确告诉用户"结果不完整，仅基于 X"，不能掩盖。

测试覆盖：
- ≤ 200 单 → 一页拉完，partial_result=False
- 1500 单 → 拉到 1000 截断，partial_result=True，notes 含"超 1000 单"
- 用户问 "今年" → period 截断到 90 天，notes 含"截断到 90 天"

**长期改进**（不在 Plan 6 范围）：spec §14 提议 ERP 加 analytics endpoint（数据库 GROUP BY 直接出聚合结果）让 HUB 不做聚合。

- [ ] **Step 2: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 9（聚合分析 tool：top 客户 + 滞销商品）"
```

---

## Task 10：Inbound Handler 升级（ChainAgent + 兜底）

**Files:**
- Modify: `backend/hub/handlers/dingtalk_inbound.py`
- Modify: `backend/worker.py`
- Test: `tests/test_inbound_handler_with_agent.py`（8）

Plan 4 的 ChainParser 路径替换为 ChainAgent；保留 RuleParser 命令路由 + 失败兜底。

- [ ] **Step 1: 修改 inbound handler 路径**

```python
# 原：rule miss → ChainParser.parse → 单步执行 UseCase
# 新：rule miss → ChainAgent.run → multi-round + tool calls

async def handle_inbound(...):
    # ... rule 命令路由（/绑定 /解绑 /帮助）保持不变
    # ... identity_service.resolve 不变
    # ... pending_choice / pending_confirm 数字编号回路保持不变（重要：UX 沉淀）

    # 业务路径：替换 ChainParser 为 ChainAgent
    try:
        agent_result = await chain_agent.run(
            user_message=content,
            hub_user_id=resolution.hub_user_id,
            conversation_id=conversation_id,
            acting_as=resolution.erp_user_id,
        )
    except (LLMServiceError, AgentMaxRoundsExceeded, TokenBudgetExceededError) as e:
        # B 兜底：降级 RuleParser
        logger.warning(f"agent 失败 {e}，降级 RuleParser")
        rule_intent = await rule_parser.parse(content, context=parser_context)
        if rule_intent.intent_type != "unknown":
            await execute_intent_via_rule(rule_intent, ...)
            record["fallback"] = "rule"
            record["final_status"] = "fallback_to_rule"
            return
        await sender.send_text(..., "AI 处理出了点问题，请用更明确的方式描述")
        record["final_status"] = "failed_system_final"
        return

    # agent 成功
    if agent_result.is_clarification:
        await sender.send_text(..., agent_result.text)
    elif agent_result.is_text:
        await sender.send_text(..., agent_result.text)
    # tool 已经在 agent 内部把文件等发了
    record["final_status"] = "success"
```

- [ ] **Step 2: worker.py 注入新依赖**

```python
# worker.py 加
from hub.agent.chain_agent import ChainAgent
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools import erp_tools, generate_tools, draft_tools, analyze_tools
from hub.agent.memory.loader import MemoryLoader
from hub.agent.prompt.builder import PromptBuilder

registry = ToolRegistry()
erp_tools.register_all(registry)
generate_tools.register_all(registry)
draft_tools.register_all(registry)
analyze_tools.register_all(registry)

memory_loader = MemoryLoader(redis=redis_client)
prompt_builder = PromptBuilder()

chain_agent = ChainAgent(
    llm=ai_provider,
    tools=registry,
    memory_loader=memory_loader,
    prompt_builder=prompt_builder,
    max_rounds=5,
)

# inbound handler 注入 chain_agent + rule_parser（fallback 用）
```

- [ ] **Step 3: 测试 + 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 10（dingtalk_inbound 接 ChainAgent + RuleParser 兜底降级）"
```

---

## Task 11：合同模板管理 + admin UI

**Files:**
- Create: `backend/hub/routers/admin/contract_templates.py`
- Create: `frontend/src/views/admin/ContractTemplatesView.vue`
- Test: `tests/test_admin_contract_templates.py`（8）

- [ ] **Step 1: contract_templates.py CRUD 路由**

```python
@router.post("", deps=[platform.contract_templates.write])
async def upload_template(file: UploadFile, name: str, template_type: str): ...

@router.get("")
async def list_templates(): ...

@router.get("/{template_id}/placeholders")
async def get_placeholders(template_id): ...  # 解析 docx 找出所有 {{xxx}}

@router.put("/{template_id}")
async def update_template(template_id, ...): ...

@router.post("/{template_id}/disable")
async def disable_template(template_id): ...
```

- [ ] **Step 2: ContractTemplatesView.vue 前端**

- 上传 docx + 显示自动解析的占位符 + 描述输入 + 启用/禁用列表
- 复用 Plan 5 的 AppCard / AppTable / AppModal

- [ ] **Step 3: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 11（合同模板管理：上传 docx + 自动占位符识别 + admin UI）"
```

---

## Task 12：审批 Inbox UI

**Files:**
- Create: `frontend/src/views/admin/ApprovalsView.vue`（带 tabs）
- Modify: `frontend/src/views/admin/AdminLayout.vue`（加菜单）
- Modify: `frontend/src/api/approvals.js`

- [ ] **Step 1: ApprovalsView.vue 带 tabs**

按 tabs 切：凭证 / 调价 / 库存调整。每个 tab 列表 + 批量勾选 + 详情抽屉 + 批量通过/拒绝按钮。

- [ ] **Step 2: 菜单 + 路由**

AdminLayout 加：
```js
{ to: '/admin/approvals', label: '待审批', icon: ClipboardCheck,
  perm: 'usecase.create_voucher.approve|usecase.adjust_price.approve|usecase.adjust_stock.approve' }
```

- [ ] **Step 3: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 12（审批 inbox UI：三 tab + 批量勾选 + 详情抽屉）"
```

---

## Task 13：升级 task detail 显示决策链

**Files:**
- Modify: `backend/hub/routers/admin/tasks.py`（API 加 conversation_log + tool_call_log 联表查询）
- Modify: `frontend/src/views/admin/TaskDetailView.vue`

- [ ] **Step 1: API 升级**

`GET /admin/tasks/{task_id}` 返回：
```json
{
  "task_log": {...},   # 原有
  "conversation_log": {  # 新加
    "rounds_count": 4,
    "tokens_used": 8420,
    "tokens_cost_yuan": 0.0234
  },
  "tool_calls": [  # 新加（按 round_idx 排序）
    {"round_idx": 0, "tool_name": "search_customers", "args": {...}, "result": {...}, "duration_ms": 230},
    {"round_idx": 1, "tool_name": "get_customer_history", ...}
  ]
}
```

- [ ] **Step 2: 前端时间线显示**

- 时间线节点扩展：除 5 步固定流程外，增加 "LLM Round N: 决策→调 tool X" 节点
- 显示 cost

- [ ] **Step 3: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 13（task detail 显示 agent 决策链：rounds / tool calls / cost）"
```

---

## Task 14：Dashboard 加成本指标

**Files:**
- Modify: `backend/hub/routers/admin/dashboard.py`
- Modify: `frontend/src/views/admin/DashboardView.vue`

- [ ] **Step 1: API 加 4 个新指标**

```python
{
  "today_llm_calls": 247,
  "today_total_tokens": {"input": 1.2e6, "output": 4.5e5},
  "today_cost_yuan": 1.85,
  "month_to_date_cost_yuan": 18.4,
  "month_budget_yuan": 1000.0,
  "budget_used_pct": 1.84,
  "budget_alert": false,  # >80% 触发
}
```

- [ ] **Step 2: dashboard 页加 1 行 4 个新卡片 + 1 个本月预算进度条**

- [ ] **Step 3: cron job 月预算超 80% 触发钉钉告警**

借用 Plan 5 cron + alert 渠道。

- [ ] **Step 4: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 14（dashboard 加 LLM 成本指标 + 80% 预算告警）"
```

---

## Task 15：cron 草稿催促

**Files:**
- Create: `backend/hub/cron/draft_reminder.py`
- Modify: `backend/main.py`（注册 cron job）

7 天未审批的草稿，每天 09:00 钉钉提醒**请求人**确认是否仍要 / 通知**审批人** 有待审。

- [ ] **Step 1: 写 cron job**

```python
async def draft_reminder_job():
    """每天 09:00 跑：找超 7 天未审批的草稿，钉钉提醒请求人 + 审批人。"""
    cutoff = datetime.now(UTC) - timedelta(days=7)
    
    for draft_type in [VoucherDraft, PriceAdjustmentRequest, StockAdjustmentRequest]:
        old_drafts = await draft_type.filter(status="pending", created_at__lte=cutoff)
        # 按 requester_hub_user_id group
        # 通过 ChannelUserBinding 找钉钉 ID
        # 投递 dingtalk_outbound 任务
```

- [ ] **Step 2: 注册到 scheduler**

`main.py` cron scheduler 加 `at_hour(9)` 调度。

- [ ] **Step 3: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 15（cron：超 7 天未审批草稿钉钉催促）"
```

---

## Task 16：LLM Eval 框架

**Files:**
- Create: `backend/hub/agent/eval/{gold_set.yaml,runner.py}`
- Create: `tests/test_eval_gold_set.py`

agent 是非确定性系统，单测不够。eval 框架在 CI 跑 gold set，满意度 < 80% 阻 merge。

- [ ] **Step 1: gold_set.yaml 30 条**

参考 spec §10.2 的格式，标注 30 条用户故事 + 期望路径 + evaluator。

- [ ] **Step 2: runner.py**

```python
class EvalRunner:
    async def run(self, gold_set: list, agent: ChainAgent) -> EvalReport:
        for case in gold_set:
            actual = await agent.run(case["user_input"], ...)
            score = self._evaluate(case, actual)
            ...
        return EvalReport(passed=N, failed=M, satisfaction_pct=...)
```

- [ ] **Step 3: 集成 CI**

`tests/test_eval_gold_set.py` 跑一遍，断言 satisfaction ≥ 80%。

- [ ] **Step 4: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 16（LLM Eval：gold set 30 条 + 集成 CI 阈值 80%）"
```

---

## Task 17：seed.py 升级（14 权限码 + 2 新角色 + 词典）

**Files:**
- Modify: `backend/hub/seed.py`

- [ ] **Step 1: 加 14 权限码 + 2 角色（spec §8.1）**

```python
# seed.py 追加
NEW_PERMISSIONS_PLAN6 = [
    ("usecase.query_customer.use", "usecase", "query_customer", "use", ...),
    # ... 14 个
]

NEW_ROLES_PLAN6 = {
    "bot_user_sales_lead": {...},
    "bot_user_finance_lead": {...},
}
```

- [ ] **Step 2: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 17（seed 加 14 权限码 + 2 新角色 + 业务词典默认数据）"
```

---

## Task 18：ERP 仓库改动（跨仓库依赖）

**Files (ERP 仓库):**
- Create: `backend/app/routers/customer_price_rules.py`
- Modify: `backend/app/routers/inventory.py`（加 /aging）
- Migrations: customer_price_rule 表

- [ ] **Step 1: customer_price_rules.py**

```python
@router.post("")
async def create_price_rule(...): ...

@router.patch("/{rule_id}")
async def update_price_rule(...): ...

@router.get("")
async def list_price_rules(customer_id, product_id): ...
```

- [ ] **Step 2: /api/v1/inventory/aging endpoint**

按库龄聚合：
```python
@router.get("/aging")
async def inventory_aging(threshold_days: int = 90, warehouse_id: int = None):
    # 查 stock_log + 计算 age_days
    return {"items": [{product_id, sku, name, total_stock, age_days, value}]}
```

- [ ] **Step 3: 在 ERP 仓库提交**

```bash
cd /Users/lin/Desktop/ERP-4
git commit -m "feat: 加 customer-price-rules CRUD + inventory/aging（HUB Plan 6 依赖）"
```

---

## Task 19：自审 + 端到端验证 + 验证记录

- [ ] **Step 1: 跑全部测试**

```bash
cd /Users/lin/Desktop/hub/backend
.venv/bin/pytest -v
```

期望：Plan 1-5 既有 315 + Plan 6 新增 ~150 单测 + 30 条 eval gold set 全 PASS。

- [ ] **Step 2: 端到端 docker 验证**

`docker compose up -d --build` 后跑：
1. 销售在钉钉发 "给阿里写讯飞x5 50 台合同 按上次价" → 收到 docx 文件
2. 会计 "把今天差旅做凭证" → 后台收到 N 张草稿待审
3. 会计 admin 后台批量通过 → ERP `vouchers` 表新增
4. 销售主管收到调价请求审批
5. dashboard 看到 LLM 成本指标
6. task detail 看到 agent 决策链

- [ ] **Step 3: 验证记录**

文件 `docs/superpowers/plans/notes/2026-04-29-plan6-end-to-end-verification.md`，记录：
- 单测 ~470 PASS（Plan 1-5 315 + Plan 6 ~150 + eval 30）
- 端到端 6 步演练 ✅/❌
- agent 决策链溯源能力验证（任意 task 详情都能复现 LLM 推理）
- 性能：合同生成 < 30s 端到端 / dashboard < 1s
- 成本：跑 30 条 gold set 测出实际 token 消耗 → 预测 100 用户月成本

```bash
git commit -m "docs(hub): Plan 6 端到端验证记录"
```

---

## Self-Review

### Spec 覆盖检查

| Spec 章节 | Plan 任务 | ✓ |
|---|---|---|
| §1 写操作边界 B' | Task 7（生成型）+ Task 8（草稿+审批） | ✓ |
| §2 系统架构 | Task 6（ChainAgent） + Task 10（handler 接入）| ✓ |
| §3.1 Tool Registry | Task 2 | ✓ |
| §3.2 ChainAgent | Task 6 | ✓ |
| §3.3 Memory 三层 | Task 4 | ✓ |
| §3.4 失败兜底 B+D | Task 6 + Task 10 | ✓ |
| §3.5 ERP 借鉴 10 项（已剔除反馈，剩 9 项）| Task 5（词典/同义/few-shots/temp=0）+ Task 7（Excel）| ✓ |
| §4.1 合同生成场景 | Task 7 + Task 11（模板管理）| ✓ |
| §4.2 凭证审批场景 | Task 8 + Task 12（UI）| ✓ |
| §4.3 调价审批场景 | Task 8 + Task 12 | ✓ |
| §4.4 数据分析场景 | Task 9（聚合 tool）| ✓ |
| §5 数据模型 10 表 | Task 1 | ✓ |
| §6 后台 UI | Task 11 + 12 + 13 + 14 + 15 | ✓ |
| §7 钉钉端 UX | Task 10 + 14 | ✓ |
| §8 安全权限 | Task 17（seed 14 权限码）+ Task 8（金额上限）+ **Task 2（写门禁硬校验）**| ✓ |
| §9 成本控制 | Task 6（5 阈值 + 调用前裁剪）+ Task 14（仪表盘）| ✓ |
| §10 测试策略 | Task 16（eval）+ 各 Task 单测（合计 ~140）| ✓ |
| §11 不在范围 | 各 Task 没引入 | ✓ |
| §14 ERP 改动 | Task 18 | ✓ |

### Placeholder Scan

- ✓ 无 TBD / TODO / "类似 X" 占位
- ✓ 每个 Task 都有明确 Files / Steps / commit msg
- ✓ 每个 Step 都有可执行命令 / 测试断言

### 类型一致性

- ✓ ToolRegistry.call() 签名跨 Task 一致（hub_user_id / acting_as / conversation_id / round_idx）
- ✓ AgentResult 类型跨 Task 6 / Task 10 一致
- ✓ Memory dataclass 字段跨 Task 4 / Task 5 / Task 6 一致
- ✓ ToolDef.fn 必须 async + 必须有 acting_as_user_id 参数

### 范围检查

Plan 6 完成后达到：
- ✅ 钉钉机器人能接受任意自然语言任务（不限"查 X"格式）
- ✅ agent 能用 16 个 tool 多 round 推理 + 综合
- ✅ 销售场景：写合同发回手机
- ✅ 会计场景：批量审凭证
- ✅ 销售主管场景：审调价
- ✅ 管理层场景：数据分析查询
- ✅ ChatGPT 模式三层 memory 跨对话记住关键事实
- ✅ 失败兜底（B+D）保证基础查询永不失败
- ✅ 决策链可溯源（admin 后台看 agent 怎么推理的）
- ✅ 用户反馈闭环（👍/👎 + 后台统计）
- ✅ 成本可控（5 阈值 + 80% 预算预警）
- ❌ 创建 ERP 主数据 / 删除数据（明确不做）
- ❌ 跨账套（明确不做）
- ❌ Claude Memory Tool（不做，沿用 ChatGPT 模式）

### 与 Plan 1-5 接口对齐

- ✅ 复用 Plan 5 的 admin 后台 + 鉴权 cookie + SSE + cron + dashboard
- ✅ 复用 Plan 4 的 RuleParser（兜底）
- ✅ 复用 Plan 3 的 IdentityService + DingTalkSender
- ✅ 复用 Plan 2 的 task_runner + RedisStreams + 加密
- ✅ Plan 1 的 ERP-HUB 双向通信不变

### 与 D 阶段后续的接口

D 阶段（凭证自动化深度）已为 Plan 6 后续预留：
- 凭证模板规则配置（HUB 后台 + 财务一起出规则）— Plan 6 已搭草稿表 + 审批 inbox 框架
- 钉钉审批 outgoing webhook → ChannelAdapter — 当前事件订阅框架已在 Plan 5 预留
- 凭证 PDF 生成 — Plan 6 合同 docx 文件存储模式可复用

---

**Plan 6 v2 结束（应用 9 条 review 反馈，删除反馈功能，加强写门禁/裁剪/批量审批/聚合分页）**

总计：
- 19 个 Task（v1 删 Task 14 反馈收集）
- 估时 5-7 周
- ~30 个新文件 + 12 个修改文件
- ~140 单元测试 + 30 eval gold set
- 跨仓库：HUB（主）+ ERP（约 1.5-2.5 天）

## v2 Review 修复清单（review 第一轮）

| # | 反馈优先级 | 问题 | 修复 |
|---|---|---|---|
| 1 | P1 | 写操作只靠 prompt 教 LLM 自觉 | Task 2 加 ToolType 元数据 + ConfirmGate（前置硬校验 + confirmation_token + Redis 已确认动作）+ 4 case 测试（无 token / 错 token / args 篡改 / 正确通过）|
| 2 | P1 | tool_logger 缺实施来源 | Task 1 明确创建 `hub/observability/tool_logger.py`（不复用 task_logger 但风格对齐）+ 5 case 测试 |
| 3 | P1 | 合同文件发送链路不完整 | Task 7 明确 DingTalkSender.send_file 实施（媒体上传 + sampleFile + 重试 + 4xx 不重试 + 文件大小硬上限）+ 5 case 测试 |
| 4 | P1 | 批量审批循环 N 次 ERP 调用 | Task 8 改用 ERP `POST /api/v1/vouchers/batch-approve` 单次调用 + 透传 success/failed + 乐观锁防并发 |
| 5 | P2 | token 上限缺调用前裁剪策略 | Task 6 加 ContextBuilder（每 round 调 LLM 前用 tiktoken 估算 + 优先级裁剪：摘要 tool result / 裁旧 memory / 压缩历史）+ 4 case 测试 |
| 6 | P2 | 聚合分析缺分页和性能边界 | Task 9 加 bounded pagination（MAX_ORDERS=1000 / MAX_PERIOD=90d / partial_result + notes）|
| 7 | P2 | Memory 抽取每轮都跑成本偏高 | Task 4 加 should_extract gate（实体命中 / 写 tool / 长对话 任一满足才抽）|
| 8 | P2 | 引用实体没有写入路径 | Task 2 entity_extractor.py 在每次 tool call 后从 result 提取 customer_id/product_id 写回 session.referenced_entities + 2 case 测试 |
| 9 | P3 | 反馈 UX 用户决策"不要" | **完全删除**：Task 14（反馈收集）+ §6.4 AI 反馈页 + §7.2 反馈 UX + agent_feedback 表 + 5 测试；Task 编号 14-20 顺移成 14-19 |
