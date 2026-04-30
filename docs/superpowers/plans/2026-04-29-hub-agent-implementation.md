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
- ⏳ ERP 仓库改 3 项（customer-price-rules / inventory/aging / Voucher.client_request_id 幂等键，约 2-3 天）

**估时：** 5-7 周

**代码示例约定**：

本 plan 中的代码示例分两类：
1. **完整实现**（`ToolRegistry.call` / `ConfirmGate` / `ContextBuilder` 等核心控制流）— 直接可拷贝执行，签名 + 函数体都写完整
2. **签名 sketch**（普通 tool 函数 / Memory class 等）— 只列签名 + 关键逻辑骨架，函数体内的 ERP API 调用 / DB 操作按 spec / docstring 标准实现，不在 plan 里展开
   - 标记：函数体仅 1 行 `...` 或 `pass` 时表示"sketch；按 docstring + 上文 pattern 实现"
   - 实施者参考相邻已完整实现的同类函数填充

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
| `backend/hub/models/conversation.py` | conversation_log + tool_call_log（v2 删 agent_feedback）|
| `backend/hub/models/memory.py` | user_memory + customer_memory + product_memory |
| `backend/hub/models/contract.py` | contract_template + contract_draft |
| `backend/hub/models/draft.py` | voucher_draft + price_adjustment_request + stock_adjustment_request |
| `backend/hub/routers/admin/approvals.py` | 凭证 / 调价 / 库存调整 三个审批 inbox 子路由 |
| `backend/hub/routers/admin/contract_templates.py` | 合同模板管理路由 |
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
| `frontend/src/views/admin/TaskDetailView.vue` | 升级：时间线显示 LLM round + tool call + 决策 thought |
| `frontend/src/views/admin/AdminLayout.vue` | 加 3 个菜单项 |
| `frontend/src/api/{approvals,contract_templates}.js` | 2 个新 API 模块 |

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
| `tests/test_inbound_handler_with_agent.py` | 8 | rule 命令路由 / agent 多 round / fallback / 写操作确认链路 |
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
- `voucher_draft`（**v3 round 2 新增 creating_started_at TIMESTAMPTZ NULL 字段** + status 五值：pending / creating / created / approved / rejected）
- `price_adjustment_request` / `stock_adjustment_request`（沿用 pending / approved / rejected 三值）

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
- voucher_draft.creating_started_at TIMESTAMPTZ NULL（v3 round 2 加：creating 状态租约时间戳）
- voucher_draft idx_creating_lease（status, creating_started_at）— 崩溃恢复扫 status=creating + 租约过期的草稿用
- voucher_draft.status CHECK 约束（pending / creating / created / approved / rejected 五值）

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

# === 写门禁硬校验（v5 round 2 P1：claim_action 原子领取 + token 绑 action_id）===
# 辅助：模拟 inbound 拦截 → ChainAgent 调 add_pending → 用户回'是' → confirm_all_pending
async def _confirm_one(reg, conversation_id, hub_user_id, tool_name, args):
    """模拟 ChainAgent 把单条 pending 标 confirmed；返 (action_id, token)。"""
    await reg.confirm_gate.add_pending(conversation_id, hub_user_id, tool_name, args)
    confirmed = await reg.confirm_gate.confirm_all_pending(conversation_id, hub_user_id)
    a = confirmed[-1]
    return a["action_id"], a["token"]


async def test_write_tool_without_confirmation_token_raises():
    """无 confirmation_action_id / confirmation_token → UnconfirmedWriteToolError。"""
    reg = ToolRegistry(...)
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT,
                 description="创建凭证草稿")
    with pytest.raises(UnconfirmedWriteToolError):
        await reg.call("create_voucher_draft", {"amount": 1000},
                       hub_user_id=1, acting_as=2,
                       conversation_id="c1", round_idx=0)

async def test_write_tool_with_wrong_token_raises():
    """confirmation_token 不匹配 → claim_action restore + 拦截 → 合法调用方 token 不被污染。"""
    reg = ToolRegistry(...)
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="...")
    action_id, _ = await _confirm_one(reg, "c1", 1, "create_voucher_draft",
                                       {"amount": 1000})
    # 用错的 token
    with pytest.raises(UnconfirmedWriteToolError):
        await reg.call("create_voucher_draft", {
            "amount": 1000,
            "confirmation_action_id": action_id,
            "confirmation_token": "x" * 32,
        }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)
    # 合法 token 还能用（验证 restore 起效）
    confirmed = await reg.confirm_gate.list_pending("c1", 1)  # pending 还在
    assert any(p["action_id"] == action_id for p in confirmed)


async def test_write_tool_with_args_changed_after_confirm_raises():
    """用户确认 args A → LLM 偷偷改成 args B 调用 → claim 校验 args 不一致 → 拦截 + restore。"""
    reg = ToolRegistry(...)
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="...")
    action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft",
                                           {"amount": 1000})
    # LLM 偷偷把 amount 改成 9999，token 仍然用合法的
    with pytest.raises(UnconfirmedWriteToolError):
        await reg.call("create_voucher_draft", {
            "amount": 9999,  # 篡改
            "confirmation_action_id": action_id,
            "confirmation_token": token,
        }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)
    # 用回正确 args 仍能成功（说明 token 被 restore 了）
    result = await reg.call("create_voucher_draft", {
        "amount": 1000,
        "confirmation_action_id": action_id,
        "confirmation_token": token,
    }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=1)
    assert result is not None


async def test_write_tool_with_correct_token_passes():
    """正确 (action_id, token) + 一致 args → 通过。"""
    reg = ToolRegistry(...)
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="...")
    action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft",
                                           {"amount": 1000})
    result = await reg.call("create_voucher_draft", {
        "amount": 1000,
        "confirmation_action_id": action_id,
        "confirmation_token": token,
    }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)
    assert result is not None


# === token 一次性消费（v4 第三轮 + v5 第二轮 P1：原子 claim 防并发）===
async def test_write_tool_token_is_one_time_use():
    """同 (action_id, token) 第二次调用被拒（claim 已删 confirmed[action_id]）。"""
    reg = ToolRegistry(...)
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="...")
    action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft",
                                           {"amount": 1000})
    payload = {
        "amount": 1000,
        "confirmation_action_id": action_id,
        "confirmation_token": token,
    }

    # 第一次：成功 → claim 已 HDEL；remove_pending 已清 pending
    result1 = await reg.call("create_voucher_draft", payload,
                              hub_user_id=1, acting_as=2,
                              conversation_id="c1", round_idx=0)
    assert result1 is not None

    # 第二次（同 action_id+token）：claim_action 返 None → 拦截
    with pytest.raises(UnconfirmedWriteToolError):
        await reg.call("create_voucher_draft", payload,
                       hub_user_id=1, acting_as=2,
                       conversation_id="c1", round_idx=1)


async def test_write_tool_token_preserved_when_tool_fails():
    """tool fn 抛异常时 restore_action 还原 confirmed → 用户重试用同 token 还能成功。"""
    counter = {"n": 0}
    async def sometimes_flaky(amount, **_):
        counter["n"] += 1
        if counter["n"] == 1:
            raise RuntimeError("ERP 5xx")
        return {"draft_id": 99}

    reg = ToolRegistry(...)
    reg.register("create_voucher_draft", sometimes_flaky,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="...")
    action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft",
                                           {"amount": 1000})
    payload = {
        "amount": 1000,
        "confirmation_action_id": action_id,
        "confirmation_token": token,
    }

    # 第一次：tool 失败 → restore_action 把 data 还回 confirmed
    with pytest.raises(RuntimeError):
        await reg.call("create_voucher_draft", payload,
                       hub_user_id=1, acting_as=2,
                       conversation_id="c1", round_idx=0)

    # 第二次：tool 成功（counter=2）→ 通过，证明 token 被 restore 了
    result = await reg.call("create_voucher_draft", payload,
                             hub_user_id=1, acting_as=2,
                             conversation_id="c1", round_idx=1)
    assert result == {"draft_id": 99}


async def test_write_tool_concurrent_claim_executes_only_once():
    """v5 round 2 P1：asyncio.gather 同 (action_id, token) N 个并发，只有 1 个 tool.fn 跑。"""
    import asyncio
    counter = {"n": 0}
    async def slow_fn(amount, **_):
        counter["n"] += 1
        await asyncio.sleep(0.05)  # 给并发窗口
        return {"draft_id": counter["n"]}

    reg = ToolRegistry(...)
    reg.register("create_voucher_draft", slow_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="...")
    action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft",
                                           {"amount": 1000})
    payload = {
        "amount": 1000,
        "confirmation_action_id": action_id,
        "confirmation_token": token,
    }

    # 5 个并发同时拿同 (action_id, token) 调
    results = await asyncio.gather(*[
        reg.call("create_voucher_draft", payload,
                 hub_user_id=1, acting_as=2,
                 conversation_id="c1", round_idx=i)
        for i in range(5)
    ], return_exceptions=True)

    # 1 个成功，4 个 UnconfirmedWriteToolError
    successes = [r for r in results if not isinstance(r, BaseException)]
    blocked = [r for r in results if isinstance(r, UnconfirmedWriteToolError)]
    assert len(successes) == 1
    assert len(blocked) == 4
    assert counter["n"] == 1  # tool.fn 只跑了 1 次（写副作用没重复）


async def test_action_id_uniqueness_for_duplicate_args():
    """v5 round 2 P1：单 round 同 tool + 同 args 两个 pending → 两个独立 token，互不影响。"""
    reg = ToolRegistry(...)
    counter = {"n": 0}
    async def fn(amount, **_):
        counter["n"] += 1
        return {"draft_id": counter["n"]}

    reg.register("create_voucher_draft", fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="...")

    # 模拟 LLM 同 round 调两次 create_voucher_draft({amount:1000}) → 都拦截
    args = {"amount": 1000}
    await reg.confirm_gate.add_pending("c1", 1, "create_voucher_draft", args)
    await reg.confirm_gate.add_pending("c1", 1, "create_voucher_draft", args)

    # 用户回'是' → 两个 action_id 各自 token
    confirmed = await reg.confirm_gate.confirm_all_pending("c1", 1)
    assert len(confirmed) == 2
    assert confirmed[0]["action_id"] != confirmed[1]["action_id"]
    assert confirmed[0]["token"] != confirmed[1]["token"]  # token 含 action_id 所以不同

    # 调用第一个：成功，第二个仍可用
    r1 = await reg.call("create_voucher_draft", {
        **args, "confirmation_action_id": confirmed[0]["action_id"],
        "confirmation_token": confirmed[0]["token"],
    }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)
    r2 = await reg.call("create_voucher_draft", {
        **args, "confirmation_action_id": confirmed[1]["action_id"],
        "confirmation_token": confirmed[1]["token"],
    }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)
    assert counter["n"] == 2  # 真的跑了 2 次，无相互干扰


async def test_token_cross_action_replay_blocked():
    """v5 round 2 P1：把 action_A 的 token 用在 action_B 上 → 拦截（防跨 action 复用）。"""
    reg = ToolRegistry(...)
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="...")
    args = {"amount": 1000}
    await reg.confirm_gate.add_pending("c1", 1, "create_voucher_draft", args)
    await reg.confirm_gate.add_pending("c1", 1, "create_voucher_draft", args)
    confirmed = await reg.confirm_gate.confirm_all_pending("c1", 1)
    a, b = confirmed[0], confirmed[1]

    # 拿 a.token 配 b.action_id → 校验失败 → restore + 拦截
    with pytest.raises(UnconfirmedWriteToolError):
        await reg.call("create_voucher_draft", {
            **args, "confirmation_action_id": b["action_id"],
            "confirmation_token": a["token"],
        }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)


# === v6 round 2 P1 加固：claim 后失败路径不消费 token / pending 同步终态（3 case）===
async def test_permission_denied_does_not_consume_token():
    """v6 round 2 P1-#1：用户无权限 → claim 之前就抛 → confirmed token 不消费 + pending 仍在。"""
    reg = ToolRegistry(...)
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.approve",  # 故意要求审批权限
                 tool_type=ToolType.WRITE_DRAFT, description="...")
    action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft",
                                           {"amount": 1000})
    # mock：has_permission 对 hub_user_id=1 返 False
    with pytest.raises(PermissionDenied):
        await reg.call("create_voucher_draft", {
            "amount": 1000,
            "confirmation_action_id": action_id,
            "confirmation_token": token,
        }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)
    # 关键断言：confirmed 仍在（token 没被 claim 消费）→ admin 改权限后仍可 claim
    pending_after = await reg.confirm_gate.list_pending("c1", 1)
    assert any(p["action_id"] == action_id for p in pending_after)
    # 用 confirm_all_pending 不会重复 mark（pending 已在）；直接调 claim 仍可成功
    bundle = await reg.confirm_gate.claim_action(
        "c1", 1, action_id, token, "create_voucher_draft", {"amount": 1000},
    )
    assert bundle is not None  # 仍可 claim


async def test_schema_validation_failure_does_not_consume_token():
    """v6 round 2 P1-#1：schema 校验失败 → claim 之前就抛 → confirmed token 不消费。"""
    reg = ToolRegistry(...)
    async def fn(amount: int, **_): return {"id": 1}
    reg.register("create_voucher_draft", fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="...")
    action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft",
                                           {"amount": 1000})
    # 调用时 amount 用错类型 → jsonschema 抛 ToolArgsValidationError
    with pytest.raises(ToolArgsValidationError):
        await reg.call("create_voucher_draft", {
            "amount": "not-an-int",  # 类型错
            "confirmation_action_id": action_id,
            "confirmation_token": token,
        }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)
    # 用合法 args 重新调，应能成功（说明 token 没被消费）
    result = await reg.call("create_voucher_draft", {
        "amount": 1000,
        "confirmation_action_id": action_id,
        "confirmation_token": token,
    }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=1)
    assert result is not None


async def test_claim_atomically_removes_pending_so_reconfirm_safe():
    """v6 round 2 P1-#2：claim 成功后 pending 同步删除 → 用户再回'是'不会重 mark 同 action。"""
    counter = {"n": 0}
    async def fn(amount, **_):
        counter["n"] += 1
        return {"draft_id": counter["n"]}

    reg = ToolRegistry(...)
    reg.register("create_voucher_draft", fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="...")
    action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft",
                                           {"amount": 1000})

    # 第一次：成功，claim 原子删除 confirmed + pending
    await reg.call("create_voucher_draft", {
        "amount": 1000,
        "confirmation_action_id": action_id,
        "confirmation_token": token,
    }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)
    assert counter["n"] == 1

    # 用户再回'是' → confirm_all_pending 看到 pending 空 → 返 []（不再 mark 同 action）
    refresh = await reg.confirm_gate.confirm_all_pending("c1", 1)
    assert refresh == []

    # 即使 LLM 在 30min TTL 内偶然记得旧 token，再调用也被拒（confirmed 已删）
    with pytest.raises(UnconfirmedWriteToolError):
        await reg.call("create_voucher_draft", {
            "amount": 1000,
            "confirmation_action_id": action_id,
            "confirmation_token": token,
        }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=1)
    assert counter["n"] == 1  # 真的没有重复执行

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

合计 **20 case**（v5 加 3 case + v6 round 2 加 3 case：权限失败不消费 token / schema 失败不消费 token / claim 原子删 pending 防重复 confirm）。

- [ ] **Step 2: 实现 ToolRegistry**

```python
# hub/agent/tools/registry.py
from __future__ import annotations
import inspect
import logging
import time
from typing import Any, Callable, get_type_hints

from hub.permissions import require_permissions, has_permission
from hub.observability.tool_logger import log_tool_call
from hub.agent.tools.types import (
    ToolType, ToolDef,
    UnconfirmedWriteToolError, ToolNotFoundError, ToolArgsValidationError,
)
from hub.agent.tools.confirm_gate import ConfirmGate
from hub.agent.tools.entity_extractor import EntityExtractor
from hub.agent.memory.session import SessionMemory

logger = logging.getLogger("hub.agent.tools.registry")


class ToolRegistry:
    SCHEMA_CACHE_TTL = 300  # 5 min

    def __init__(self, *, confirm_gate: ConfirmGate, session_memory: SessionMemory):
        self._tools: dict[str, ToolDef] = {}
        self._user_schema_cache: dict[int, tuple[float, list[dict]]] = {}
        self.confirm_gate = confirm_gate
        self.session_memory = session_memory
        self.entity_extractor = EntityExtractor()

    def register(self, name: str, fn: Callable, *, perm: str, description: str,
                 tool_type: ToolType):
        """注册 tool；tool_type 必填。"""
        sig = inspect.signature(fn)
        hints = get_type_hints(fn)
        params = self._build_json_schema(sig, hints)

        self._tools[name] = ToolDef(
            name=name, fn=fn, perm=perm,
            description=description,
            tool_type=tool_type,
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
            if pname in ("self", "ctx", "acting_as_user_id", "hub_user_id",
                        "conversation_id",
                        "confirmation_token", "confirmation_action_id"):
                continue  # 这些是 ToolRegistry 注入的内部 context，不暴露给 LLM
            ptype = hints.get(pname, str)
            properties[pname] = {"type": self._py_to_json_type(ptype)}
            if param.default == inspect.Parameter.empty:
                required.append(pname)
        return {"type": "object", "properties": properties, "required": required}

    def _py_to_json_type(self, t):
        """Python type → OpenAI function schema type。"""
        if t is int: return "integer"
        if t is str: return "string"
        if t is float: return "number"
        if t is bool: return "boolean"
        # typing.List / typing.Dict / typing.Optional[X] 等需要解 origin
        origin = getattr(t, "__origin__", None)
        if origin is list: return "array"
        if origin is dict: return "object"
        if origin is type(None) or t is type(None): return "null"
        # Optional[X] 取 X
        args = getattr(t, "__args__", ())
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return self._py_to_json_type(non_none[0])
        return "string"  # 默认 fallback

    async def schema_for_user(self, hub_user_id: int) -> list[dict]:
        """按用户权限过滤 + 5 min cache。"""
        cached = self._user_schema_cache.get(hub_user_id)
        if cached and time.monotonic() < cached[0]:
            return cached[1]

        schemas = []
        for tool in self._tools.values():
            if await has_permission(hub_user_id, tool.perm):
                schemas.append(tool.schema)
        self._user_schema_cache[hub_user_id] = (
            time.monotonic() + self.SCHEMA_CACHE_TTL, schemas,
        )
        return schemas

    async def call(self, name: str, args: dict, *, hub_user_id: int,
                   acting_as: int, conversation_id: str, round_idx: int) -> Any:
        """统一入口：权限 → schema 校验 → claim（写类）→ 调 fn → 失败 restore / 成功直接走 → 提取实体。

        v6 round 2 P1 加固（关键改动）：
        1. require_permissions / _validate_args 移到 claim **之前**（review v6 P1-#1）
           原因：v6 写法把这两步放在 claim 后；权限被撤销 / args schema 不合法时 confirmed
           token 已被 HDEL 但 tool 没真跑，用户进入难解释的"已确认但失败"循环。
           修复：把 stateless 的权限+schema 校验提前；失败时不消费 confirmed token。
        2. claim_action 现在原子同时 HDEL confirmed + pending（持久终态，review v6 P1-#2）
           原因：v6 写法 tool 成功后再 remove_pending；Redis 短暂故障导致 pending 残留时
           用户回'是'重新 mark_confirmed → 同写操作再次执行（重复副作用）。
           修复：claim 时一次性删两个；成功路径无需任何后续 cleanup（持久终态）。
        3. 失败 restore 也用 Lua 原子还回 confirmed + pending；不会出现"confirmed 还了
           但 pending 没还"的中间态导致用户回'是'时多 mark 一份。
        """
        tool = self._tools.get(name)
        if not tool:
            raise ToolNotFoundError(name)

        # ❶ 写类 tool：先 pop confirmation 字段（schema 校验不含它们）
        is_write = tool.tool_type in (ToolType.WRITE_DRAFT, ToolType.WRITE_ERP)
        action_id: str | None = None
        token: str | None = None
        if is_write:
            action_id = args.pop("confirmation_action_id", None)
            token = args.pop("confirmation_token", None)

        # ❷ 权限 + args schema 校验（v6 round 2 P1：移到 claim 前；失败不消费 confirmed token）
        await require_permissions(hub_user_id, [tool.perm])
        self._validate_args(args, tool.schema)

        # ❸ 写类 tool 硬门禁：原子 claim（claim 内已含 token / tool_name / args 一致性校验）
        bundle: dict | None = None
        if is_write:
            bundle = await self.confirm_gate.claim_action(
                conversation_id, hub_user_id, action_id, token, name, args,
            )
            if bundle is None:
                # 全部失败场景（无 action_id / 无 token / token 错 / args 错 / 已被并发领取）
                # 都走这里，统一报错；ChainAgent 上层把 raise 翻成对 LLM 的 system hint
                await self._log_blocked_call(conversation_id, round_idx, name, args,
                                              reason="unconfirmed_write_or_claim_failed")
                raise UnconfirmedWriteToolError(
                    f"写类 tool '{name}' 未确认，或 confirmation_token/action_id 无效，"
                    "或已被另一并发调用领取。请用 text 把操作预览发给用户，"
                    "用户回'是'后由 ChainAgent 自动注入新 (action_id, token) 重试。"
                )

        # ❹ 调 tool（注入内部 context）+ 记 log + 失败 restore_action（confirmed + pending 一起还）
        async with log_tool_call(
            conversation_id=conversation_id, round_idx=round_idx,
            tool_name=name, args=args,
        ) as ctx:
            inject_ctx = {
                "acting_as_user_id": acting_as,
                "hub_user_id": hub_user_id,
                "conversation_id": conversation_id,
            }
            # 只注入 fn 实际接受的参数（避免 TypeError unexpected keyword）
            sig = inspect.signature(tool.fn)
            kwargs = {**args}
            for k, v in inject_ctx.items():
                if k in sig.parameters:
                    kwargs[k] = v

            try:
                result = await tool.fn(**kwargs)
            except Exception:
                # ❺ 写类 tool 执行失败 → restore_action（让用户/重试用同 token 再来）
                if is_write and bundle is not None and action_id is not None:
                    try:
                        await self.confirm_gate.restore_action(
                            conversation_id, hub_user_id, action_id, bundle,
                        )
                    except Exception:
                        # restore 本身也失败：罕见的 Redis 故障；记日志让 30 min TTL 自然清理。
                        # 这里不重抛 restore 错误，向上抛原始 tool exception 以保留语义。
                        logger.exception(
                            "restore_action failed (conv=%s user=%s action=%s); "
                            "TTL will eventually clean up, but user may need to reconfirm",
                            conversation_id, hub_user_id, action_id,
                        )
                raise

            ctx.set_result(result)

            # 写类 tool 成功路径：claim 时已原子删除 confirmed + pending，无需任何 cleanup（持久终态）

            # ❻ 提取实体引用写回 session memory（review P2-#8）
            refs = self.entity_extractor.extract(result)
            if refs.has_any():
                await self.session_memory.add_entity_refs(
                    conversation_id,
                    customer_ids=refs.customer_ids,
                    product_ids=refs.product_ids,
                )
            return result

    def _validate_args(self, args: dict, schema: dict):
        """jsonschema validate；不符抛 ToolArgsValidationError。"""
        from jsonschema import validate, ValidationError
        try:
            validate(instance=args, schema=schema["function"]["parameters"])
        except ValidationError as e:
            raise ToolArgsValidationError(str(e)) from e

    async def _log_blocked_call(self, conversation_id, round_idx, name, args, *, reason):
        """拦截掉的 tool call 也写一条 tool_call_log（error 字段标 reason）。"""
        from hub.models.conversation import ToolCallLog
        await ToolCallLog.create(
            conversation_id=conversation_id, round_idx=round_idx,
            tool_name=name, args_json=args,
            error=f"blocked: {reason}",
        )
```

**配套类型 / Gate 完整实现** — `hub/agent/tools/types.py`：

```python
from enum import StrEnum
from dataclasses import dataclass
from typing import Callable

class ToolType(StrEnum):
    READ = "read"
    GENERATE = "generate"
    WRITE_DRAFT = "write_draft"
    WRITE_ERP = "write_erp"

@dataclass
class ToolDef:
    name: str
    fn: Callable
    perm: str
    description: str
    tool_type: ToolType
    schema: dict

class UnconfirmedWriteToolError(Exception): ...
class ToolNotFoundError(Exception): ...
class ToolArgsValidationError(Exception): ...
```

**`hub/agent/tools/confirm_gate.py`**：

```python
import hashlib
import json
import uuid
from redis.asyncio import Redis

class ConfirmGate:
    """写门禁 + pending_write 状态管理（按 conversation_id × hub_user_id 严格隔离）。

    review v3 第二轮 P1（已应用）：
    - key/token 加 hub_user_id：群聊里 B 不能确认 A 的写
    - pending 改 hash（action_id → pending data）：支持单 round 多个写 tool 一起 pending

    review v5 第二轮 P1（本轮新加，关键改动）：
    - confirmed 从 set 改成 hash {action_id → confirmed_data}：消费按 action_id 原子做
    - compute_token 加 action_id 入 payload：单 round 同 tool+同 args 多 pending 也有不同 token
    - 新 claim_action：tool.fn 前用 Redis Lua HGET+HDEL 原子领取 → 真正 one-time，挡得住并发
    - 新 restore_action：tool.fn 抛错时还原 confirmed 状态以便重试（不强制用户重新确认）
    """
    PENDING_KEY = "hub:agent:pending:"      # hash: {action_id: pending_json}
    CONFIRMED_KEY = "hub:agent:confirmed:"  # hash: {action_id: confirmed_json}（v5 round 2 改）
    TTL = 1800  # 30 min（与会话 memory 同 TTL）

    # Lua 脚本：v6 round 2 P1 加固 —— 原子 HGET+HDEL **同时跨 confirmed 和 pending 两个 hash**
    # KEYS[1] = confirmed_key, KEYS[2] = pending_key, ARGV[1] = action_id
    # 返回 [confirmed_raw, pending_raw]（pending_raw 可能是 false 表示之前就没 pending）
    # 关键不变量：claim 成功 → confirmed 和 pending 同时被删除（持久终态，无需后续 remove_pending）
    # 没拿到 confirmed_raw（confirmed 中无该 action_id）→ 不动 pending，直接返 nil
    _CLAIM_LUA = """
    local confirmed_raw = redis.call('HGET', KEYS[1], ARGV[1])
    if not confirmed_raw then
        return nil
    end
    local pending_raw = redis.call('HGET', KEYS[2], ARGV[1])
    redis.call('HDEL', KEYS[1], ARGV[1])
    if pending_raw then
        redis.call('HDEL', KEYS[2], ARGV[1])
    end
    return {confirmed_raw, pending_raw or false}
    """

    # Lua 脚本：v6 round 2 P1 加固 —— 原子 restore（tool.fn 抛错时把 confirmed + pending 都还回去）
    # KEYS[1] = confirmed_key, KEYS[2] = pending_key
    # ARGV: [action_id, confirmed_raw, ttl, pending_raw_or_empty]
    # 用 Lua 保证 restore 也是原子的，不会出现 confirmed 还回去但 pending 没还的中间态
    _RESTORE_LUA = """
    redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
    redis.call('EXPIRE', KEYS[1], ARGV[3])
    if ARGV[4] and ARGV[4] ~= '' then
        redis.call('HSET', KEYS[2], ARGV[1], ARGV[4])
        redis.call('EXPIRE', KEYS[2], ARGV[3])
    end
    return 1
    """

    def __init__(self, redis: Redis):
        self.redis = redis
        self._claim_script = redis.register_script(self._CLAIM_LUA)
        self._restore_script = redis.register_script(self._RESTORE_LUA)

    def _pending_key(self, conversation_id: str, hub_user_id: int) -> str:
        return f"{self.PENDING_KEY}{conversation_id}:{hub_user_id}"

    def _confirmed_key(self, conversation_id: str, hub_user_id: int) -> str:
        return f"{self.CONFIRMED_KEY}{conversation_id}:{hub_user_id}"

    @staticmethod
    def canonicalize(args: dict) -> dict:
        """归一化 args：剔除 None；list/dict 内部递归排序 key。"""
        def _norm(v):
            if isinstance(v, dict):
                return {k: _norm(v[k]) for k in sorted(v) if v[k] is not None}
            if isinstance(v, list):
                return [_norm(x) for x in v]
            return v
        return _norm(args)

    @staticmethod
    def compute_token(conversation_id: str, hub_user_id: int, action_id: str,
                      tool_name: str, normalized_args: dict) -> str:
        """token = sha256(conv:user:action_id:tool:canonical(args))[:32]（v5 round 2：含 action_id）。

        加 action_id 后，单 round 同 tool + 同 args 的多个 pending 也有不同 token，
        消费一个不影响另一个；防 LLM 用同 token 触发不同 action 的副作用。
        """
        payload = (
            f"{conversation_id}:{hub_user_id}:{action_id}:{tool_name}:"
            f"{json.dumps(normalized_args, sort_keys=True, ensure_ascii=False)}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    # ====== pending（被门禁拦截的写动作）======
    async def add_pending(self, conversation_id: str, hub_user_id: int,
                          tool_name: str, args: dict) -> str:
        """ChainAgent 在 tool 被门禁拦截时调；返回 action_id。同 round 多个写都能存。"""
        action_id = uuid.uuid4().hex[:8]
        normalized = self.canonicalize(args)
        await self.redis.hset(
            self._pending_key(conversation_id, hub_user_id),
            action_id,
            json.dumps({
                "tool_name": tool_name,
                "args": args,
                "normalized_args": normalized,
            }, ensure_ascii=False),
        )
        await self.redis.expire(self._pending_key(conversation_id, hub_user_id), self.TTL)
        return action_id

    async def list_pending(self, conversation_id: str,
                           hub_user_id: int) -> list[dict]:
        """返回该 user 的所有 pending action（用户回'是'时由 ChainAgent 调）。"""
        raw = await self.redis.hgetall(self._pending_key(conversation_id, hub_user_id))
        out = []
        for action_id, payload in (raw or {}).items():
            data = json.loads(payload)
            data["action_id"] = action_id.decode() if isinstance(action_id, bytes) else action_id
            out.append(data)
        return sorted(out, key=lambda d: d["action_id"])  # 稳定顺序

    async def clear_pending(self, conversation_id: str, hub_user_id: int) -> None:
        await self.redis.delete(self._pending_key(conversation_id, hub_user_id))

    # 注：v5 round 2 曾有 remove_pending 单条删 helper，v6 round 2 P1 加固后删除：
    # claim_action Lua 脚本已原子同时 HDEL confirmed + pending，
    # 写 tool 成功路径不再需要单独的 remove_pending（避免 Redis 短暂故障下 pending 残留导致重复执行）。

    # ====== confirmed（用户已确认的待执行 action）======
    async def mark_confirmed(self, conversation_id: str, hub_user_id: int,
                             action_id: str, tool_name: str, args: dict) -> str:
        """v5 round 2：confirmed 改成 hash {action_id: data}。token 含 action_id，唯一。"""
        normalized = self.canonicalize(args)
        token = self.compute_token(
            conversation_id, hub_user_id, action_id, tool_name, normalized,
        )
        confirmed_data = {
            "tool_name": tool_name,
            "args": args,
            "normalized_args": normalized,
            "token": token,
        }
        confirmed_key = self._confirmed_key(conversation_id, hub_user_id)
        await self.redis.hset(
            confirmed_key, action_id,
            json.dumps(confirmed_data, ensure_ascii=False),
        )
        await self.redis.expire(confirmed_key, self.TTL)
        return token

    async def confirm_all_pending(self, conversation_id: str,
                                   hub_user_id: int) -> list[dict]:
        """用户回'是' → 把所有 pending action 标 confirmed → 返 [{action_id, tool_name, args, token}, ...]。

        ChainAgent 用这个返回值组装 system hint 让 LLM 重新调 tool 时填对 (action_id, token)。
        pending 不主动清；ToolRegistry.call 成功执行后由 remove_pending 清掉对应 action_id。
        """
        pending = await self.list_pending(conversation_id, hub_user_id)
        out = []
        for p in pending:
            token = await self.mark_confirmed(
                conversation_id, hub_user_id, p["action_id"],
                p["tool_name"], p["args"],
            )
            out.append({**p, "token": token})
        return out

    async def claim_action(self, conversation_id: str, hub_user_id: int,
                           action_id: str | None, token: str | None,
                           tool_name: str, args: dict) -> dict | None:
        """v6 round 2 P1 加固：原子领取 confirmed + pending action（tool.fn 前调，持久终态）。

        流程：
        1. Lua 脚本原子 HGET+HDEL **同时**对 confirmed_hash 和 pending_hash 操作：
           - 并发 N 个调用只有 1 个拿到 confirmed_raw（其余拿到 nil）
           - 拿到 confirmed_raw 的同时把 pending 也 HDEL 掉（如果存在）
           - 关键：confirmed 和 pending 同步删除是**唯一可靠的成功终态**，避免后续 remove_pending
             单独失败时 pending 残留导致用户回'是'重新 confirm 再次执行同写操作（v6 review P1-#2）
        2. 校验 token / tool_name / args 一致性
        3. 校验失败：用 _restore_script 把 confirmed + pending 都还回去 + 返 None
        4. 全部通过：返 bundle = {data, confirmed_raw, pending_raw}；调用方可用 bundle 做 restore

        失败语义（返 None 的全部场景）：
        - confirmed_hash 中无该 action_id（从未确认 / 已被并发领取 / 已超 TTL）
        - token 不匹配（LLM 篡改 / 跨 action 复用）
        - tool_name 或 args 与 confirmed 时不一致（LLM 偷偷改参数）
        """
        if not (token and action_id):
            return None
        confirmed_key = self._confirmed_key(conversation_id, hub_user_id)
        pending_key = self._pending_key(conversation_id, hub_user_id)
        result = await self._claim_script(
            keys=[confirmed_key, pending_key], args=[action_id],
        )
        if not result:
            return None

        # result = [confirmed_raw, pending_raw_or_false]
        confirmed_raw = result[0] if isinstance(result[0], str) else result[0].decode()
        pending_raw_or_false = result[1] if len(result) > 1 else False
        pending_raw: str | None = None
        if pending_raw_or_false and pending_raw_or_false is not False:
            pending_raw = (
                pending_raw_or_false if isinstance(pending_raw_or_false, str)
                else pending_raw_or_false.decode()
            )
        data = json.loads(confirmed_raw)

        # 校验 token / tool_name / args 一致性；任何不一致都 restore（confirmed + pending 都还）
        normalized = self.canonicalize(args)
        expected_token = self.compute_token(
            conversation_id, hub_user_id, action_id, tool_name, normalized,
        )
        consistent = (
            data.get("token") == token == expected_token
            and data.get("tool_name") == tool_name
            and data.get("normalized_args") == normalized
        )
        if not consistent:
            await self._restore_script(
                keys=[confirmed_key, pending_key],
                args=[action_id, confirmed_raw, str(self.TTL), pending_raw or ""],
            )
            return None

        return {
            "data": data,
            "confirmed_raw": confirmed_raw,
            "pending_raw": pending_raw,  # 可能 None：之前就没 pending（直接 mark_confirmed 走的路径）
        }

    async def restore_action(self, conversation_id: str, hub_user_id: int,
                             action_id: str, bundle: dict) -> None:
        """v6 round 2 P1：tool.fn 抛错时**原子**还原 confirmed + pending（让用户/重试用同 token 再来）。

        bundle 来自 claim_action 返回值，含 confirmed_raw + pending_raw（可能 None）。
        Lua 脚本保证 confirmed 和 pending 都被原子还回去；不会出现 confirmed 还了但 pending 没还的中间态。
        TTL 重置 30 min；用户在窗口内还能重试。
        """
        confirmed_key = self._confirmed_key(conversation_id, hub_user_id)
        pending_key = self._pending_key(conversation_id, hub_user_id)
        await self._restore_script(
            keys=[confirmed_key, pending_key],
            args=[
                action_id,
                bundle["confirmed_raw"],
                str(self.TTL),
                bundle.get("pending_raw") or "",
            ],
        )
```

**`hub/agent/tools/entity_extractor.py`**：

```python
from dataclasses import dataclass, field

@dataclass
class EntityRefs:
    customer_ids: set[int] = field(default_factory=set)
    product_ids: set[int] = field(default_factory=set)

    def has_any(self) -> bool:
        return bool(self.customer_ids or self.product_ids)

class EntityExtractor:
    """从 tool result（任意 nested dict/list）提取 customer_id / product_id。"""

    def extract(self, result) -> EntityRefs:
        refs = EntityRefs()
        self._walk(result, refs)
        return refs

    def _walk(self, node, refs: EntityRefs):
        if isinstance(node, dict):
            # 直接 customer_id / product_id 字段
            for key, val in node.items():
                if key == "customer_id" and isinstance(val, int):
                    refs.customer_ids.add(val)
                elif key == "product_id" and isinstance(val, int):
                    refs.product_ids.add(val)
                elif key == "id" and "customer" in str(node.get("type", "")).lower():
                    refs.customer_ids.add(val) if isinstance(val, int) else None
                elif key == "id" and "product" in str(node.get("type", "")).lower():
                    refs.product_ids.add(val) if isinstance(val, int) else None
                else:
                    self._walk(val, refs)
        elif isinstance(node, list):
            for item in node:
                self._walk(item, refs)
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
async def search_orders(self, *, customer_id: int | None = None,
                        since: datetime | None = None,
                        status: str | None = None,
                        page: int = 1, page_size: int = 200,
                        acting_as_user_id: int) -> dict:
    """ERP `/api/v1/orders`：按客户/时间/状态分页搜单。"""
    params = {"page": page, "page_size": page_size}
    if customer_id is not None:
        params["customer_id"] = customer_id
    if since is not None:
        params["since"] = since.isoformat()
    if status:
        params["status"] = status
    return await self._act_as_get("/api/v1/orders", acting_as_user_id, params=params)

async def get_order_detail(self, order_id: int, *, acting_as_user_id: int) -> dict:
    return await self._act_as_get(f"/api/v1/orders/{order_id}", acting_as_user_id)

async def get_customer_balance(self, customer_id: int, *, acting_as_user_id: int) -> dict:
    return await self._act_as_get(
        f"/api/v1/finance/customer-statement/{customer_id}", acting_as_user_id,
    )

async def get_inventory_aging(self, *, threshold_days: int = 90,
                              product_id: int | None = None,
                              warehouse_id: int | None = None,
                              acting_as_user_id: int) -> dict:
    """ERP `/api/v1/inventory/aging`：按库龄聚合滞销商品。⏳ 依赖 Task 18 ERP 新增 endpoint。"""
    params = {"threshold_days": threshold_days}
    if product_id is not None:
        params["product_id"] = product_id
    if warehouse_id is not None:
        params["warehouse_id"] = warehouse_id
    return await self._act_as_get("/api/v1/inventory/aging", acting_as_user_id, params=params)

async def upsert_customer_price_rule(self, *, customer_id: int, product_id: int,
                                     new_price: float, reason: str | None = None,
                                     client_request_id: str,
                                     acting_as_user_id: int) -> dict:
    """ERP `POST /api/v1/customer-price-rules` 创建/更新客户专属定价。
    ⏳ 依赖 Task 18 ERP 新增 endpoint + client_request_id 幂等键。"""
    return await self._act_as_post(
        "/api/v1/customer-price-rules",
        json={
            "customer_id": customer_id, "product_id": product_id,
            "price": new_price, "reason": reason,
            "client_request_id": client_request_id,
        },
        acting_as_user_id=acting_as_user_id,
    )

async def create_voucher(self, *, voucher_data: dict, client_request_id: str,
                          acting_as_user_id: int) -> dict:
    """ERP `POST /api/v1/vouchers` 创建凭证。
    带 client_request_id 实现幂等（Task 18 ERP 加唯一约束）。"""
    return await self._act_as_post(
        "/api/v1/vouchers",
        json={**voucher_data, "client_request_id": client_request_id},
        acting_as_user_id=acting_as_user_id,
    )

async def batch_approve_vouchers(self, *, voucher_ids: list[int],
                                  acting_as_user_id: int) -> dict:
    """ERP `POST /api/v1/vouchers/batch-approve`（已存在）。返回 {success, failed}。"""
    return await self._act_as_post(
        "/api/v1/vouchers/batch-approve",
        json={"voucher_ids": voucher_ids},
        acting_as_user_id=acting_as_user_id,
    )
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


# 其余 8 个读 tool 完全照 search_products 模式：函数体一行调对应 erp.<method>，
# 参数透传 acting_as_user_id；docstring 必须含 Args 段（auto schema 生成依赖）。
# sketch 仅列签名 — 实施按 §3.1 注入 context 规则填充。

async def search_customers(query: str, *, acting_as_user_id: int) -> dict:
    """搜索客户。Args: query: 关键字（中英自动分词）。"""
    return await current_erp_adapter().search_customers(query=query, acting_as_user_id=acting_as_user_id)

async def get_product_detail(product_id: int, *, acting_as_user_id: int) -> dict:
    """商品详情（含库存）。Args: product_id: ERP 商品 ID。"""
    return await current_erp_adapter().get_product(product_id=product_id, acting_as_user_id=acting_as_user_id)

async def get_customer_history(product_id: int, customer_id: int, *,
                                limit: int = 5, acting_as_user_id: int) -> dict:
    """查客户最近 N 次该商品成交价。"""
    return await current_erp_adapter().get_product_customer_prices(
        product_id=product_id, customer_id=customer_id,
        limit=limit, acting_as_user_id=acting_as_user_id,
    )

async def check_inventory(product_id: int, *, acting_as_user_id: int) -> dict:
    """简化的库存查询（封装 get_product_detail 的 stocks 字段）。"""
    detail = await get_product_detail(product_id, acting_as_user_id=acting_as_user_id)
    return {"product_id": product_id, "total_stock": detail.get("total_stock", 0),
            "stocks": detail.get("stocks", [])}

async def search_orders(customer_id: int | None = None, since_days: int = 30,
                        *, acting_as_user_id: int) -> dict:
    """搜单。"""
    since = datetime.now(UTC) - timedelta(days=since_days)
    return await current_erp_adapter().search_orders(
        customer_id=customer_id, since=since, acting_as_user_id=acting_as_user_id,
    )

async def get_order_detail(order_id: int, *, acting_as_user_id: int) -> dict:
    return await current_erp_adapter().get_order_detail(order_id=order_id, acting_as_user_id=acting_as_user_id)

async def get_customer_balance(customer_id: int, *, acting_as_user_id: int) -> dict:
    """客户应收/已付/未付汇总。"""
    return await current_erp_adapter().get_customer_balance(
        customer_id=customer_id, acting_as_user_id=acting_as_user_id,
    )

async def get_inventory_aging(threshold_days: int = 90, *, acting_as_user_id: int) -> dict:
    """库龄超 N 天的滞销商品。⏳ 依赖 Task 18 ERP /inventory/aging。"""
    return await current_erp_adapter().get_inventory_aging(
        threshold_days=threshold_days, acting_as_user_id=acting_as_user_id,
    )


def register_all(registry: ToolRegistry):
    """11 个 ERP 读 tool 全部注册（perm + tool_type 必填）。"""
    registry.register("search_products", search_products,
                      perm="usecase.query_product.use",
                      tool_type=ToolType.READ,
                      description="按关键字搜索商品（中英自动分词）")
    registry.register("search_customers", search_customers,
                      perm="usecase.query_customer.use",
                      tool_type=ToolType.READ,
                      description="按关键字搜索客户")
    registry.register("get_product_detail", get_product_detail,
                      perm="usecase.query_product.use",
                      tool_type=ToolType.READ,
                      description="商品详情（含库存明细）")
    registry.register("get_customer_history", get_customer_history,
                      perm="usecase.query_customer_history.use",
                      tool_type=ToolType.READ,
                      description="客户最近 N 次该商品成交价")
    registry.register("check_inventory", check_inventory,
                      perm="usecase.query_inventory.use",
                      tool_type=ToolType.READ,
                      description="商品库存简查")
    registry.register("search_orders", search_orders,
                      perm="usecase.query_orders.use",
                      tool_type=ToolType.READ,
                      description="按条件搜订单")
    registry.register("get_order_detail", get_order_detail,
                      perm="usecase.query_orders.use",
                      tool_type=ToolType.READ,
                      description="订单详情")
    registry.register("get_customer_balance", get_customer_balance,
                      perm="usecase.query_customer_balance.use",
                      tool_type=ToolType.READ,
                      description="客户余额（应收/已付/未付）")
    registry.register("get_inventory_aging", get_inventory_aging,
                      perm="usecase.query_inventory_aging.use",
                      tool_type=ToolType.READ,
                      description="库龄超 N 天的滞销商品")
    # 聚合 tool 在 Task 9 注册（analyze_top_customers / analyze_slow_moving_products）
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
    def should_extract(*, tool_call_logs: list, rounds_count: int) -> bool:
        """重要性 gate（review v3 P2-#5 修签名）。任一满足即抽：
        1. ≥ 1 次 write_draft tool 调用（凭证 / 调价 / 库存调整）
        2. ≥ 1 次 generate_* tool 调用（合同 / 报价 / Excel）
        3. ≥ 1 次 search_*/get_* tool result 含 customer_id/product_id
        4. rounds_count ≥ 4（长对话兜底，即使纯文本也可能有用户偏好）

        否则跳过抽取（闲聊 / 单 round unknown / 失败查询）。"""
        if rounds_count >= 4:
            return True
        for log in tool_call_logs:
            if log.tool_name.startswith(("create_", "generate_")):
                return True
            result = log.result_json or {}
            if "customer_id" in str(result) or "product_id" in str(result):
                return True
        return False
```

**调用方在 ChainAgent.run 完成后**：

```python
# chain_agent.py run() 末尾
asyncio.create_task(self.memory_writer.extract_and_write(
    conversation_id=conversation_id,
    conversation_log_id=conv_log.id,
    tool_call_logs=await ToolCallLog.filter(conversation_id=conversation_id).all(),
    rounds_count=round_idx + 1,  # 实际 round 数
    ai_provider=self.llm,
))
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
import tiktoken
from dataclasses import dataclass
from typing import Any

@dataclass
class Section:
    name: str
    content: Any  # str / list[dict] (messages)
    tokens: int

class PromptTooLargeError(Exception): ...

class ContextBuilder:
    def __init__(self, budget_token: int = 18_000, encoding="cl100k_base"):
        self._enc = tiktoken.get_encoding(encoding)
        self.budget = budget_token

    async def build_round(self, *, round_idx, base_memory, tools_schema,
                          conversation_history, latest_user_message,
                          confirm_state_hint: str | None = None,
                          budget_token: int | None = None) -> list[dict]:
        budget = budget_token or self.budget

        # ❶ MUST_KEEP（必保层 — 删了 agent 就废了）
        must_keep: list[Section] = []
        must_keep.append(self._mk_section(
            "system_prompt",
            build_system_prompt(base_memory.user, tools_schema),
        ))
        if latest_user_message:
            must_keep.append(self._mk_section("user_msg", latest_user_message))
        # 最近 1 round assistant 输出 + tool result
        must_keep.append(self._mk_section(
            "recent_round", conversation_history[-2:] if len(conversation_history) >= 2 else conversation_history,
        ))
        if confirm_state_hint:
            # 用户已确认动作的 system hint —— 必保（review P1-#2）
            must_keep.append(self._mk_section("confirm_hint", confirm_state_hint))

        must_tokens = sum(s.tokens for s in must_keep)
        if must_tokens > budget:
            # MUST_KEEP 自身就超预算 — 不静默裁，直接抛错让 ChainAgent fallback rule
            raise PromptTooLargeError(
                f"必保上下文 {must_tokens} token 已超 budget {budget}；"
                "可能是 system_prompt + tool schema 太大或 confirm_hint 太长。"
                "建议减少 tool 数量或裁剪 user_msg。"
            )

        # ❷ CAN_TRUNCATE（可裁层，按优先级从高到低装填）
        remaining = budget - must_tokens
        candidates: list[tuple[int, Section]] = []  # (priority, section)
        # 优先级 7：当前对话引用的客户 + 商品 memory
        candidates.append((7, self._mk_section(
            "entity_memory", base_memory.customers + base_memory.products,
        )))
        # 优先级 5：3 round 之前的 tool result 摘要
        candidates.append((5, self._mk_section(
            "old_results_summary",
            self._summarize_old_tool_results(conversation_history[:-2]),
        )))
        # 优先级 2：4 round 之前对话历史压缩成 1 句/round
        candidates.append((2, self._mk_section(
            "old_history_summary",
            self._summarize_old_history(conversation_history[:-4]),
        )))

        kept: list[Section] = []
        for _, sec in sorted(candidates, key=lambda x: -x[0]):  # 高优先级先装
            if sec.tokens <= remaining:
                kept.append(sec)
                remaining -= sec.tokens
            # 装不下就丢掉（不再尝试裁这个 section 内部，简化语义）

        return self._compose_messages(must_keep + kept)

    def _mk_section(self, name: str, content) -> Section:
        return Section(name=name, content=content, tokens=self._count_tokens(content))

    def _count_tokens(self, content) -> int:
        if isinstance(content, str):
            return len(self._enc.encode(content))
        if isinstance(content, list):
            # list of messages
            return sum(self._count_tokens(m.get("content", "") if isinstance(m, dict) else str(m))
                       for m in content)
        return self._count_tokens(str(content))

    def _summarize_old_tool_results(self, history) -> str:
        """旧 tool result 压缩：> 500 token 的，保 type + count + 前 3 项 keys，删数据。"""
        lines = []
        for msg in history:
            if not isinstance(msg, dict) or msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            tokens = self._count_tokens(content)
            if tokens > 500:
                # 摘要："调用 X 返回 N 个 items（字段：[id, name, ...]）"
                summary = self._summarize_dict(content)
                lines.append(f"[round-{msg.get('round_idx', '?')}] {msg.get('tool_name', '?')}: {summary}")
            else:
                lines.append(f"[round-{msg.get('round_idx', '?')}] {msg.get('tool_name', '?')}: {content[:200]}")
        return "\n".join(lines)

    def _summarize_dict(self, content: str) -> str:
        """从 JSON 文本提取 type/count/keys 摘要。"""
        try:
            import json
            data = json.loads(content)
            if isinstance(data, dict) and "items" in data:
                items = data["items"]
                if items and isinstance(items, list):
                    keys = list(items[0].keys()) if isinstance(items[0], dict) else []
                    return f"{len(items)} items, fields={keys[:6]}"
            return f"{type(data).__name__}, len={len(data) if hasattr(data, '__len__') else '?'}"
        except Exception:
            return "(摘要失败)"

    def _summarize_old_history(self, history) -> str:
        """4 round 之前对话压成 1 句/round。"""
        lines = []
        for msg in history:
            role = msg.get("role", "?") if isinstance(msg, dict) else "?"
            tool_name = msg.get("tool_name") if isinstance(msg, dict) else None
            if role == "tool" and tool_name:
                lines.append(f"调了 {tool_name}")
            elif role == "user":
                lines.append(f"用户: {msg.get('content', '')[:50]}")
        return " → ".join(lines)

    def _compose_messages(self, sections: list[Section]) -> list[dict]:
        """把 sections 拼成 OpenAI chat messages list。"""
        messages = []
        for s in sections:
            if s.name == "system_prompt" or s.name == "confirm_hint":
                messages.append({"role": "system", "content": str(s.content)})
            elif s.name == "user_msg":
                messages.append({"role": "user", "content": str(s.content)})
            elif s.name == "recent_round" and isinstance(s.content, list):
                messages.extend(s.content)
            elif s.name in ("old_results_summary", "old_history_summary"):
                if s.content:
                    messages.append({"role": "system", "content": f"[{s.name}]\n{s.content}"})
            else:
                messages.append({"role": "system", "content": f"[{s.name}]\n{s.content}"})
        return messages
```

**关键修复**（review P2-#4）：
- `must_keep` 与 `can_truncate` 拆开 — must_keep 超预算时直接抛 `PromptTooLargeError`，不静默裁掉 confirm_state 等关键内容
- `can_truncate` 按优先级从高到低**装填**（不是从低到高 pop），整段 section 装得下就装、装不下就丢
- 修了 v2 写的 `sections.append(..., priority=10)` 这个非法 Python（append 只接受一个参数）— 现在用 `(priority, section)` tuple list

ChainAgent 主循环要点：
- 每 round 入口先调 `ContextBuilder.build_round` 拿裁剪后的 messages
- LLM 调用 `asyncio.wait_for(timeout=30)`
- tool call 抛 LLMServiceError / 5xx → 注入 error 让 LLM 重试或放弃
- tool call 抛 UnconfirmedWriteToolError → 走"待确认"路径（见下面 Step 2.5）
- max_rounds=5 / budget_token=18000 / timeout=30 都从 system_config 读，可调

- [ ] **Step 2.5: 用户确认链路（review P1-#2 完整端到端）**

写门禁拦截后到用户回"是"再到 LLM 重试调用，**完整链路**：

```python
# hub/agent/chain_agent.py
class ChainAgent:
    """关键设计（review v3 第二轮 P1）：pending 走 ConfirmGate（按 conversation × hub_user 隔离 + 支持多 pending），
    ChainAgent 自身不直接管 Redis。同 round 多个 write tool 都被拦截 → 都进 pending list →
    用户一次回'是' → confirm_all_pending 一并标 confirmed → 把 N 个 token 拼进 hint 让 LLM 重新调用。"""

    async def run(self, user_message, *, hub_user_id, conversation_id, acting_as,
                  user_just_confirmed: bool = False):
        """
        user_just_confirmed: inbound handler 识别 "是/确认" 时传 True；
        ChainAgent 据此把该 user 在该 conversation 下所有 pending action 一起标 confirmed。
        """
        # ❶ 处理"用户已确认"路径
        confirm_hint = None
        if user_just_confirmed:
            confirmed_actions = await self.confirm_gate.confirm_all_pending(
                conversation_id, hub_user_id,
            )
            if confirmed_actions:
                # 把所有 pending 拼成结构化 hint。v5 round 2 P1：必须同时传 (action_id, token) 两个字段，
                # ToolRegistry.call 用 action_id 做原子 claim，token 做一致性校验
                lines = [
                    f"用户已确认 {len(confirmed_actions)} 个写操作。请按下表重新调用对应 tool，"
                    "**每次调用必须同时传 confirmation_action_id 和 confirmation_token 两个字段**："
                ]
                for a in confirmed_actions:
                    args_summary = json.dumps(a["args"], ensure_ascii=False)[:200]
                    lines.append(
                        f"  • {a['tool_name']}: args={args_summary} "
                        f"→ confirmation_action_id=\"{a['action_id']}\" "
                        f"confirmation_token=\"{a['token']}\""
                    )
                lines.append(
                    "注意：每对 (action_id, token) 只能用 1 次。失败时（tool 抛错）token 会被 HUB 还原，可重试同对；"
                    "成功后 HUB 会原子消费，不可再用。"
                )
                confirm_hint = "\n".join(lines)
                # 不在这里 clear_pending；ToolRegistry.call 在 tool 成功后调
                # ConfirmGate.remove_pending 按 action_id 单条清；其他 pending 不受影响。
            # 没 pending：用户随口"是"，confirm_hint 留 None，正常进 LLM

        # ❷ 主循环
        for round_idx in range(self.MAX_ROUNDS):
            messages = await self.context_builder.build_round(
                round_idx=round_idx, base_memory=memory, tools_schema=tools,
                conversation_history=self.history,
                latest_user_message=user_message if round_idx == 0 else None,
                confirm_state_hint=confirm_hint if round_idx == 0 else None,
            )
            llm_resp = await asyncio.wait_for(
                self.llm.chat(messages, tools=tools, temperature=0.0),
                timeout=self.LLM_TIMEOUT,
            )
            self.history.append(llm_resp)

            if llm_resp.is_tool_call:
                for call in llm_resp.tool_calls:
                    try:
                        result = await self.registry.call(
                            call.name, call.args,
                            hub_user_id=hub_user_id, acting_as=acting_as,
                            conversation_id=conversation_id, round_idx=round_idx,
                        )
                        self.history.append(ToolResult(call.id, result))
                        # v5 round 2：pending 清理由 ToolRegistry.call 内部按 confirmation_action_id
                        # 调 ConfirmGate.remove_pending 完成；ChainAgent 这里不再做按 args 匹配的兜底。
                    except UnconfirmedWriteToolError as e:
                        # 写门禁拦截 → 加入 pending list（不覆盖之前的；按 action_id 单条存）
                        await self.confirm_gate.add_pending(
                            conversation_id, hub_user_id, call.name, call.args,
                        )
                        # 注入错误让 LLM 改用 text 预览给用户
                        self.history.append(ToolResult(call.id, {"error": str(e)}))
                continue

            if llm_resp.is_clarification:
                return AgentResult.clarification(llm_resp.text)
            return AgentResult.text(llm_resp.text)

        raise AgentMaxRoundsExceeded()
```

**单聊 vs 群聊 conversation_id 设计**：

| 场景 | conversation_id 取值 | 隔离级别 |
|---|---|---|
| 钉钉 1:1 私聊 | `dingtalk:{senderStaffId}`（每个用户独立）| 天然单用户 |
| 钉钉群聊 @ 机器人 | `dingtalk:{conversationId}`（群级）| 同 conversation 多用户 → **必须靠 hub_user_id 隔离** |

ConfirmGate 用 `(conversation_id, hub_user_id)` 双键已经处理了群聊 B 不能确认 A 的写的情况：
- A 在群 G 调 create_voucher_draft 被拦截 → pending 写到 `pending:G:hub_user_A`
- B 在同群回 "是" → ChainAgent 找的是 `pending:G:hub_user_B` → 空 → confirm_hint=None → 正常进 LLM 处理

**inbound handler 端识别"是/确认"**（在 Task 10 加入）：

```python
# hub/handlers/dingtalk_inbound.py 业务路径处理前
RE_CONFIRM = re.compile(r"^\s*(是|确认|yes|y|ok|确定)\s*$", re.IGNORECASE)

# ... rule 命令路由 / identity_service.resolve / pending_choice 数字回路 都不变 ...

user_just_confirmed = bool(RE_CONFIRM.match(content))
# 如果是确认词 + Redis 有 pending_write → 后续 ChainAgent 会 mark_confirmed 并提示 LLM 重试
agent_result = await chain_agent.run(
    user_message=content,
    hub_user_id=resolution.hub_user_id,
    conversation_id=conversation_id,
    acting_as=resolution.erp_user_id,
    user_just_confirmed=user_just_confirmed,
)
```

**完整对话样例**（端到端验证）：

| Round | 钉钉消息 | ChainAgent 行为 | Redis 状态 |
|---|---|---|---|
| 1 | 用户: "把上周差旅做凭证" | LLM 调 search_orders / create_voucher_draft（无 token）→ 写门禁拦截 → save_pending_write → LLM 改输出 text 预览 → ChainAgent 返回 text | `pending_write:{tool: create_voucher_draft, args: {...}}` |
| 1 端 | bot: "我准备创建凭证：差旅费 ¥3,200，借管理费用-差旅 / 贷库存现金。回复'是'确认提交。" | — | 同上 |
| 2 | 用户: "是" | inbound 识别 RE_CONFIRM → user_just_confirmed=True → ChainAgent mark_confirmed + clear_pending_write + 注入 confirm_hint → LLM 看到 hint 重新调 create_voucher_draft 带 token → ToolRegistry is_confirmed=True → 真执行 | 清空 pending_write，confirmed set 加 token |
| 2 端 | bot: "凭证草稿已生成（draft_id=42），等会计审批。" | — | — |

测试覆盖（review P1-#2）：
```python
async def test_first_call_blocked_then_user_confirms_then_retry():
    """端到端：第一次 LLM 调 create_voucher_draft → 拦截 → 用户回'是' → 第二轮 LLM 自动带 token 调用 → 通过。"""

async def test_user_confirms_but_no_pending_write():
    """用户回'是'但没 pending_write（直接说'是'）→ 不报错，正常进 LLM 处理。"""

async def test_pending_write_expires_after_30min():
    """30min 后 pending_write Redis 过期 → 用户再回'是'无效，需重新发起请求。"""
```

- [ ] **Step 3: 测试合计**

| 类别 | 数量 |
|---|---|
| 原有 ChainAgent 主循环测试 | 12 |
| ContextBuilder 大 tool result / 优先级裁剪 / 必保超预算 | 4 |
| 用户确认链路（Step 2.5）| 3 |
| **合计** | **19** |

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

- [ ] **Step 2: admin/approvals.py 三个 inbox 子路由（review P1-#4 改进版：草稿状态机 + 幂等 + creating 崩溃恢复）**

**review P1-#3 关键问题**：v2 写法仍是循环 N 次 `POST /vouchers` 创建 ERP，中途失败留下部分 ERP 副作用，重试还会重复创建。修复方案：**两阶段提交 + client_request_id 幂等键 + creating 状态租约**。

**voucher_draft 状态机（v3 round 2 加 creating 租约恢复）**：

```
pending  ──[lock to creating]──▶  creating  ──[ERP create OK]──▶ created
   ▲                                  │                            │
   │                                  │ ──[ERP create fail]──▶ pending（释放锁，可重试）
   │                                  │
   │                                  └─[进程崩溃]──▶ creating（卡住，但 lease 5 min 后过期）
   │                                                       │
   │                                                       │ ──[下次 batch 拿同 client_request_id 重试]
   │                                                       │    ERP 用幂等键返已存在的 voucher
   │
   └─[reject]──▶ rejected                              ──▶ created
                                                            │
                                                  ──[batch-approve]──▶ approved
                                                            │
                                                  ──[approve fail]──▶ created（保持，可重试 approve）
```

**关键设计**：
1. **`creating` 是租约状态**：进入 creating 时记 `creating_started_at`；下次 batch 看到 `now - creating_started_at > LEASE_TIMEOUT(5min)` 就视为崩溃残留，可重新拿锁继续。
2. **`client_request_id` = `f"hub-draft-{draft.id}"`** 全程不变：进程崩溃后下次 batch 拿同 client_request_id 调 ERP，ERP 端唯一索引保证不会重复创建（Task 18 ERP 改动）。
3. **乐观锁防并发同 batch**：`filter(status__in=["pending", "creating"], creating_started_at__lt=lease_cutoff or null) → update(status="creating", creating_started_at=now)`，update 行数=0 表示别的请求已抢先。

```python
import datetime as dt

LEASE_TIMEOUT = dt.timedelta(minutes=5)  # creating 租约过期时间

@router.get("/voucher", deps=[require_hub_perm("usecase.create_voucher.approve")])
async def list_voucher_drafts(status: str = "pending", ...): ...

@router.post("/voucher/batch-approve",
             deps=[require_hub_perm("usecase.create_voucher.approve")])
async def batch_approve_vouchers(req: BatchApproveRequest, request: Request):
    """两阶段提交 + creating 崩溃恢复：
       phase1: 把 status∈{pending, creating(租约过期)} 的转成 creating + 调 ERP create
               （client_request_id 幂等键防重 → ERP 端唯一索引）
       phase2: 把 status=created 的（含本次新创建的 + 入参原本 created）一次性调 batch-approve
    """
    now = dt.datetime.utcnow()
    lease_cutoff = now - LEASE_TIMEOUT

    # 注意：drafts 入参允许 pending / creating(过期) / created（重试 approve）
    drafts = await VoucherDraft.filter(
        id__in=req.draft_ids,
        status__in=["pending", "creating", "created"],
    ).all()
    if len(drafts) != len(req.draft_ids):
        raise HTTPException(400, "包含已审/已拒/不存在的 draft_id")

    # 把 creating 但租约未过期的标"in_progress"，单独返让 UI 显示"处理中"
    # （review v4 第三轮 P2-#3：不能静默吞掉，否则用户看到 approved=0 / failed=[] 没法判断到底发生了什么）
    in_progress: list[dict] = []
    actionable_drafts: list = []
    for d in drafts:
        if d.status == "creating" and (
            d.creating_started_at is None or d.creating_started_at >= lease_cutoff
        ):
            in_progress.append({
                "draft_id": d.id,
                "since": d.creating_started_at.isoformat() if d.creating_started_at else None,
                "lease_expires_at": (
                    (d.creating_started_at + LEASE_TIMEOUT).isoformat()
                    if d.creating_started_at else None
                ),
                "reason": "另一会话正在处理此草稿，请稍后重试或等租约自动过期",
            })
        else:
            actionable_drafts.append(d)
    drafts = actionable_drafts

    actor = request.state.hub_user
    creation_failures = []

    # ========== Phase 1: 把 pending / creating(过期) 的草稿创建到 ERP（幂等 + 租约）==========
    todo_drafts = [d for d in drafts if d.status in ("pending", "creating")]
    for d in todo_drafts:
        # 乐观锁 + 租约：仅当 (status=pending) OR (status=creating AND lease 已过期) 时才能拿锁
        rows = await VoucherDraft.filter(id=d.id).filter(
            Q(status="pending")
            | (Q(status="creating") & Q(creating_started_at__lt=lease_cutoff)),
        ).update(
            status="creating",
            creating_started_at=now,
        )
        if rows == 0:
            continue  # 别的请求/线程已抢先，跳过

        try:
            erp_resp = await erp.create_voucher(
                voucher_data=d.voucher_data,
                client_request_id=f"hub-draft-{d.id}",  # 幂等键，崩溃重试同样这个值
                acting_as_user_id=...,
            )
            # 注意：ERP 可能返 idempotent_replay=True（同 key 已存在），仍走成功分支
            d.erp_voucher_id = erp_resp["id"]
            d.status = "created"
            d.creating_started_at = None  # 清租约
            await d.save()
        except (ErpAdapterError, ErpSystemError) as e:
            # 创建失败 → 回滚 pending（释放租约让下次重试）
            await VoucherDraft.filter(id=d.id).update(
                status="pending", creating_started_at=None,
            )
            creation_failures.append({"draft_id": d.id, "reason": str(e)})

    # 重新拉一次：这次包含 phase1 新创建的 + 入参中本来就 status=created 的
    created_drafts = await VoucherDraft.filter(
        id__in=req.draft_ids, status="created",
    ).all()

    if not created_drafts:
        # review v5 第二轮 P2-#3：早返回前也写一条 audit log，避免 in_progress-only 批量审批
        # 不留任何痕迹（管理层无法看到"会计 A 在 X 时点尝试批了 N 张但全在处理中"）
        await AuditLog.create(
            who_hub_user_id=actor.id, action="batch_approve_vouchers",
            target_type="voucher_draft",
            target_id=str(req.draft_ids),
            detail={
                "approved": [],
                "creation_failed": creation_failures,
                "approve_failed": [],
                "in_progress": [p["draft_id"] for p in in_progress],
                "early_return_reason": (
                    "no_actionable_drafts"  # 全部 in_progress 或 phase1 全失败
                ),
            },
        )
        return {
            "approved_count": 0, "approved_draft_ids": [],
            "failed": creation_failures,
            "in_progress": in_progress,  # 让 UI 区分"处理中"vs"全部失败"
        }

    # ========== Phase 2: 一次性调 ERP batch-approve（事务）==========
    erp_voucher_ids = [d.erp_voucher_id for d in created_drafts]
    result = await erp.batch_approve_vouchers(
        voucher_ids=erp_voucher_ids, acting_as_user_id=...,
    )
    approved_set = set(result.get("success", []))
    failed_map = {f["id"]: f["reason"] for f in result.get("failed", [])}

    approved_draft_ids = []
    approve_failures = []
    for d in created_drafts:
        if d.erp_voucher_id in approved_set:
            d.status = "approved"
            d.approved_by_hub_user_id = actor.id
            d.approved_at = now_utc()
            await d.save()
            approved_draft_ids.append(d.id)
        else:
            # status 保持 created（可重试 approve），不回 pending（防重复创建）
            approve_failures.append({
                "draft_id": d.id, "erp_voucher_id": d.erp_voucher_id,
                "reason": failed_map.get(d.erp_voucher_id, "ERP 拒绝"),
            })

    await AuditLog.create(
        who_hub_user_id=actor.id, action="batch_approve_vouchers",
        target_type="voucher_draft",
        target_id=str(req.draft_ids),
        detail={
            "approved": approved_draft_ids,
            "creation_failed": creation_failures,
            "approve_failed": approve_failures,
            "in_progress": [p["draft_id"] for p in in_progress],
        },
    )
    return {
        "approved_count": len(approved_draft_ids),
        "approved_draft_ids": approved_draft_ids,
        "failed": creation_failures + approve_failures,
        "in_progress": in_progress,  # review v4 第三轮 P2-#3：让 UI 显示"处理中"
    }


@router.post("/voucher/batch-reject")
async def batch_reject_vouchers(req, request):
    """拒绝：仅 pending 可拒（created 已落 ERP 不能在 HUB 端拒，要在 ERP 端反审）。"""
    drafts = await VoucherDraft.filter(id__in=req.draft_ids, status="pending").all()
    if len(drafts) != len(req.draft_ids):
        raise HTTPException(400, "包含 created/已拒/已通过的 draft（请到 ERP 反审）")
    for d in drafts:
        d.status = "rejected"
        d.rejection_reason = req.reason
        await d.save()
    await AuditLog.create(...)
    return {"rejected_count": len(drafts)}

# /price /stock 类似（先不做 ERP batch endpoint；草稿数通常单位数）
```

**关键保证**：
1. **不重复创建 ERP voucher**：`client_request_id` 唯一约束让 ERP 端拒重复（Task 18 ERP 加）；HUB 端乐观锁 + 中间态 `creating` 租约防并发同 batch
2. **崩溃恢复**：phase1 调 ERP 时进程崩溃 → draft 卡在 `creating` 状态。下次 batch 看到 `creating_started_at` 已过 5min 租约，重新拿锁继续；ERP 收同 client_request_id 后通过唯一索引判幂等返回已存在的 voucher（不会重复创建）
3. **重试安全**：phase1 显式失败 → 回滚 pending（可重试创建）；phase2 失败 → 保持 created（可重试 approve），ERP voucher 不会重复创建
4. **部分失败可恢复**：返回详细 `creation_failures + approve_failures`，admin UI 显示 → 用户可只重试失败的子集

**Task 18 ERP 改动**配套（plan Task 18 加这一条）：
- `Voucher` 表加 `client_request_id` 字段（VARCHAR 64，NULL OK）+ partial unique index（仅当 client_request_id NOT NULL 时唯一）
- `POST /api/v1/vouchers` body 接受 `client_request_id` 字段；冲突时返已存在的 voucher（HTTP 200 + 标记 idempotent_replay=True）

- [ ] **Step 3: 测试 18 case（含 creating 租约恢复 + in_progress 暴露 + 早返回 audit log）**

| # | 场景 | 期望 |
|---|---|---|
| 1 | 创建凭证草稿成功 | voucher_draft 写入 + status=pending + creating_started_at=NULL |
| 2 | 创建超金额上限 | 拦截 + ¥1M 错误信息 |
| 3 | 必填字段校验 | 拒绝 + schema error |
| 4 | 批量通过：phase1+phase2 全成功 | 全部 approved + audit log + creating_started_at 清空 |
| 5 | phase1 创建部分失败（ERP 5xx）| 失败的回滚 pending（清 creating_started_at），成功的进 phase2 |
| 6 | phase2 batch-approve 部分失败 | 失败的保持 created（不回 pending）|
| 7 | phase2 全失败（ERP 5xx）| 全部保持 created |
| 8 | 重试（同 draft_id 二次 batch-approve）| created 状态跳过 phase1，只走 phase2 |
| 9 | 重试（用同 client_request_id）| ERP 返已存在的 voucher（idempotent_replay=True）+ HUB draft 不重复创建 |
| 10 | 批量通过含 approved/rejected draft | 400 |
| 11 | 批量拒绝 pending → status=rejected | OK |
| 12 | 批量拒绝 created（已落 ERP）→ 400 | "请到 ERP 反审" |
| 13 | 无审批权限 | 403 |
| 14 | 同 draft_id 并发 batch-approve | 第二次乐观锁失败，跳过该条（status=creating + 租约未过期）|
| 15 | **崩溃恢复（租约过期）**：人为造一条 status=creating + creating_started_at=10 分钟前 → 再次 batch-approve 同 draft_id | 视为崩溃残留，重新进 phase1 + 同 client_request_id 调 ERP；ERP 返 idempotent_replay → 标 created → 进 phase2 |
| 16 | **租约未过期跳过**：status=creating + creating_started_at=2 分钟前 → 再次 batch-approve | 当作"另一个进程在处理"，**该条不进 phase1，但出现在 response.in_progress 中**（含 since / lease_expires_at / 中文 reason），UI 可显示"处理中"区别于"全部失败" |
| 17 | **混合 in_progress + 正常**：3 张 draft 中 1 张 creating(2min 前 lease 未过)，2 张 pending → batch-approve 全部 | 2 张正常进 phase1+phase2 → approved_count=2；in_progress 数组含那 1 条；失败列表为空 |
| 18 | **全 in_progress 早返回**（review v5 第二轮 P2-#3）：3 张 draft 都 creating + lease 未过 → batch-approve 全部 | approved_count=0 + failed=[]；**audit log 仍写入一条**，detail 含 in_progress draft_id + early_return_reason="no_actionable_drafts" + actor，UI/管理层可看到"会计 X 在 T 时点尝试批 3 张但全部在处理中" |

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

- [ ] **Step 1: 加 14 权限码 + 2 角色（完整列表）**

```python
# seed.py 追加（schema 与 PERMISSIONS 列表一致：(code, resource, sub, action, name, description)）
NEW_PERMISSIONS_PLAN6 = [
    ("usecase.query_customer.use", "usecase", "query_customer", "use",
     "查客户", "搜索客户列表"),
    ("usecase.query_inventory.use", "usecase", "query_inventory", "use",
     "查库存", "查商品库存信息"),
    ("usecase.query_orders.use", "usecase", "query_orders", "use",
     "查订单", "搜索订单列表与详情"),
    ("usecase.query_customer_balance.use", "usecase", "query_customer_balance", "use",
     "查客户余额", "应收 / 已付 / 未付汇总"),
    ("usecase.query_inventory_aging.use", "usecase", "query_inventory_aging", "use",
     "查库龄", "滞销商品分析"),
    ("usecase.analyze.use", "usecase", "analyze", "use",
     "数据分析", "TOP 客户 / 滞销 / 周转等聚合"),
    ("usecase.generate_quote.use", "usecase", "generate_quote", "use",
     "生成报价单", "agent 生成报价 docx 发用户"),
    ("usecase.export.use", "usecase", "export", "use",
     "导出 Excel", "把查询结果导成 .xlsx 发用户"),
    ("usecase.adjust_price.use", "usecase", "adjust_price", "use",
     "提交调价请求", "销售给客户提交特价请求草稿"),
    ("usecase.adjust_price.approve", "usecase", "adjust_price", "approve",
     "审批调价", "销售主管审批调价请求"),
    ("usecase.adjust_stock.use", "usecase", "adjust_stock", "use",
     "提交库存调整", "提交盘点 / 调拨草稿"),
    ("usecase.adjust_stock.approve", "usecase", "adjust_stock", "approve",
     "审批库存调整", "仓管审批库存调整"),
    ("usecase.create_voucher.approve", "usecase", "create_voucher", "approve",
     "审批凭证", "会计批量审凭证草稿（Plan 4 已有 .use）"),
    ("platform.contract_templates.write", "platform", "contract_templates", "write",
     "管理合同模板", "上传 / 编辑合同 docx 模板"),
]

NEW_ROLES_PLAN6 = {
    "bot_user_sales_lead": {
        "name": "机器人 - 销售主管",
        "description": "继承销售权限，加调价审批",
        "permissions": [
            # 继承 bot_user_sales 的所有权限 + 加 .approve
            "channel.dingtalk.use",
            "downstream.erp.use",
            "usecase.query_product.use",
            "usecase.query_customer.use",
            "usecase.query_customer_history.use",
            "usecase.query_inventory.use",
            "usecase.query_orders.use",
            "usecase.query_customer_balance.use",
            "usecase.analyze.use",
            "usecase.generate_contract.use",
            "usecase.generate_quote.use",
            "usecase.export.use",
            "usecase.adjust_price.use",
            "usecase.adjust_price.approve",  # ← 新增
        ],
    },
    "bot_user_finance_lead": {
        "name": "机器人 - 会计主管",
        "description": "继承会计权限，加凭证 + 库存调整审批",
        "permissions": [
            "channel.dingtalk.use",
            "downstream.erp.use",
            "usecase.query_product.use",
            "usecase.query_customer.use",
            "usecase.query_customer_balance.use",
            "usecase.query_orders.use",
            "usecase.create_voucher.use",
            "usecase.create_voucher.approve",  # ← 新增
            "usecase.adjust_stock.use",
            "usecase.adjust_stock.approve",   # ← 新增
            "usecase.export.use",
        ],
    },
}

# 同时升级现有角色（在 ROLES dict 中追加）
# - bot_user_basic 加：query_customer / query_inventory / query_orders
# - bot_user_sales 加：query_customer / query_customer_balance / query_inventory /
#                    query_orders / generate_quote / export / adjust_price.use
# - bot_user_finance 加：query_customer / query_customer_balance / query_orders /
#                       adjust_stock.use / export
# - platform_admin 自动获得全部新权限（其 permissions 字段是 [p[0] for p in PERMISSIONS]）
```

- [ ] **Step 2: 业务词典默认数据**

```python
DEFAULT_BUSINESS_DICT_SEED = {
    "压货": "库龄高的商品（调 get_inventory_aging）",
    "周转": "商品周转率（调 analyze_slow_moving_products）",
    "回款": "客户应付未付的款项（调 get_customer_balance）",
    "上次价格 / 上次价 / 之前的价": "该客户最近一次该商品成交价（调 get_customer_history limit=1）",
    "差旅 / 报销": "差旅费用报销，对应凭证科目 借管理费用-差旅 / 贷库存现金",
    "套餐 / 组合": "多 SKU 打包销售，每个 SKU 独立列在合同条款中",
    # ... 共 50 条术语，由用户实际使用反馈持续扩
}
# 写入 system_config.business_dict（admin 后台可编辑）
```

- [ ] **Step 3: 提交**

```bash
git commit -m "feat(hub): Plan 6 Task 17（seed 加 14 权限码 + 2 新角色 + 业务词典默认数据）"
```

---

## Task 18：ERP 仓库改动（跨仓库依赖）

**Files (ERP 仓库):**
- Create: `backend/app/routers/customer_price_rules.py`
- Modify: `backend/app/routers/inventory.py`（加 /aging）
- Modify: `backend/app/models/voucher.py`（加 client_request_id 字段）
- Modify: `backend/app/routers/vouchers.py`（接 client_request_id + 幂等回放）
- Modify: `backend/app/schemas/voucher.py`（VoucherCreate 加 client_request_id 可选字段）
- Migrations: `backend/migrations/models/N_xxx_plan6_erp.py`（手写：customer_price_rule 表 + voucher.client_request_id 列 + 部分唯一索引）

- [ ] **Step 1: customer_price_rules.py（HUB tool `create_price_adjustment_request` 落库依赖）**

```python
@router.post("")
async def create_price_rule(...): ...

@router.patch("/{rule_id}")
async def update_price_rule(...): ...

@router.get("")
async def list_price_rules(customer_id, product_id): ...
```

- [ ] **Step 2: /api/v1/inventory/aging endpoint（HUB tool `get_inventory_aging` 依赖）**

按库龄聚合：
```python
@router.get("/aging")
async def inventory_aging(threshold_days: int = 90, warehouse_id: int = None):
    # 查 stock_log + 计算 age_days
    return {"items": [{product_id, sku, name, total_stock, age_days, value}]}
```

- [ ] **Step 3: Voucher.client_request_id 幂等键（HUB Task 8 两阶段提交 + 崩溃恢复依赖）**

> **强阻塞**：没有这一步，HUB 端 phase1 调 ERP create_voucher 进程崩溃后下次 batch 重试会重复创建 ERP voucher（脏数据）。

**3.1 Model 层加字段**：

```python
# backend/app/models/voucher.py（Tortoise ORM）
class Voucher(Model):
    id = fields.IntField(pk=True)
    # ... 既有字段 ...
    client_request_id = fields.CharField(max_length=64, null=True)
    # 部分唯一索引在迁移里建（Tortoise 不支持 partial unique meta）

    class Meta:
        table = "voucher"
```

**3.2 手写迁移加部分唯一索引**：

```python
# backend/migrations/models/N_xxx_plan6_erp.py
async def upgrade(db) -> str:
    return """
    -- customer_price_rule 表（Step 1）
    CREATE TABLE customer_price_rule (...);
    -- voucher.client_request_id（Step 3）
    ALTER TABLE voucher ADD COLUMN client_request_id VARCHAR(64);
    -- 部分唯一索引：仅当 NOT NULL 时唯一（允许历史 voucher 全部为 NULL）
    CREATE UNIQUE INDEX idx_voucher_client_request_id_unique
        ON voucher (client_request_id)
        WHERE client_request_id IS NOT NULL;
    """
```

**3.3 Schema 接收字段**：

```python
# backend/app/schemas/voucher.py
class VoucherCreate(BaseModel):
    # ... 既有字段 ...
    client_request_id: str | None = Field(
        default=None, max_length=64,
        description="HUB 调用方传入的幂等键。同一 key 重复 POST 返已存在的 voucher（200 + idempotent_replay=True）。",
    )
```

**3.4 Router 幂等回放语义**：

```python
# backend/app/routers/vouchers.py
from tortoise.exceptions import IntegrityError

@router.post("", response_model=VoucherResponse)
async def create_voucher(payload: VoucherCreate, ...):
    """凭证创建（含幂等回放）。

    幂等语义（review v4 第三轮 P1：实际处理并发唯一冲突）：
    - 不传 client_request_id：按普通创建走（idempotent_replay=False）
    - 传 client_request_id：
        1. 先查：命中 → 直接返已存在 voucher（idempotent_replay=True）
        2. 未命中 → 尝试 INSERT
        3. 并发场景：两个请求同时走到 #2，第一个 INSERT 成功，第二个被
           PostgreSQL UNIQUE 索引拒（IntegrityError）；catch + 重新查询 → 返已存在
        4. 状态码统一 200：不是 409，让 HUB 端 phase1 流程不被打断

    返回：始终 200 + idempotent_replay 字段标识本次是否实际创建。
    """
    if payload.client_request_id:
        # 幂等回放：先按 client_request_id 查
        existing = await Voucher.filter(
            client_request_id=payload.client_request_id,
        ).first()
        if existing:
            return VoucherResponse(
                **existing.to_dict(),
                idempotent_replay=True,
            )

        # 未命中 → 尝试 INSERT；并发场景下唯一索引可能抛 IntegrityError
        try:
            voucher = await Voucher.create(
                **payload.dict(exclude_unset=True),
            )
        except IntegrityError as exc:
            # 并发兜底：另一并发请求刚刚用同 client_request_id 创建成功
            # → 本次的 INSERT 撞上唯一索引 → 重新查询 → 返已存在
            # 必须确认是 client_request_id 唯一冲突，而非其他约束
            if "client_request_id" not in str(exc).lower():
                raise  # 其他唯一约束冲突照常向上抛
            existing = await Voucher.filter(
                client_request_id=payload.client_request_id,
            ).first()
            if not existing:
                # 极罕见：抛了 IntegrityError 但又查不到 → ORM/DB 层异常状态，向上抛
                raise
            return VoucherResponse(
                **existing.to_dict(),
                idempotent_replay=True,
            )
        return VoucherResponse(**voucher.to_dict(), idempotent_replay=False)

    # 不传 client_request_id：普通路径
    voucher = await Voucher.create(**payload.dict(exclude_unset=True))
    return VoucherResponse(**voucher.to_dict(), idempotent_replay=False)
```

**3.5 测试 case（ERP 仓库 backend/tests/test_voucher_idempotent.py）**：

| # | 场景 | 期望 |
|---|---|---|
| 1 | 不传 client_request_id 创建 | 正常 200 + idempotent_replay=False |
| 2 | 传新 client_request_id 创建 | 200 + voucher 持久化含此 key |
| 3 | 重复传相同 client_request_id（顺序两次）| 200 + 返第 1 次的 voucher + idempotent_replay=True |
| 4 | 不同 voucher_data 但同 client_request_id | 200 + 返第 1 次的 voucher（不更新 voucher_data；幂等保证一致性，不接受第二次输入） |
| 5 | 历史 voucher（client_request_id NULL） | 不互相冲突（部分唯一索引允许多 NULL）|
| 6 | **并发同 client_request_id（asyncio.gather 两个 POST）** | 两次都返 200；其中一个 idempotent_replay=False（赢），另一个 idempotent_replay=True（被 IntegrityError catch 后回查）；DB 中只有 1 条 voucher |
| 7 | 客户端 mock：第一次 INSERT 抛 IntegrityError（but 'client_request_id' 字样在 exc 里），回查能查到 | 走 except 分支 + 返 idempotent_replay=True |
| 8 | 客户端 mock：抛 IntegrityError 但 exc 不含 'client_request_id'（其他唯一约束）| 直接 reraise，路由返 5xx 让 ERP 客户端 retry |
| 9 | 客户端 mock：抛 IntegrityError 但回查不到（数据库状态异常）| 直接 reraise，不假装成功 |

**测试 6 的并发模式**（pytest-asyncio）：
```python
async def test_concurrent_idempotent_replay():
    payload = {"client_request_id": "hub-draft-99", "voucher_data": {...}}
    # asyncio.gather 让两个 POST 同时走（HTTP 客户端独立 connection）
    r1, r2 = await asyncio.gather(
        client.post("/api/v1/vouchers", json=payload),
        client.post("/api/v1/vouchers", json=payload),
    )
    assert r1.status_code == r2.status_code == 200
    bodies = [r1.json(), r2.json()]
    replays = [b["idempotent_replay"] for b in bodies]
    assert sorted(replays) == [False, True]  # 一个赢，一个被 catch 回查
    assert bodies[0]["id"] == bodies[1]["id"]  # 返同一 voucher
    assert await Voucher.filter(client_request_id="hub-draft-99").count() == 1
```

- [ ] **Step 4: 在 ERP 仓库提交**

```bash
cd /Users/lin/Desktop/ERP-4
git add backend/app/models/voucher.py \
        backend/app/schemas/voucher.py \
        backend/app/routers/vouchers.py \
        backend/app/routers/customer_price_rules.py \
        backend/app/routers/inventory.py \
        backend/migrations/models/
git commit -m "feat: ERP for HUB Plan 6（customer-price-rules CRUD + inventory/aging + voucher.client_request_id 幂等键）"
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
| §14 ERP 改动（含 Plan 6 新增 voucher.client_request_id 幂等键）| Task 18 | ✓ |

### Placeholder Scan（实事求是版）

- ✓ 无 "TBD" / "TODO" / "implement later" / "fill in details" 等纯占位词
- ✓ 无 "类似 Task N" 这种引用其他 Task 才能理解的省略
- ✓ 每个 Task 都有明确 Files / Steps / commit msg
- ✓ 每个 Step 都有可执行命令 / 测试断言或测试表
- ⚠️ **完整实现 vs 设计骨架的区分**：
  - 「**完整实现**」（执行者照抄即可）：Task 1（tool_logger 全实现 + migration 关键 SQL）、Task 2（ToolRegistry 全实现含写门禁 + ConfirmGate）、Task 4（SessionMemory + UserMemory 全实现）、Task 6（ChainAgent.run 全实现含 ContextBuilder + RE_CONFIRM 链路 + 5 阈值）、Task 8（batch_approve_vouchers 含两阶段提交 + creating 租约恢复全实现）、Task 17（seed.py 14 权限码 + 2 角色 + 业务词典 dict 全列出）、Task 18（ERP voucher.client_request_id 模型 + 迁移 + router 全实现）
  - 「**设计骨架**」（执行者需照 spec + 同 Task 测试用例补完，不是占位）：Task 3 各 ERP read tool 函数体（套路一致：调 erp_adapter + 包装）、Task 4 CustomerMemory / ProductMemory（schema 与 UserMemory 同型）、Task 5 PromptBuilder（few-shots 列表照 spec §3.5 抄）、Task 7 generate_contract / generate_excel_query（按测试 case + 文件目录骨架照抄）、Task 9 analyze_top_customers / analyze_slow_moving_products（聚合套路 spec §4.4 已给）、Task 10 handle_inbound 升级（Plan 5 已有 inbound handler 全文，仅插入 ChainAgent 调用）、Task 11-15 admin UI（Vue SFC + 路由，参考 Plan 5 既有 admin 页风格）、Task 16 EvalRunner（标准 LLM 评测框架，不是新发明）

- 上述「设计骨架」类 Task 都明确给了：(1) 文件路径 (2) 测试用例表（执行者 TDD 时先写测试就能反推实现） (3) spec 对应章节锚点。这与 "TODO: implement"（无任何细节）有本质区别。

### 类型一致性

- ✓ ToolRegistry.call() 签名跨 Task 一致（hub_user_id / acting_as / conversation_id / round_idx + 可选 confirmation_token in args）
- ✓ AgentResult 类型跨 Task 6 / Task 10 一致
- ✓ Memory dataclass 字段跨 Task 4 / Task 5 / Task 6 一致（SessionContext / UserMemory / CustomerMemory / ProductMemory）
- ✓ ToolDef.fn 必须 async + 必须有 acting_as_user_id 参数（ToolRegistry.register 校验签名）
- ✓ ConfirmGate (conversation_id, hub_user_id) 二元组隔离 + action_id 多 pending 支持 — 跨 Task 2 / Task 6 / Task 10 一致
- ✓ voucher_draft 状态机 5 值（pending / creating / created / approved / rejected）跨 spec §5.4 / Task 1 / Task 8 / Task 18 一致
- ✓ client_request_id 命名：HUB 端 `f"hub-draft-{draft.id}"` 与 ERP schema VARCHAR(64) NULL 一致
- ✓ confirmation 链路一次性 + 真正抗并发（v6）：ConfirmGate.claim_action（Lua HGET+HDEL 原子）+ restore_action / remove_pending；ToolRegistry.call 用 (confirmation_action_id, confirmation_token) 双字段；token 含 action_id（同 args 多 pending 不撞 token）
- ✓ ERP idempotent_replay 字段语义跨 Task 8（HUB phase1 调用方）/ Task 18（ERP router 返回方）/ ChainAgent confirm hint 一致

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

**Plan 6 v3 结束（应用第二轮 review 6 条反馈：实现块完整化 / 确认链路端到端 / 批量审批两阶段提交幂等 / ContextBuilder must_keep 拆分 / should_extract 签名修正 / 占位 + 反馈口径全清）**

**Plan 6 v4 结束（应用第三轮 review 6 条反馈）**：
- P1 #1：ConfirmGate 按 (conversation_id, hub_user_id) 二元组隔离 + action_id 支持单 round 多 pending（跨 Task 2 / 6 / 10 端到端打通）
- P1 #2：Task 18 加 ERP `voucher.client_request_id` 字段 + 部分唯一索引 + 幂等回放（Task 8 强阻塞依赖）
- P1 #3：Task 8 加 `creating_started_at` 5 min 租约 + 崩溃恢复路径（status=creating 过期则重新拿锁，client_request_id 不变让 ERP 唯一索引兜底防重复创建）
- P2 #4：ToolRegistry 全实现块补 imports 起手块
- P2 #5：spec §4.2 / §14 同步两阶段 + 幂等 + 租约恢复语义（§14 把 voucher 从"已有可复用"挪到"需要修改"）
- P3 #6：Self-Review Placeholder Scan 改为实事求是版（区分"完整实现" vs "设计骨架"，并标注每类骨架 Task 的可执行性来源）

**Plan 6 v5 结束（应用第四轮 review 4 条反馈，专注写操作安全/幂等的边界）**：
- P1 #1：confirmation_token **一次性消费** —— ConfirmGate 加 `consume_token` 原子 SREM；ToolRegistry.call 在 tool 成功执行后消费 token（失败不消费便于重试）；Task 2 加 2 个测试 case（"同 token 第二次调用被拒" + "tool 失败时 token 保留"），全 Task 测试 13 → 15 case
- P1 #2：ERP 幂等回放**真正处理并发唯一冲突** —— Task 18 router 实现块改为实际 `try/except IntegrityError + requery + idempotent_replay=True`（含分支：非 client_request_id 唯一冲突照常 reraise / IntegrityError 后回查不到也 reraise），测试 case 5 → 9（含 asyncio.gather 并发测）
- P2 #3：creating 租约未过期的草稿不再静默吞掉 —— batch-approve 返回新增 `in_progress` 数组（含 draft_id / since / lease_expires_at / 中文 reason），UI 可显示"处理中"区别于"全部失败"，audit log 同步含 in_progress draft_id；Task 8 测试 16 → 17 case
- P2 #4：ToolRegistry imports 完整列表搬进实现块顶部（含 `import time` / `Any` / `ToolArgsValidationError` / `logging.getLogger`），删掉块外的"必要 import"footnote

**Plan 6 v6 结束（应用第五轮 review 3 条反馈，写操作 confirmation 链路真正抗并发）**：
- P1 #1：v5 的"成功后 SREM"挡不住并发 → 改为 **tool.fn 前原子 claim_action**（Redis Lua HGET+HDEL）。ConfirmGate 全面重构：confirmed 从 set 改成 hash {action_id: data}；新 `claim_action` Lua 原子领取 + 一致性校验 + 失败 restore；新 `restore_action` 让 tool.fn 抛错时还原 confirmed；新 `remove_pending` 按 action_id 单条清。ToolRegistry.call 改用 claim+restore pattern。
- P1 #2：**token 绑定 action_id** —— compute_token payload 加 action_id；LLM 调写 tool 时必须**同时传 confirmation_action_id + confirmation_token** 两字段；ChainAgent confirm hint 改为按"action_id+token"对要求；schema 同时排除两个字段不暴露给 LLM。
- P2 #3：**in_progress-only 早返回写 audit log** —— 全部 draft 都 creating + 租约未过期时不再无声返回 0/0，audit log 加 `early_return_reason="no_actionable_drafts"` + in_progress draft_id；Task 8 加 case 18 验证。
- 测试新增：`test_write_tool_concurrent_claim_executes_only_once`（asyncio.gather 5 个并发只跑 1 次 tool.fn）+ `test_action_id_uniqueness_for_duplicate_args`（同 args 多 pending 各自独立）+ `test_token_cross_action_replay_blocked`（跨 action 复用 token 拦截）；Task 2 测试 15 → 17 case；Task 8 测试 17 → 18 case。
- 旧 `_cleanup_consumed_pending` ChainAgent 兜底删除（pending 清理责任已移到 ToolRegistry.call → ConfirmGate.remove_pending 按 action_id 单条做，更精确）

**Plan 6 v7 结束（应用第六轮 review 3 条反馈，confirmation 持久终态加固 + spec 同步）**：
- P1 #1：**权限/schema 校验移到 claim 前**（review v6 P1-#1）—— v6 把 require_permissions 和 _validate_args 放在 claim 后，权限被撤销 / schema 校验失败时 confirmed token 已被 HDEL 但 tool 没真跑，用户进入"已确认但失败"循环。修复：把这两步 stateless 校验提前；失败时不消费 confirmed token。
- P1 #2：**claim 原子删除 confirmed + pending 实现持久终态**（review v6 P1-#2）—— v6 写法 tool 成功后再 remove_pending；Redis 短暂故障导致 pending 残留时，用户回'是'重新 mark_confirmed → 同写操作再次执行（重复副作用）。修复：claim Lua 脚本扩成跨两个 hash 的原子 GETDEL；成功路径无任何后续 cleanup（持久终态）；失败 restore 也用 Lua 原子还回两个 hash；删除 v6 的 ConfirmGate.remove_pending 单条清 helper。
- P2 #3：**spec §3.1 / §8.4 同步 v6/v7 confirmation 协议**（review v6 P2-#3）—— spec 仍是旧 _is_confirmed + SET + token 不含 action_id 的描述，与 plan 实现冲突。同步项：token 加 action_id；双字段（confirmation_action_id + confirmation_token）；claim_action / restore_action / 三重一致性校验；权限+schema 前置；测试覆盖列表扩到 11 项；§8.4 防幻觉描述对齐。
- 测试新增（Task 2: 17 → 20 case）：
  - `test_permission_denied_does_not_consume_token`
  - `test_schema_validation_failure_does_not_consume_token`
  - `test_claim_atomically_removes_pending_so_reconfirm_safe`

总计：
- 19 个 Task
- 估时 5-7 周
- ~32 个新文件 + 12 个修改文件
- ~145 单元测试 + 30 eval gold set
- 跨仓库：HUB（主）+ ERP（约 2-3 天，加 customer-price-rules / inventory/aging / Voucher.client_request_id）

## v3 Review 修复清单（review 第二轮）

| # | 优先级 | 反馈 | 修复 |
|---|---|---|---|
| 1 | P1 | ToolRegistry 代码块没实现写门禁 | 完整实现 ToolRegistry.register（带 tool_type）+ ToolRegistry.call 5 步骤（写门禁 → 权限 → schema 校验 → 调 fn → 实体提取）+ ToolDef dataclass + ConfirmGate（canonicalize / compute_token / mark_confirmed / is_confirmed）+ EntityExtractor 完整代码 |
| 2 | P1 | 用户确认后没有落 confirm token 执行路径 | Task 6 加 Step 2.5：ChainAgent 加 _save/_load/_clear_pending_write + run() 接收 user_just_confirmed 参数 + confirm_state_hint 注入 + inbound handler 加 RE_CONFIRM 识别 + 完整对话样例 + 3 case 测试（端到端 / 无 pending / 30min 过期）|
| 3 | P1 | 批量审批仍 N 次 ERP 创建 | 改两阶段提交 + 状态机 pending → creating → created → approved；client_request_id 幂等键（Task 18 ERP 加唯一约束）；phase1 失败回滚 pending；phase2 失败保持 created（不重复创建）；测试 14 case 涵盖重试 / 部分失败 / 同 id 并发 |
| 4 | P2 | ContextBuilder 静默裁必保 | must_keep 与 can_truncate 拆开数据结构 — must_keep 超预算抛 PromptTooLargeError，can_truncate 按优先级**装填**（不是 pop）；修了 v2 的 `sections.append(...,priority=10)` 非法 Python；加 confirm_state_hint 进必保层 |
| 5 | P2 | should_extract 长对话判断不可实现 | 签名改 `should_extract(*, tool_call_logs, rounds_count)`，rounds_count >= 4 兜底独立判定 |
| 6 | P3 | 反馈删除不完整（11 处残留）| 全文 grep 清：删 conversation.has_feedback 字段 / agent_feedback.py 路由 / AgentFeedbackView.vue / agent_feedback.js / test_admin_agent_feedback / "AI 反馈"后台描述 / 风险区 👍/👎 提及；表数量统一为 9 |
| — | P2 | Placeholder Scan 与正文不符（多处 `...`）| 清主要占位：_py_to_json_type 完整实现；ERP 5 个新 endpoint params={...} 改具体；11 个 ERP tool 函数补完整签名 + 1 行实现；seed 14 权限码完整列表 + 2 新角色完整 perm list；ContextBuilder 摘要逻辑实现；加 plan 顶部"代码示例约定"区分完整实现 vs sketch |

---

## v2 Review 修复清单（review 第一轮，仅供历史回溯）

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
