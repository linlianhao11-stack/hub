# 业务用例 + AI Fallback 实施计划（Plan 4 / 5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Plan 1-3 已搭好的骨架上实现 C 阶段最小业务闭环——已绑定用户在钉钉发"查 SKU100" / "查 SKU100 给阿里" → HUB 解析意图 → 调 ERP 查商品 + 客户最近成交价 → 通过**文本格式化卡片**回显（含模糊匹配多命中"回复编号选择"、0 命中友好提示、ERP 5xx 自动重试 + 熔断）。规则解析优先 + AI fallback（DeepSeek 默认 / Qwen 备选）+ 低置信度强制人工确认。

**消息形态说明：** Plan 4 全部用钉钉文本消息（`OutboundMessageType.TEXT`）做格式化展示——多行文本 + 编号列表，用户回复纯数字编号实现"选择"。**不**实现钉钉 ActionCard 富按钮交互（钉钉 oToMessages/batchSend 的 sampleActionCard 形态）。如未来需要按钮交互，由 Plan 5 或单独 spec 接入；DingTalkSender 已经有 `send_action_card` 能力，cards.py 也预留了 `OutboundMessageType.ACTIONCARD` 分支。

**Architecture:** `IntentParser` 实现链 `RuleParser → LLMParser`（前者命中即返回，未命中或低置信度才走 AI）+ `MatchResolver` 统一处理模糊匹配（命中多个 → 文本编号列表 / 0 命中 → 友好提示 / 唯一 → 直接用）+ `DefaultPricingStrategy`（客户最近成交价 → 系统零售价 fallback）+ `QueryProductUseCase` 业务编排 + `DeepSeekProvider` / `QwenProvider`（CapabilityProvider 实现）+ `ConversationStateRepository`（多轮选编号需保留待选项上下文）。错误分层 + 重试编排 + ERP 熔断器全部启用。

**Tech Stack:** httpx（调 DeepSeek/Qwen OpenAI 兼容 API）+ Plan 2 端口/策略接口 + Plan 3 Erp4Adapter / DingTalkSender 已就绪。

**前置阅读：**
- [HUB Spec §5 核心抽象接口](../specs/2026-04-27-hub-middleware-design.md#5-核心抽象接口端口策略)
- [HUB Spec §9 错误码体系（P1-9）](../specs/2026-04-27-hub-middleware-design.md#19-待确认事项p1p2用户-review-阶段决定) §19.1 错误码 20 条
- [HUB Spec §13.2 重试策略](../specs/2026-04-27-hub-middleware-design.md#132-重试策略)
- [HUB Spec §13.4 ERP 故障降级](../specs/2026-04-27-hub-middleware-design.md#134-erp-故障降级)
- [Plan 1 ERP 4.3 历史成交价接口](2026-04-27-erp-integration-changes.md)（必须已实施）
- [Plan 2 端口接口定义](2026-04-27-hub-skeleton.md)（IntentParser / PricingStrategy / CapabilityProvider）
- [Plan 3 Erp4Adapter / 入站 handler / IdentityService](2026-04-27-hub-dingtalk-binding.md)

**前置依赖：**
- ✅ Plan 2 完成（端口接口 + 任务队列 + 鉴权 + 加密）
- ✅ Plan 3 完成（钉钉接入 + 绑定 + IdentityService + DingTalkSender + Erp4Adapter）
- ✅ Plan 1 完成（ERP 历史成交价接口启用 + ApiKey scope=act_as_user 已生效）

**估时：** 4-5 天

---

## 文件结构

### 新增文件

| 文件 | 职责 |
|---|---|
| `backend/hub/intent/__init__.py` | 意图解析入口（暴露 ChainParser + ParsedIntent） |
| `backend/hub/intent/rule_parser.py` | RuleParser（正则命中常见命令模式） |
| `backend/hub/intent/llm_parser.py` | LLMParser（schema-guided 调 AICapability） |
| `backend/hub/intent/chain_parser.py` | ChainParser（rule → llm，低置信度返回 confidence < 阈值） |
| `backend/hub/match/__init__.py` | 模糊匹配入口 |
| `backend/hub/match/resolver.py` | MatchResolver（unique / multi / none 统一处理） |
| `backend/hub/match/conversation_state.py` | ConversationStateRepository（多轮选编号上下文，Redis 存） |
| `backend/hub/strategy/__init__.py` | 业务策略入口 |
| `backend/hub/strategy/pricing.py` | DefaultPricingStrategy（历史价 → 系统价 fallback） |
| `backend/hub/capabilities/__init__.py` | 能力入口 |
| `backend/hub/capabilities/deepseek.py` | DeepSeekProvider（OpenAI 兼容 API） |
| `backend/hub/capabilities/qwen.py` | QwenProvider（dashscope OpenAI 兼容 API） |
| `backend/hub/capabilities/factory.py` | 按 ai_provider 表配置加载具体 Provider |
| `backend/hub/usecases/__init__.py` | 业务用例入口 |
| `backend/hub/usecases/query_product.py` | QueryProductUseCase（无客户场景） |
| `backend/hub/usecases/query_customer_history.py` | QueryCustomerHistoryUseCase（带客户场景） |
| `backend/hub/circuit_breaker/__init__.py` | 熔断器 |
| `backend/hub/circuit_breaker/erp_breaker.py` | ERP 调用熔断器（5/30s 触发，60s 半开） |
| `backend/hub/error_codes.py` | 错误码定义（20 条 spec 初始码表，含中文文案） |
| `backend/hub/cards.py` | 文本卡片模板（多命中编号选择 / 商品价格回显 / 客户历史价回显，全部 TEXT 形态） |
| `backend/hub/permissions.py` | HUB 权限校验（hub_user 是否拥有 usecase.* / downstream.erp.use 等） |

### 修改文件

| 文件 | 修改 |
|---|---|
| `backend/hub/handlers/dingtalk_inbound.py` | 在"已绑定 + ERP 启用"路径后接 ChainParser + UseCase 路由（替换 Plan 3 占位提示） |
| `backend/worker.py` | 构造 ChainParser / MatchResolver / PricingStrategy / UseCase 并注入到 inbound handler |
| `backend/hub/adapters/downstream/erp4.py` | 加 ERP 熔断器装饰；超时降级（历史价 3s 超时返回空） |

### 测试

| 文件 | 数量 | 职责 |
|---|---|---|
| `backend/tests/test_rule_parser.py` | 6 | 规则命中 / 不命中 / confidence |
| `backend/tests/test_llm_parser.py` | 9 | schema-guided 调用 + 解析 + 异常降级 + 缺必填字段降级（3 个）+ confidence 非数字降级 |
| `backend/tests/test_chain_parser.py` | 4 | rule 命中跳过 LLM / rule miss 走 LLM / 低置信度 / LLM 失败降级 |
| `backend/tests/test_match_resolver.py` | 6 | unique / multi / none / 编号回选 |
| `backend/tests/test_conversation_state.py` | 4 | 写入/读取/过期/清理 |
| `backend/tests/test_pricing_strategy.py` | 7 | 客户历史价 / fallback_retail_price / get_product / 历史空 / 历史 403 上抛 / 系统错降级 / Decimal |
| `backend/tests/test_deepseek_provider.py` | 4 | 真实接口形态（mock httpx） |
| `backend/tests/test_qwen_provider.py` | 3 | dashscope 兼容 API |
| `backend/tests/test_query_product_usecase.py` | 8 | 唯一 / 多命中 / 0 命中 / 权限不足 / ERP 5xx / 熔断 / **execute_selected 渲染（含库存）** / **fallback_retail_price 透传** |
| `backend/tests/test_query_customer_history_usecase.py` | 8 | 客户唯一/多命中、商品多命中、空历史、ERP 5xx、客户 403、**历史价 403 翻译为 PERM**、确认编号回路 |
| `backend/tests/test_erp_breaker.py` | 6 | closed/open 切换、半开探测、降级返回、countable_exceptions 仅系统错 |
| `backend/tests/test_inbound_handler_with_intent.py` | 5 | rule 命中走 usecase / LLM 走 usecase / 低置信度走确认卡 / select_choice 调 execute_selected / 权限不足提示 |
| `backend/tests/test_error_codes.py` | 3 | 错误码 → 中文文案 / 不暴露 code |
| `backend/tests/test_permissions.py` | 4 | usecase / downstream / channel 权限聚合 |
| `backend/tests/test_erp4_adapter.py`（追加） | 4 | Plan 4 追加：keyword 搜索参数（products/customers）+ 熔断 + 历史价超时 |
| **合计** | **81** | |

---

## Task 1：错误码定义 + 文本卡片模板

**Files:**
- Create: `backend/hub/error_codes.py`
- Create: `backend/hub/cards.py`
- Test: `backend/tests/test_error_codes.py`

错误码遵循 spec §19.1（20 条初始码表），UI 文案严格中文大白话不暴露 code。

**卡片形态：** 本 plan 全部走 `OutboundMessageType.TEXT`（多行文本 + 编号列表），不用钉钉 ActionCard 富按钮——见 Goal 节"消息形态说明"。函数仍命名 `xxx_card`（语义性命名，"卡片"=格式化的多行文本）。

- [ ] **Step 1: 写错误码测试**

文件 `backend/tests/test_error_codes.py`：
```python
import pytest


def test_known_codes_have_zh_messages():
    from hub.error_codes import ERROR_MESSAGES, BizErrorCode
    must_have = [
        BizErrorCode.BIND_USER_NOT_FOUND, BizErrorCode.BIND_CODE_INVALID,
        BizErrorCode.USER_NOT_BOUND, BizErrorCode.USER_ERP_DISABLED,
        BizErrorCode.PERM_NO_PRODUCT_QUERY, BizErrorCode.PERM_DOWNSTREAM_DENIED,
        BizErrorCode.MATCH_NOT_FOUND, BizErrorCode.MATCH_AMBIGUOUS,
        BizErrorCode.INTENT_LOW_CONFIDENCE, BizErrorCode.ERP_TIMEOUT,
        BizErrorCode.ERP_CIRCUIT_OPEN, BizErrorCode.INTERNAL_ERROR,
    ]
    for code in must_have:
        assert code in ERROR_MESSAGES
        assert ERROR_MESSAGES[code]
        # 中文：不能含纯英文 code
        assert code.value not in ERROR_MESSAGES[code]


def test_user_friendly_message_supports_template():
    from hub.error_codes import build_user_message, BizErrorCode
    msg = build_user_message(
        BizErrorCode.MATCH_NOT_FOUND, keyword="阿里", resource="客户",
    )
    assert "阿里" in msg
    assert "客户" in msg
    assert "MATCH_NOT_FOUND" not in msg


def test_unknown_code_falls_back_internal_error():
    from hub.error_codes import build_user_message
    msg = build_user_message("BOGUS_CODE_NOT_DEFINED")
    assert "出错" in msg or "异常" in msg
```

- [ ] **Step 2: 实现 error_codes.py**

文件 `backend/hub/error_codes.py`：
```python
"""HUB 业务错误码定义（spec §19.1 初始码表 20 条）。

UI 大白话原则：每条 code 对应中文文案；最终回复钉钉用户的内容只包含中文文案，
不暴露 code 字符串。
"""
from __future__ import annotations
from enum import Enum
from string import Template


class BizErrorCode(str, Enum):
    BIND_USER_NOT_FOUND = "BIND_USER_NOT_FOUND"
    BIND_CODE_INVALID = "BIND_CODE_INVALID"
    BIND_CODE_EXPIRED = "BIND_CODE_EXPIRED"
    BIND_ALREADY_BOUND = "BIND_ALREADY_BOUND"
    BIND_MISMATCH = "BIND_MISMATCH"
    UNBIND_NOT_OWNER = "UNBIND_NOT_OWNER"
    USER_NOT_BOUND = "USER_NOT_BOUND"
    USER_ERP_DISABLED = "USER_ERP_DISABLED"
    PERM_NO_PRODUCT_QUERY = "PERM_NO_PRODUCT_QUERY"
    PERM_NO_CUSTOMER_HISTORY = "PERM_NO_CUSTOMER_HISTORY"
    PERM_DOWNSTREAM_DENIED = "PERM_DOWNSTREAM_DENIED"
    MATCH_NOT_FOUND = "MATCH_NOT_FOUND"
    MATCH_AMBIGUOUS = "MATCH_AMBIGUOUS"
    INTENT_LOW_CONFIDENCE = "INTENT_LOW_CONFIDENCE"
    ERP_TIMEOUT = "ERP_TIMEOUT"
    ERP_CIRCUIT_OPEN = "ERP_CIRCUIT_OPEN"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    CONTENT_TOO_LONG = "CONTENT_TOO_LONG"
    SETUP_TOKEN_INVALID = "SETUP_TOKEN_INVALID"


# UI 中文文案（带模板变量）
ERROR_MESSAGES: dict[BizErrorCode, str] = {
    BizErrorCode.BIND_USER_NOT_FOUND: "未找到 ERP 用户「$username」，请检查用户名",
    BizErrorCode.BIND_CODE_INVALID: "绑定码错误，请检查后重新输入",
    BizErrorCode.BIND_CODE_EXPIRED: "绑定码已过期（5 分钟有效），请重新发起绑定",
    BizErrorCode.BIND_ALREADY_BOUND: "该钉钉账号已经绑定到 ERP 用户「$name」，如需换绑请先解绑",
    BizErrorCode.BIND_MISMATCH: "绑定码与你输入的 ERP 用户不匹配",
    BizErrorCode.UNBIND_NOT_OWNER: "你不能解绑别人的账号",
    BizErrorCode.USER_NOT_BOUND: "你还没绑定 ERP 账号，请先发送 /绑定 你的ERP用户名",
    BizErrorCode.USER_ERP_DISABLED: "你的 ERP 账号已停用，请联系管理员",
    BizErrorCode.PERM_NO_PRODUCT_QUERY: "你没有「商品查询」功能的使用权限，请联系管理员开通",
    BizErrorCode.PERM_NO_CUSTOMER_HISTORY: "你没有「客户历史价查询」功能的使用权限",
    BizErrorCode.PERM_DOWNSTREAM_DENIED: "后台校验未通过：你在 ERP 没有访问该数据的权限",
    BizErrorCode.MATCH_NOT_FOUND: "未找到符合「$keyword」的$resource，请检查输入",
    BizErrorCode.MATCH_AMBIGUOUS: "找到多个匹配，请回复编号选择",
    BizErrorCode.INTENT_LOW_CONFIDENCE: "我不太确定你想做什么，请用更明确的方式描述",
    BizErrorCode.ERP_TIMEOUT: "系统繁忙，请稍后重试（已自动记录）",
    BizErrorCode.ERP_CIRCUIT_OPEN: "系统暂时不可用，请稍后重试",
    BizErrorCode.INTERNAL_ERROR: "系统出错了，已通知管理员",
    BizErrorCode.RATE_LIMITED: "操作太频繁，请稍后再试",
    BizErrorCode.CONTENT_TOO_LONG: "你的消息太长了，请精简后重新发送",
    BizErrorCode.SETUP_TOKEN_INVALID: "初始化 Token 错误或已过期",
}


def build_user_message(code, **context) -> str:
    """根据错误码 + 上下文变量构造给用户的中文文案。"""
    if isinstance(code, str):
        try:
            code = BizErrorCode(code)
        except ValueError:
            return ERROR_MESSAGES[BizErrorCode.INTERNAL_ERROR]
    template = ERROR_MESSAGES.get(code, ERROR_MESSAGES[BizErrorCode.INTERNAL_ERROR])
    try:
        return Template(template).safe_substitute(**context)
    except Exception:
        return template


class BizError(Exception):
    """业务异常：携带错误码 + 模板上下文，给上游决定是否翻译给用户。"""
    def __init__(self, code: BizErrorCode, **context):
        self.code = code
        self.context = context
        super().__init__(build_user_message(code, **context))
```

- [ ] **Step 3: 实现 cards.py（文本卡片模板）**

文件 `backend/hub/cards.py`：
```python
"""钉钉文本卡片模板（OutboundMessageType.TEXT 多行格式化文本）。"""
from __future__ import annotations
from hub.ports import OutboundMessage, OutboundMessageType


def multi_match_select_card(
    keyword: str, resource: str, items: list[dict],
) -> OutboundMessage:
    """模糊匹配多命中 → 让用户回复编号选择。

    items: [{"label": "阿里巴巴集团", "subtitle": "客户编号 12345", "ref": <内部 ref>}, ...]
    """
    lines = [f"找到多个匹配「{keyword}」的{resource}，请回复编号选择："]
    for i, it in enumerate(items, start=1):
        sub = f"（{it['subtitle']}）" if it.get("subtitle") else ""
        lines.append(f"{i}. {it['label']}{sub}")
    lines.append("\n（输入编号，例如：1）")
    return OutboundMessage(type=OutboundMessageType.TEXT, text="\n".join(lines))


def product_simple_card(product: dict, retail_price: str) -> OutboundMessage:
    """无客户场景：商品基本信息 + 系统零售价。"""
    text = (
        f"📦 {product['name']}\n"
        f"SKU：{product.get('sku', '-')}\n"
        f"系统零售价：¥{retail_price}\n"
    )
    if product.get("stock") is not None:
        text += f"当前库存：{product['stock']}\n"
    return OutboundMessage(type=OutboundMessageType.TEXT, text=text)


def product_with_customer_history_card(
    product: dict, customer: dict, history: list[dict], retail_price: str,
) -> OutboundMessage:
    """带客户场景：商品 + 客户最近 N 次成交价。"""
    lines = [
        f"📦 {product['name']}（SKU {product.get('sku', '-')}）",
        f"🏢 客户：{customer['name']}",
        f"系统零售价：¥{retail_price}",
        "",
    ]
    if not history:
        lines.append("该客户暂无该商品的历史成交价")
    else:
        lines.append(f"最近 {len(history)} 次成交价：")
        for rec in history:
            lines.append(
                f"• ¥{rec['unit_price']} · {rec.get('order_date', '')[:10]} "
                f"· 单号 {rec.get('order_no', '-')}"
            )
    return OutboundMessage(type=OutboundMessageType.TEXT, text="\n".join(lines))


def low_confidence_confirm_card(parsed_summary: str) -> OutboundMessage:
    """AI 解析低置信度 → 让用户确认或重新表达。"""
    return OutboundMessage(
        type=OutboundMessageType.TEXT,
        text=(
            f"我大概理解为：{parsed_summary}\n\n"
            "如果是这个意思请回复「是」继续，否则请用更明确的方式重新描述。"
        ),
    )
```

- [ ] **Step 4: 跑测试 + 提交**

```bash
cd /Users/lin/Desktop/hub/backend
pytest tests/test_error_codes.py -v
git add backend/hub/error_codes.py backend/hub/cards.py backend/tests/test_error_codes.py
git commit -m "feat(hub): 错误码体系（20 条初始码表，中文大白话）+ 文本卡片模板"
```

---

## Task 2：HUB 权限校验（usecase / downstream / channel）

**Files:**
- Create: `backend/hub/permissions.py`
- Test: `backend/tests/test_permissions.py`

入站消息进入业务用例前必须聚合校验：用户拥有 `channel.dingtalk.use` + `usecase.X.use` + `downstream.erp.use`。

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_permissions.py`：
```python
import pytest


@pytest.mark.asyncio
async def test_user_with_all_perms_passes():
    from hub.permissions import has_permission, require_permissions
    from hub.models import HubUser, HubRole, HubUserRole
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="A")
    role = await HubRole.get(code="bot_user_basic")
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    assert await has_permission(user.id, "channel.dingtalk.use") is True
    assert await has_permission(user.id, "usecase.query_product.use") is True
    assert await has_permission(user.id, "downstream.erp.use") is True

    # 多权限聚合
    await require_permissions(user.id, [
        "channel.dingtalk.use",
        "usecase.query_product.use",
        "downstream.erp.use",
    ])  # 不抛


@pytest.mark.asyncio
async def test_missing_permission_raises():
    from hub.permissions import require_permissions
    from hub.error_codes import BizError, BizErrorCode
    from hub.models import HubUser

    user = await HubUser.create(display_name="B")  # 没绑任何角色

    with pytest.raises(BizError) as exc:
        await require_permissions(user.id, ["usecase.query_product.use"])
    assert exc.value.code == BizErrorCode.PERM_NO_PRODUCT_QUERY


@pytest.mark.asyncio
async def test_admin_role_has_all():
    from hub.permissions import has_permission
    from hub.models import HubUser, HubRole, HubUserRole
    from hub.seed import run_seed
    await run_seed()

    user = await HubUser.create(display_name="Adm")
    role = await HubRole.get(code="platform_admin")
    await HubUserRole.create(hub_user_id=user.id, role_id=role.id)

    # platform_admin 拥有所有 usecase / downstream / platform 权限
    for code in ["usecase.query_product.use", "downstream.erp.use",
                 "platform.tasks.read", "channel.dingtalk.use"]:
        assert await has_permission(user.id, code) is True


@pytest.mark.asyncio
async def test_permission_to_error_code_mapping():
    """缺权限按 code 映射到具体 BizErrorCode。"""
    from hub.permissions import _permission_to_error_code, BIZ_DEFAULT
    from hub.error_codes import BizErrorCode

    assert _permission_to_error_code("usecase.query_product.use") == BizErrorCode.PERM_NO_PRODUCT_QUERY
    assert _permission_to_error_code("usecase.query_customer_history.use") == BizErrorCode.PERM_NO_CUSTOMER_HISTORY
    assert _permission_to_error_code("downstream.erp.use") == BizErrorCode.PERM_DOWNSTREAM_DENIED
    assert _permission_to_error_code("unknown.x.y") == BIZ_DEFAULT
```

- [ ] **Step 2: 实现 permissions.py**

文件 `backend/hub/permissions.py`：
```python
"""HUB 权限校验：hub_user → 拥有的权限码集合（聚合所有 role 的 permissions）。"""
from __future__ import annotations
from functools import lru_cache
from hub.models import HubUserRole, HubRole
from hub.error_codes import BizError, BizErrorCode


# 权限 code → BizErrorCode 映射（用于把权限不足翻译成具体中文文案）
_PERM_TO_BIZ = {
    "usecase.query_product.use": BizErrorCode.PERM_NO_PRODUCT_QUERY,
    "usecase.query_customer_history.use": BizErrorCode.PERM_NO_CUSTOMER_HISTORY,
    "downstream.erp.use": BizErrorCode.PERM_DOWNSTREAM_DENIED,
}
BIZ_DEFAULT = BizErrorCode.PERM_DOWNSTREAM_DENIED


def _permission_to_error_code(perm_code: str) -> BizErrorCode:
    return _PERM_TO_BIZ.get(perm_code, BIZ_DEFAULT)


async def get_user_permissions(hub_user_id: int) -> set[str]:
    """返回 hub_user 通过所有 role 聚合的所有权限 code 集合。"""
    user_roles = await HubUserRole.filter(
        hub_user_id=hub_user_id,
    ).select_related("role")
    perms = set()
    for ur in user_roles:
        role = await HubRole.get(id=ur.role_id).prefetch_related("permissions")
        async for p in role.permissions:
            perms.add(p.code)
    return perms


async def has_permission(hub_user_id: int, perm_code: str) -> bool:
    perms = await get_user_permissions(hub_user_id)
    return perm_code in perms


async def require_permissions(hub_user_id: int, perm_codes: list[str]) -> None:
    """所有 perm_codes 必须都拥有，否则抛 BizError（按缺失的第一个 code 决定文案）。"""
    perms = await get_user_permissions(hub_user_id)
    for code in perm_codes:
        if code not in perms:
            raise BizError(_permission_to_error_code(code))
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
pytest tests/test_permissions.py -v
git add backend/hub/permissions.py backend/tests/test_permissions.py
git commit -m "feat(hub): HUB 权限校验（usecase / downstream / channel 聚合 + 错误码映射）"
```

---

## Task 3：RuleParser（正则命中常见命令）

**Files:**
- Create: `backend/hub/intent/__init__.py`
- Create: `backend/hub/intent/rule_parser.py`
- Test: `backend/tests/test_rule_parser.py`

C 阶段支持的两种意图：
- `query_product`：查 SKU100 / 查 SKU100 多少钱
- `query_customer_history`：查 SKU100 给阿里 / 查 SKU100 给阿里报价

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_rule_parser.py`：
```python
import pytest


@pytest.mark.asyncio
async def test_query_product_simple():
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("查 SKU100", context={})
    assert intent.intent_type == "query_product"
    assert intent.fields["sku_or_keyword"] == "SKU100"
    assert intent.fields.get("customer_keyword") is None
    assert intent.confidence >= 0.9
    assert intent.parser == "rule"


@pytest.mark.asyncio
async def test_query_product_with_price_word():
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("查 SKU100 多少钱", context={})
    assert intent.intent_type == "query_product"


@pytest.mark.asyncio
async def test_query_customer_history():
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("查 SKU100 给阿里", context={})
    assert intent.intent_type == "query_customer_history"
    assert intent.fields["sku_or_keyword"] == "SKU100"
    assert intent.fields["customer_keyword"] == "阿里"


@pytest.mark.asyncio
async def test_select_number_in_pending_choice_context():
    """有待选编号上下文时，纯数字输入被识别为编号选择。"""
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("2", context={"pending_choice": "yes"})
    assert intent.intent_type == "select_choice"
    assert intent.fields["choice"] == 2


@pytest.mark.asyncio
async def test_no_match_returns_none_intent():
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("今天天气怎么样", context={})
    assert intent.intent_type == "unknown"
    assert intent.confidence == 0.0


@pytest.mark.asyncio
async def test_confirm_yes_in_pending_confirm_context():
    """低置信度后用户回复"是"被识别为确认。"""
    from hub.intent.rule_parser import RuleParser
    p = RuleParser()
    intent = await p.parse("是", context={"pending_confirm": "yes"})
    assert intent.intent_type == "confirm_yes"
```

- [ ] **Step 2: 实现 RuleParser**

文件 `backend/hub/intent/__init__.py`：
```python
"""HUB 意图解析（rule + LLM 链）。"""
```

文件 `backend/hub/intent/rule_parser.py`：
```python
"""RuleParser：正则匹配常见命令模式。

命中模式：
- 查 <SKU或商品关键字> [给 <客户关键字>] [报价/价格/多少钱]?
- 纯数字（仅在 context["pending_choice"] 时识别为编号选择）
- "是" / "确认" / "yes"（仅在 context["pending_confirm"] 时识别为确认）

不命中 → intent_type="unknown" + confidence=0
"""
from __future__ import annotations
import re
from hub.ports import ParsedIntent


# 查 <sku> [给 <customer>] [价格词]
RE_QUERY = re.compile(
    r"^/?查\s*(?P<sku>\S+?)(?:\s*给\s*(?P<customer>\S+?))?\s*(?:报价|价格|多少钱|几钱)?\s*$"
)
RE_NUMBER = re.compile(r"^\s*(\d{1,3})\s*$")
RE_CONFIRM = re.compile(r"^\s*(是|确认|yes|y)\s*$", re.IGNORECASE)


class RuleParser:
    parser_name = "rule"

    async def parse(self, text: str, context: dict) -> ParsedIntent:
        # 编号选择
        if context.get("pending_choice"):
            m = RE_NUMBER.match(text)
            if m:
                return ParsedIntent(
                    intent_type="select_choice",
                    fields={"choice": int(m.group(1))},
                    confidence=0.95, parser=self.parser_name,
                )

        # 低置信度后的确认
        if context.get("pending_confirm"):
            if RE_CONFIRM.match(text):
                return ParsedIntent(
                    intent_type="confirm_yes",
                    fields={}, confidence=0.95, parser=self.parser_name,
                )

        m = RE_QUERY.match(text)
        if m:
            sku = m.group("sku")
            customer = m.group("customer")
            if customer:
                return ParsedIntent(
                    intent_type="query_customer_history",
                    fields={"sku_or_keyword": sku, "customer_keyword": customer},
                    confidence=0.95, parser=self.parser_name,
                )
            return ParsedIntent(
                intent_type="query_product",
                fields={"sku_or_keyword": sku, "customer_keyword": None},
                confidence=0.95, parser=self.parser_name,
            )

        return ParsedIntent(
            intent_type="unknown", fields={}, confidence=0.0, parser=self.parser_name,
        )
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
pytest tests/test_rule_parser.py -v
git add backend/hub/intent/__init__.py backend/hub/intent/rule_parser.py \
        backend/tests/test_rule_parser.py
git commit -m "feat(hub): RuleParser（查商品/查客户历史价/编号选择/确认 正则）"
```

---

## Task 4：DeepSeek + Qwen Provider（CapabilityProvider 实现）

**Files:**
- Create: `backend/hub/capabilities/__init__.py`
- Create: `backend/hub/capabilities/deepseek.py`
- Create: `backend/hub/capabilities/qwen.py`
- Create: `backend/hub/capabilities/factory.py`
- Test: `backend/tests/test_deepseek_provider.py`
- Test: `backend/tests/test_qwen_provider.py`

DeepSeek / Qwen 都用 OpenAI 兼容 API（/v1/chat/completions）。Provider 实现 `AICapability` Protocol。

- [ ] **Step 1: 写 DeepSeek 测试**

文件 `backend/tests/test_deepseek_provider.py`：
```python
import json
import pytest
import httpx
from httpx import MockTransport, Response


@pytest.mark.asyncio
async def test_chat_calls_openai_compatible_endpoint():
    from hub.capabilities.deepseek import DeepSeekProvider

    captured = {}
    def handler(req: httpx.Request) -> Response:
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content)
        captured["auth"] = req.headers.get("authorization")
        return Response(200, json={
            "choices": [{"message": {"content": "回答"}}],
        })

    p = DeepSeekProvider(
        api_key="sk-test", base_url="https://api.deepseek.com/v1",
        model="deepseek-chat", transport=MockTransport(handler),
    )
    out = await p.chat(messages=[{"role": "user", "content": "hi"}])
    assert out == "回答"
    assert "chat/completions" in captured["url"]
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_parse_intent_returns_dict():
    from hub.capabilities.deepseek import DeepSeekProvider
    parsed_json = '{"intent_type":"query_product","fields":{"sku_or_keyword":"SKU100"},"confidence":0.85}'
    def handler(req): return Response(200, json={
        "choices": [{"message": {"content": parsed_json}}],
    })
    p = DeepSeekProvider(api_key="k", base_url="x", model="m",
                         transport=MockTransport(handler))
    schema = {"intent_type": "string", "fields": "object", "confidence": "float"}
    out = await p.parse_intent("查 SKU100", schema)
    assert out["intent_type"] == "query_product"
    assert out["confidence"] == 0.85


@pytest.mark.asyncio
async def test_parse_intent_handles_invalid_json():
    """LLM 返回非 JSON → 抛 LLMParseError。"""
    from hub.capabilities.deepseek import DeepSeekProvider, LLMParseError
    def handler(req): return Response(200, json={
        "choices": [{"message": {"content": "Sorry, I can't parse"}}],
    })
    p = DeepSeekProvider(api_key="k", base_url="x", model="m",
                         transport=MockTransport(handler))
    with pytest.raises(LLMParseError):
        await p.parse_intent("xxx", {})


@pytest.mark.asyncio
async def test_5xx_raises():
    from hub.capabilities.deepseek import DeepSeekProvider, LLMServiceError
    def handler(req): return Response(503)
    p = DeepSeekProvider(api_key="k", base_url="x", model="m",
                         transport=MockTransport(handler))
    with pytest.raises(LLMServiceError):
        await p.chat(messages=[{"role": "user", "content": "hi"}])
```

- [ ] **Step 2: 写 Qwen 测试**

文件 `backend/tests/test_qwen_provider.py`：
```python
import pytest
import httpx
from httpx import MockTransport, Response


@pytest.mark.asyncio
async def test_qwen_uses_dashscope_compatible_endpoint():
    from hub.capabilities.qwen import QwenProvider

    captured = {}
    def handler(req: httpx.Request) -> Response:
        captured["url"] = str(req.url)
        captured["body"] = req.content
        return Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
        })

    p = QwenProvider(
        api_key="sk-q", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus", transport=MockTransport(handler),
    )
    out = await p.chat(messages=[{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert "dashscope" in captured["url"]


@pytest.mark.asyncio
async def test_qwen_capability_type_correct():
    from hub.capabilities.qwen import QwenProvider
    p = QwenProvider(api_key="x", base_url="x", model="x")
    assert p.capability_type == "ai"
    assert p.provider_name == "qwen"


@pytest.mark.asyncio
async def test_qwen_parse_intent_returns_dict():
    from hub.capabilities.qwen import QwenProvider
    def handler(req): return Response(200, json={
        "choices": [{"message": {"content": '{"intent_type":"x","fields":{},"confidence":0.5}'}}],
    })
    p = QwenProvider(api_key="k", base_url="x", model="m",
                     transport=MockTransport(handler))
    out = await p.parse_intent("test", {})
    assert out["intent_type"] == "x"
```

- [ ] **Step 3: 实现 capabilities/__init__.py 和 deepseek.py**

文件 `backend/hub/capabilities/__init__.py`：
```python
"""HUB Capability 实现（AI / OCR 等）。"""
```

文件 `backend/hub/capabilities/deepseek.py`：
```python
"""DeepSeekProvider：OpenAI 兼容 API。"""
from __future__ import annotations
import json
import httpx


class LLMServiceError(Exception):
    """LLM 服务侧异常（5xx / 网络错误）。"""


class LLMParseError(Exception):
    """LLM 返回内容无法解析为期望 schema。"""


class _OpenAICompatibleProvider:
    """OpenAI 兼容 chat completions 客户端基类。"""

    capability_type = "ai"
    provider_name = "base"

    def __init__(
        self, api_key: str, base_url: str, model: str,
        *, timeout: float = 30.0, transport: httpx.BaseTransport | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def aclose(self):
        await self._client.aclose()

    async def chat(self, messages: list[dict], **kwargs) -> str:
        url = f"{self.base_url}/chat/completions"
        body = {"model": self.model, "messages": messages, **kwargs}
        try:
            r = await self._client.post(
                url, json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.RequestError as e:
            raise LLMServiceError(f"网络错误: {e}")
        if r.status_code >= 500:
            raise LLMServiceError(f"{self.provider_name} {r.status_code}")
        if r.status_code >= 400:
            raise LLMServiceError(f"{self.provider_name} {r.status_code}: {r.text[:200]}")
        body = r.json()
        return body["choices"][0]["message"]["content"]

    async def parse_intent(self, text: str, schema: dict) -> dict:
        """schema-guided 意图解析：要求 LLM 返回纯 JSON。"""
        sys_msg = (
            "你是一个意图解析器。把用户输入解析成符合给定 schema 的 JSON 对象。"
            "**只返回 JSON，不要包含任何解释或 markdown 标记。**"
            f"\nSchema 字段：{json.dumps(schema, ensure_ascii=False)}"
            "\n如果无法可靠解析，返回 {\"intent_type\":\"unknown\",\"fields\":{},\"confidence\":0.0}。"
        )
        content = await self.chat(messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": text},
        ])
        # 容错：剥离可能的 ```json 代码块标记
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LLMParseError(f"LLM 返回非 JSON: {content[:200]}") from e


class DeepSeekProvider(_OpenAICompatibleProvider):
    provider_name = "deepseek"
```

- [ ] **Step 4: 实现 qwen.py**

文件 `backend/hub/capabilities/qwen.py`：
```python
"""QwenProvider：通义千问 dashscope OpenAI 兼容模式。"""
from __future__ import annotations
from hub.capabilities.deepseek import _OpenAICompatibleProvider


class QwenProvider(_OpenAICompatibleProvider):
    provider_name = "qwen"
```

- [ ] **Step 5: 实现 factory.py**

文件 `backend/hub/capabilities/factory.py`：
```python
"""按 ai_provider 表的 provider_type 构造对应 Provider 实例。"""
from __future__ import annotations
from hub.crypto import decrypt_secret
from hub.models import AIProvider
from hub.capabilities.deepseek import DeepSeekProvider
from hub.capabilities.qwen import QwenProvider


_PROVIDERS = {
    "deepseek": DeepSeekProvider,
    "qwen": QwenProvider,
}


async def load_active_ai_provider():
    """从 ai_provider 表查 status=active 的第一条配置，构造 Provider 实例。"""
    record = await AIProvider.filter(status="active").first()
    if record is None:
        return None
    cls = _PROVIDERS.get(record.provider_type)
    if cls is None:
        raise ValueError(f"未知 AI provider_type: {record.provider_type}")
    api_key = decrypt_secret(record.encrypted_api_key, purpose="config_secrets")
    return cls(api_key=api_key, base_url=record.base_url, model=record.model)
```

- [ ] **Step 6: 跑测试 + 提交**

```bash
pytest tests/test_deepseek_provider.py tests/test_qwen_provider.py -v
git add backend/hub/capabilities/ \
        backend/tests/test_deepseek_provider.py \
        backend/tests/test_qwen_provider.py
git commit -m "feat(hub): DeepSeek + Qwen Provider（OpenAI 兼容 API）+ factory"
```

---

## Task 5：LLMParser + ChainParser

**Files:**
- Create: `backend/hub/intent/llm_parser.py`
- Create: `backend/hub/intent/chain_parser.py`
- Test: `backend/tests/test_llm_parser.py`
- Test: `backend/tests/test_chain_parser.py`

ChainParser 是入站 handler 直接用的入口：先 RuleParser，未命中或低置信度才走 LLMParser；LLMParser 故障也降级回 unknown 不抛错。

- [ ] **Step 1: 写 LLMParser 测试**

文件 `backend/tests/test_llm_parser.py`：
```python
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_llm_parser_calls_capability():
    from hub.intent.llm_parser import LLMParser

    cap = AsyncMock()
    cap.parse_intent = AsyncMock(return_value={
        "intent_type": "query_product",
        "fields": {"sku_or_keyword": "SKU100", "customer_keyword": "阿里"},
        "confidence": 0.85,
    })

    p = LLMParser(ai=cap)
    intent = await p.parse("帮我看下阿里那个 SKU100 多少钱", context={})
    assert intent.intent_type == "query_product"
    assert intent.fields["sku_or_keyword"] == "SKU100"
    assert intent.confidence == 0.85
    assert intent.parser == "llm"


@pytest.mark.asyncio
async def test_llm_parser_returns_unknown_on_service_error():
    """LLM 服务异常 → 降级 unknown，不向上抛。"""
    from hub.intent.llm_parser import LLMParser
    from hub.capabilities.deepseek import LLMServiceError

    cap = AsyncMock()
    cap.parse_intent = AsyncMock(side_effect=LLMServiceError("503"))
    p = LLMParser(ai=cap)
    intent = await p.parse("xxx", context={})
    assert intent.intent_type == "unknown"
    assert intent.confidence == 0.0


@pytest.mark.asyncio
async def test_llm_parser_returns_unknown_on_parse_error():
    from hub.intent.llm_parser import LLMParser
    from hub.capabilities.deepseek import LLMParseError

    cap = AsyncMock()
    cap.parse_intent = AsyncMock(side_effect=LLMParseError("not json"))
    p = LLMParser(ai=cap)
    intent = await p.parse("xxx", context={})
    assert intent.intent_type == "unknown"


@pytest.mark.asyncio
async def test_llm_parser_clamps_invalid_confidence():
    """LLM 返回 confidence > 1 或 < 0 时 clamp。"""
    from hub.intent.llm_parser import LLMParser
    cap = AsyncMock()
    cap.parse_intent = AsyncMock(return_value={
        "intent_type": "query_product", "fields": {}, "confidence": 1.5,
    })
    p = LLMParser(ai=cap)
    intent = await p.parse("x", context={})
    assert 0.0 <= intent.confidence <= 1.0


@pytest.mark.asyncio
async def test_llm_parser_no_capability_returns_unknown():
    """ai=None（未配置 AI 提供商）时直接返回 unknown，不报错。"""
    from hub.intent.llm_parser import LLMParser
    p = LLMParser(ai=None)
    intent = await p.parse("x", context={})
    assert intent.intent_type == "unknown"


@pytest.mark.asyncio
async def test_llm_parser_missing_required_fields_falls_back_to_unknown():
    """LLM 返回 intent_type=query_product 但缺 sku_or_keyword → 降级 unknown。"""
    from hub.intent.llm_parser import LLMParser
    cap = AsyncMock()
    cap.parse_intent = AsyncMock(return_value={
        "intent_type": "query_product", "fields": {}, "confidence": 0.9,  # 缺 sku_or_keyword
    })
    p = LLMParser(ai=cap)
    intent = await p.parse("x", context={})
    assert intent.intent_type == "unknown"
    assert intent.confidence == 0.0


@pytest.mark.asyncio
async def test_llm_parser_query_customer_history_missing_customer_keyword():
    """query_customer_history 缺 customer_keyword → 降级。"""
    from hub.intent.llm_parser import LLMParser
    cap = AsyncMock()
    cap.parse_intent = AsyncMock(return_value={
        "intent_type": "query_customer_history",
        "fields": {"sku_or_keyword": "SKU100"},  # 缺 customer_keyword
        "confidence": 0.9,
    })
    p = LLMParser(ai=cap)
    intent = await p.parse("x", context={})
    assert intent.intent_type == "unknown"


@pytest.mark.asyncio
async def test_llm_parser_fields_not_dict_falls_back():
    """LLM 返回 fields 不是 dict → 降级。"""
    from hub.intent.llm_parser import LLMParser
    cap = AsyncMock()
    cap.parse_intent = AsyncMock(return_value={
        "intent_type": "query_product", "fields": "not a dict", "confidence": 0.9,
    })
    p = LLMParser(ai=cap)
    intent = await p.parse("x", context={})
    assert intent.intent_type == "unknown"


@pytest.mark.asyncio
async def test_llm_parser_confidence_non_numeric_falls_back_to_zero():
    """LLM 返回 confidence 非数字（None / "high" / 字符串）→ 不抛 TypeError，降级 0.0。"""
    from hub.intent.llm_parser import LLMParser

    for bad_conf in [None, "high", "0.8x", [], {"x": 1}]:
        cap = AsyncMock()
        cap.parse_intent = AsyncMock(return_value={
            "intent_type": "query_product",
            "fields": {"sku_or_keyword": "X"},
            "confidence": bad_conf,
        })
        p = LLMParser(ai=cap)
        intent = await p.parse("x", context={})
        # 不应抛异常，confidence 降级为 0.0
        assert intent.confidence == 0.0
        # intent_type 仍可能是 query_product（字段齐全）但 confidence=0 会被 ChainParser 视为 unknown 等价
        assert intent.intent_type in ("query_product", "unknown")
```

- [ ] **Step 2: 实现 LLMParser**

文件 `backend/hub/intent/llm_parser.py`：
```python
"""LLMParser：用 AICapability schema-guided 解析。"""
from __future__ import annotations
import logging
from hub.ports import ParsedIntent
from hub.capabilities.deepseek import LLMServiceError, LLMParseError

logger = logging.getLogger("hub.intent.llm")


_INTENT_SCHEMA = {
    "intent_type": "query_product | query_customer_history | unknown",
    "fields": {
        "sku_or_keyword": "string (商品 SKU 或关键字)",
        "customer_keyword": "string | null (客户关键字)",
    },
    "confidence": "float 0.0~1.0",
}


# 每种 intent_type 的必填字段。LLM 返回缺失任一必填 → 降级 unknown 防 handler KeyError。
_REQUIRED_FIELDS = {
    "query_product": ["sku_or_keyword"],
    "query_customer_history": ["sku_or_keyword", "customer_keyword"],
}


def _unknown(parser_name: str) -> ParsedIntent:
    return ParsedIntent(
        intent_type="unknown", fields={}, confidence=0.0, parser=parser_name,
    )


class LLMParser:
    parser_name = "llm"

    def __init__(self, ai):
        self.ai = ai  # CapabilityProvider 实现 (可为 None)

    async def parse(self, text: str, context: dict) -> ParsedIntent:
        if self.ai is None:
            return _unknown(self.parser_name)
        try:
            raw = await self.ai.parse_intent(text, _INTENT_SCHEMA)
        except (LLMServiceError, LLMParseError) as e:
            logger.warning(f"LLM 解析降级: {e}")
            return _unknown(self.parser_name)
        except Exception:
            logger.exception("LLM 调用异常")
            return _unknown(self.parser_name)

        intent_type = str(raw.get("intent_type", "unknown"))
        fields = raw.get("fields") or {}
        if not isinstance(fields, dict):
            logger.warning(f"LLM 返回 fields 非 dict: {type(fields)}")
            return _unknown(self.parser_name)

        # **schema 必填字段校验**：缺任一必填 → 降级 unknown，避免下游 KeyError
        required = _REQUIRED_FIELDS.get(intent_type)
        if required is not None:
            for f in required:
                value = fields.get(f)
                if not value:  # None / "" / 0 都视为缺失
                    logger.warning(
                        f"LLM intent_type={intent_type} 缺必填字段 {f}，降级 unknown",
                    )
                    return _unknown(self.parser_name)

        # confidence 字段防御：LLM 可能返回 None / "high" / 非数字 → 降级 0.0
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            logger.warning(f"LLM 返回 confidence 非数字: {raw.get('confidence')!r}，降级 0.0")
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        return ParsedIntent(
            intent_type=intent_type,
            fields=dict(fields),
            confidence=confidence,
            parser=self.parser_name,
        )
```

- [ ] **Step 3: 写 ChainParser 测试**

文件 `backend/tests/test_chain_parser.py`：
```python
import pytest
from unittest.mock import AsyncMock
from hub.ports import ParsedIntent


@pytest.mark.asyncio
async def test_rule_hit_skips_llm():
    from hub.intent.chain_parser import ChainParser

    rule = AsyncMock()
    rule.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product", fields={"sku_or_keyword": "SKU100", "customer_keyword": None},
        confidence=0.95, parser="rule",
    ))
    llm = AsyncMock()

    chain = ChainParser(rule=rule, llm=llm, low_confidence_threshold=0.7)
    intent = await chain.parse("查 SKU100", context={})
    assert intent.parser == "rule"
    llm.parse.assert_not_called()


@pytest.mark.asyncio
async def test_rule_miss_falls_through_to_llm():
    from hub.intent.chain_parser import ChainParser

    rule = AsyncMock()
    rule.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="unknown", fields={}, confidence=0.0, parser="rule",
    ))
    llm = AsyncMock()
    llm.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product", fields={"sku_or_keyword": "X"},
        confidence=0.85, parser="llm",
    ))

    chain = ChainParser(rule=rule, llm=llm, low_confidence_threshold=0.7)
    intent = await chain.parse("帮我看下 X 多少钱", context={})
    assert intent.parser == "llm"


@pytest.mark.asyncio
async def test_llm_low_confidence_marked_pending_confirm():
    """LLM 返回 confidence < 阈值 → 保留意图但 needs_confirm=True。"""
    from hub.intent.chain_parser import ChainParser

    rule = AsyncMock()
    rule.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="unknown", fields={}, confidence=0.0, parser="rule",
    ))
    llm = AsyncMock()
    llm.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product", fields={"sku_or_keyword": "X"},
        confidence=0.5, parser="llm",
    ))

    chain = ChainParser(rule=rule, llm=llm, low_confidence_threshold=0.7)
    intent = await chain.parse("xxx", context={})
    assert intent.parser == "llm"
    assert intent.confidence == 0.5
    assert intent.notes == "low_confidence"  # ChainParser 标记给上层走确认卡


@pytest.mark.asyncio
async def test_llm_returns_unknown_passes_through():
    from hub.intent.chain_parser import ChainParser

    rule = AsyncMock()
    rule.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="unknown", fields={}, confidence=0.0, parser="rule",
    ))
    llm = AsyncMock()
    llm.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="unknown", fields={}, confidence=0.0, parser="llm",
    ))

    chain = ChainParser(rule=rule, llm=llm, low_confidence_threshold=0.7)
    intent = await chain.parse("???", context={})
    assert intent.intent_type == "unknown"
```

- [ ] **Step 4: 实现 ChainParser**

文件 `backend/hub/intent/chain_parser.py`：
```python
"""ChainParser：rule → llm 链；低置信度标记 needs_confirm。"""
from __future__ import annotations
from hub.ports import ParsedIntent


class ChainParser:
    parser_name = "chain"

    def __init__(self, rule, llm, *, low_confidence_threshold: float = 0.7):
        self.rule = rule
        self.llm = llm
        self.threshold = low_confidence_threshold

    async def parse(self, text: str, context: dict) -> ParsedIntent:
        rule_intent = await self.rule.parse(text, context)
        if rule_intent.intent_type != "unknown" and rule_intent.confidence >= self.threshold:
            return rule_intent

        llm_intent = await self.llm.parse(text, context)
        if llm_intent.intent_type == "unknown":
            return llm_intent

        # 低置信度标记，由上层渲染确认卡片
        if llm_intent.confidence < self.threshold:
            return ParsedIntent(
                intent_type=llm_intent.intent_type,
                fields=llm_intent.fields,
                confidence=llm_intent.confidence,
                parser=llm_intent.parser,
                notes="low_confidence",
            )
        return llm_intent
```

- [ ] **Step 5: 跑测试 + 提交**

```bash
pytest tests/test_llm_parser.py tests/test_chain_parser.py -v
git add backend/hub/intent/llm_parser.py backend/hub/intent/chain_parser.py \
        backend/tests/test_llm_parser.py backend/tests/test_chain_parser.py
git commit -m "feat(hub): LLMParser（schema-guided + 降级）+ ChainParser（rule→llm + 低置信度标记）"
```

---

## Task 6：MatchResolver + ConversationState

**Files:**
- Create: `backend/hub/match/__init__.py`
- Create: `backend/hub/match/resolver.py`
- Create: `backend/hub/match/conversation_state.py`
- Test: `backend/tests/test_match_resolver.py`
- Test: `backend/tests/test_conversation_state.py`

`MatchResolver` 接收一组候选项（来自 ERP 模糊查询），返回三种结果：
- `unique`：唯一命中，直接返回选中项
- `multi`：多命中，需要让用户回选编号
- `none`：0 命中，需要返回友好提示

`ConversationStateRepository` 把多命中的候选项缓存到 Redis（key=`hub:conv:<dingtalk_userid>`，TTL 5 分钟），用户回复编号时取出。

- [ ] **Step 1: 写 ConversationState 测试**

文件 `backend/tests/test_conversation_state.py`：
```python
import pytest
from fakeredis import aioredis as fakeredis_aio


@pytest.fixture
async def fake_redis():
    c = fakeredis_aio.FakeRedis()
    yield c
    await c.aclose()


@pytest.mark.asyncio
async def test_save_and_load(fake_redis):
    from hub.match.conversation_state import ConversationStateRepository
    repo = ConversationStateRepository(redis=fake_redis, ttl_seconds=300)

    state = {
        "intent_type": "query_product",
        "candidates": [{"id": 1, "label": "A"}, {"id": 2, "label": "B"}],
        "resource": "商品",
    }
    await repo.save("user1", state)
    loaded = await repo.load("user1")
    assert loaded == state


@pytest.mark.asyncio
async def test_load_missing_returns_none(fake_redis):
    from hub.match.conversation_state import ConversationStateRepository
    repo = ConversationStateRepository(redis=fake_redis, ttl_seconds=300)
    assert await repo.load("nobody") is None


@pytest.mark.asyncio
async def test_clear(fake_redis):
    from hub.match.conversation_state import ConversationStateRepository
    repo = ConversationStateRepository(redis=fake_redis, ttl_seconds=300)
    await repo.save("u", {"x": 1})
    await repo.clear("u")
    assert await repo.load("u") is None


@pytest.mark.asyncio
async def test_ttl_expires(fake_redis):
    """TTL 过期后无法读取（fakeredis 支持 ttl）。"""
    import asyncio
    from hub.match.conversation_state import ConversationStateRepository
    repo = ConversationStateRepository(redis=fake_redis, ttl_seconds=1)
    await repo.save("u", {"x": 1})
    assert await repo.load("u") is not None
    await fake_redis.expire("hub:conv:u", 0)  # 强制过期
    await asyncio.sleep(0.05)
    assert await repo.load("u") is None
```

- [ ] **Step 2: 实现 ConversationStateRepository**

文件 `backend/hub/match/__init__.py`：
```python
"""HUB 模糊匹配 + 多轮会话状态。"""
```

文件 `backend/hub/match/conversation_state.py`：
```python
"""多轮会话状态（多命中选编号 / 低置信度确认 等待用户回复）。

存储：Redis key `hub:conv:<dingtalk_userid>`，TTL 5 分钟。
"""
from __future__ import annotations
import json
from redis.asyncio import Redis


class ConversationStateRepository:
    KEY_PREFIX = "hub:conv:"

    def __init__(self, redis: Redis, *, ttl_seconds: int = 300):
        self.redis = redis
        self.ttl = ttl_seconds

    def _key(self, dingtalk_userid: str) -> str:
        return f"{self.KEY_PREFIX}{dingtalk_userid}"

    async def save(self, dingtalk_userid: str, state: dict) -> None:
        await self.redis.set(
            self._key(dingtalk_userid),
            json.dumps(state, ensure_ascii=False),
            ex=self.ttl,
        )

    async def load(self, dingtalk_userid: str) -> dict | None:
        raw = await self.redis.get(self._key(dingtalk_userid))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def clear(self, dingtalk_userid: str) -> None:
        await self.redis.delete(self._key(dingtalk_userid))
```

- [ ] **Step 3: 写 MatchResolver 测试**

文件 `backend/tests/test_match_resolver.py`：
```python
import pytest


@pytest.mark.asyncio
async def test_unique_match():
    from hub.match.resolver import MatchResolver, MatchOutcome

    candidates = [{"id": 1, "label": "A 商品", "subtitle": "SKU 1"}]
    res = MatchResolver().resolve(keyword="A", resource="商品", candidates=candidates, max_show=5)
    assert res.outcome == MatchOutcome.UNIQUE
    assert res.selected == candidates[0]


@pytest.mark.asyncio
async def test_multi_match():
    from hub.match.resolver import MatchResolver, MatchOutcome

    candidates = [
        {"id": 1, "label": "A1"}, {"id": 2, "label": "A2"}, {"id": 3, "label": "A3"},
    ]
    res = MatchResolver().resolve(keyword="A", resource="商品", candidates=candidates, max_show=5)
    assert res.outcome == MatchOutcome.MULTI
    assert len(res.choices) == 3


@pytest.mark.asyncio
async def test_no_match():
    from hub.match.resolver import MatchResolver, MatchOutcome
    res = MatchResolver().resolve(keyword="zzz", resource="商品", candidates=[], max_show=5)
    assert res.outcome == MatchOutcome.NONE


@pytest.mark.asyncio
async def test_multi_truncates_to_max_show():
    from hub.match.resolver import MatchResolver, MatchOutcome

    candidates = [{"id": i, "label": f"X{i}"} for i in range(20)]
    res = MatchResolver().resolve(keyword="X", resource="商品", candidates=candidates, max_show=5)
    assert res.outcome == MatchOutcome.MULTI
    assert len(res.choices) == 5
    assert res.truncated is True


@pytest.mark.asyncio
async def test_resolve_choice_by_number():
    """根据用户回复的编号定位 candidates 中具体项。"""
    from hub.match.resolver import MatchResolver

    candidates = [{"id": 10, "label": "X"}, {"id": 11, "label": "Y"}]
    chosen = MatchResolver().resolve_choice(candidates, choice_number=2)
    assert chosen == candidates[1]


@pytest.mark.asyncio
async def test_resolve_choice_out_of_range():
    from hub.match.resolver import MatchResolver
    candidates = [{"id": 1, "label": "X"}]
    chosen = MatchResolver().resolve_choice(candidates, choice_number=99)
    assert chosen is None
```

- [ ] **Step 4: 实现 MatchResolver**

文件 `backend/hub/match/resolver.py`：
```python
"""MatchResolver：unique / multi / none 三种结果统一处理。"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class MatchOutcome(str, Enum):
    UNIQUE = "unique"
    MULTI = "multi"
    NONE = "none"


@dataclass
class MatchResult:
    outcome: MatchOutcome
    selected: dict | None = None
    choices: list[dict] | None = None
    truncated: bool = False


class MatchResolver:
    def resolve(
        self, *, keyword: str, resource: str, candidates: list[dict], max_show: int = 5,
    ) -> MatchResult:
        if not candidates:
            return MatchResult(outcome=MatchOutcome.NONE)
        if len(candidates) == 1:
            return MatchResult(outcome=MatchOutcome.UNIQUE, selected=candidates[0])
        truncated = len(candidates) > max_show
        return MatchResult(
            outcome=MatchOutcome.MULTI,
            choices=candidates[:max_show],
            truncated=truncated,
        )

    def resolve_choice(self, candidates: list[dict], choice_number: int) -> dict | None:
        if 1 <= choice_number <= len(candidates):
            return candidates[choice_number - 1]
        return None
```

- [ ] **Step 5: 跑测试 + 提交**

```bash
pytest tests/test_conversation_state.py tests/test_match_resolver.py -v
git add backend/hub/match/ \
        backend/tests/test_conversation_state.py \
        backend/tests/test_match_resolver.py
git commit -m "feat(hub): MatchResolver（unique/multi/none 三态）+ ConversationStateRepository（Redis 多轮上下文）"
```

---

## Task 7：DefaultPricingStrategy

**Files:**
- Create: `backend/hub/strategy/__init__.py`
- Create: `backend/hub/strategy/pricing.py`
- Test: `backend/tests/test_pricing_strategy.py`

价格 fallback 链：客户最近成交价（取首条）→ 系统零售价。

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_pricing_strategy.py`：
```python
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_uses_recent_customer_price():
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    erp.get_product_customer_prices = AsyncMock(return_value={
        "records": [
            {"unit_price": "98.50", "order_no": "O1", "order_date": "2026-04-01T00:00:00Z"},
        ],
    })
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "retail_price": "120.00"}],
    })

    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(product_id=1, customer_id=10, acting_as=42)
    assert info.unit_price == "98.50"
    assert info.source == "customer_history"
    assert info.customer_id == 10


@pytest.mark.asyncio
async def test_no_customer_uses_fallback_retail_when_provided():
    """调用方传入 fallback_retail_price → 直接用，不调 ERP。"""
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(
        product_id=1, customer_id=None, acting_as=42,
        fallback_retail_price="120.00",
    )
    assert info.unit_price == "120.00"
    assert info.source == "retail"
    erp.get_product.assert_not_called()


@pytest.mark.asyncio
async def test_no_customer_no_fallback_uses_get_product():
    """无 fallback → 用 get_product(product_id) 精确反查。"""
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    erp.get_product = AsyncMock(return_value={"id": 1, "retail_price": "120.00"})
    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(product_id=1, customer_id=None, acting_as=42)
    assert info.unit_price == "120.00"
    assert info.source == "retail"
    erp.get_product.assert_awaited_once_with(product_id=1, acting_as_user_id=42)


@pytest.mark.asyncio
async def test_empty_history_uses_fallback_first():
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    erp.get_product_customer_prices = AsyncMock(return_value={"records": []})
    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(
        product_id=1, customer_id=10, acting_as=42,
        fallback_retail_price="120.00",
    )
    assert info.source == "retail"
    assert info.unit_price == "120.00"


@pytest.mark.asyncio
async def test_history_query_failure_falls_back():
    """ERP 历史价查询异常 → 降级到 fallback_retail_price。"""
    from hub.strategy.pricing import DefaultPricingStrategy
    from hub.adapters.downstream.erp4 import ErpSystemError
    erp = AsyncMock()
    erp.get_product_customer_prices = AsyncMock(side_effect=ErpSystemError("timeout"))
    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(
        product_id=1, customer_id=10, acting_as=42,
        fallback_retail_price="120.00",
    )
    assert info.source == "retail"
    assert info.unit_price == "120.00"
    assert info.notes is not None  # 含降级原因


@pytest.mark.asyncio
async def test_history_403_raises_not_falls_back():
    """历史价 403 → ErpPermissionError 向上抛，不降级零售价。"""
    from hub.strategy.pricing import DefaultPricingStrategy
    from hub.adapters.downstream.erp4 import ErpPermissionError
    erp = AsyncMock()
    erp.get_product_customer_prices = AsyncMock(side_effect=ErpPermissionError("403"))
    strat = DefaultPricingStrategy(erp_adapter=erp)
    with pytest.raises(ErpPermissionError):
        await strat.get_price(
            product_id=1, customer_id=10, acting_as=42,
            fallback_retail_price="120.00",
        )


@pytest.mark.asyncio
async def test_decimal_string_preserved():
    """价格保持 Decimal 字符串，不转 float。"""
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    erp.get_product_customer_prices = AsyncMock(return_value={
        "records": [{"unit_price": "98.500000", "order_no": "x", "order_date": "x"}],
    })
    erp.search_products = AsyncMock(return_value={"items": []})

    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(product_id=1, customer_id=10, acting_as=42)
    assert isinstance(info.unit_price, str)
    assert "98.500000" == info.unit_price or "98.50" == info.unit_price
```

- [ ] **Step 2: 实现 DefaultPricingStrategy**

文件 `backend/hub/strategy/__init__.py`：
```python
"""HUB 业务策略。"""
```

文件 `backend/hub/strategy/pricing.py`：
```python
"""DefaultPricingStrategy：客户历史价 → 零售价 fallback。

零售价 fallback 策略（按优先级）：
1. 调用方传入的 `fallback_retail_price`（候选项已含，最快）
2. `erp.get_product(product_id)` 精确反查
3. 都失败 → unit_price="0" + notes 标记

权限错误（ErpPermissionError）**向上抛**——上游翻译为 PERM_NO_CUSTOMER_HISTORY
（不能默默降级成零售价隐藏权限问题）。仅 ErpSystemError / 网络超时降级。
"""
from __future__ import annotations
import logging
from hub.ports import PriceInfo
from hub.adapters.downstream.erp4 import ErpSystemError, ErpPermissionError

logger = logging.getLogger("hub.strategy.pricing")


class DefaultPricingStrategy:
    def __init__(self, erp_adapter):
        self.erp = erp_adapter

    async def get_price(
        self, product_id: int, customer_id: int | None, *, acting_as: int,
        fallback_retail_price: str | None = None,
    ) -> PriceInfo:
        notes = None

        if customer_id is not None:
            try:
                resp = await self.erp.get_product_customer_prices(
                    product_id=product_id, customer_id=customer_id, limit=1,
                    acting_as_user_id=acting_as,
                )
                records = resp.get("records", [])
                if records:
                    return PriceInfo(
                        unit_price=str(records[0]["unit_price"]),
                        source="customer_history",
                        customer_id=customer_id,
                        notes=None,
                    )
            except ErpPermissionError:
                # 权限不足必须向上抛，不能降级隐藏；上游翻译为 PERM_NO_CUSTOMER_HISTORY
                raise
            except ErpSystemError as e:
                logger.warning(f"历史价查询系统错误，降级零售价: {e}")
                notes = "历史价查询暂不可用"

        # fallback 1：调用方传入（业务用例已经从模糊搜索结果拿到）
        if fallback_retail_price is not None:
            return PriceInfo(
                unit_price=str(fallback_retail_price),
                source="retail", customer_id=customer_id, notes=notes,
            )

        # fallback 2：用 ID 精确反查商品（不依赖 keyword 搜索）
        try:
            prod = await self.erp.get_product(
                product_id=product_id, acting_as_user_id=acting_as,
            )
            if prod and prod.get("retail_price") is not None:
                return PriceInfo(
                    unit_price=str(prod["retail_price"]),
                    source="retail", customer_id=customer_id, notes=notes,
                )
        except Exception as e:
            logger.warning(f"商品详情查询失败: {e}")

        return PriceInfo(
            unit_price="0", source="fallback_default",
            customer_id=customer_id, notes=notes or "价格暂不可用",
        )
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
pytest tests/test_pricing_strategy.py -v
git add backend/hub/strategy/ backend/tests/test_pricing_strategy.py
git commit -m "feat(hub): DefaultPricingStrategy（客户历史价 → 系统零售价 fallback + 降级）"
```

---

## Task 8：ERP 熔断器

**Files:**
- Create: `backend/hub/circuit_breaker/__init__.py`
- Create: `backend/hub/circuit_breaker/erp_breaker.py`
- Test: `backend/tests/test_erp_breaker.py`

按 spec §13.2 + P1-2：30s 内连续 5 次失败 → 熔断 60s → 半开探测。

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_erp_breaker.py`：
```python
import pytest
import asyncio
from hub.circuit_breaker.erp_breaker import CircuitBreaker, CircuitOpenError


@pytest.mark.asyncio
async def test_closed_passes_through():
    cb = CircuitBreaker(threshold=5, window_seconds=30, open_seconds=60)
    async def ok(): return "result"
    assert await cb.call(ok) == "result"


@pytest.mark.asyncio
async def test_opens_after_threshold_failures():
    cb = CircuitBreaker(threshold=3, window_seconds=30, open_seconds=60)
    async def bad(): raise RuntimeError("err")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(bad)

    assert cb.state == "open"
    with pytest.raises(CircuitOpenError):
        await cb.call(bad)


@pytest.mark.asyncio
async def test_half_open_after_open_window():
    cb = CircuitBreaker(threshold=2, window_seconds=30, open_seconds=0.1)
    async def bad(): raise RuntimeError("err")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(bad)
    assert cb.state == "open"

    await asyncio.sleep(0.15)

    async def ok(): return "ok"
    result = await cb.call(ok)
    assert result == "ok"
    assert cb.state == "closed"


@pytest.mark.asyncio
async def test_half_open_failure_reopens():
    cb = CircuitBreaker(threshold=2, window_seconds=30, open_seconds=0.1)
    async def bad(): raise RuntimeError("err")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(bad)
    await asyncio.sleep(0.15)

    with pytest.raises(RuntimeError):
        await cb.call(bad)
    assert cb.state == "open"


@pytest.mark.asyncio
async def test_failures_outside_window_reset():
    cb = CircuitBreaker(threshold=3, window_seconds=0.1, open_seconds=60)
    async def bad(): raise RuntimeError("err")

    with pytest.raises(RuntimeError):
        await cb.call(bad)
    await asyncio.sleep(0.15)
    # 窗口外的失败不计入；接下来 2 次失败不应该开熔
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(bad)
    assert cb.state == "closed"


@pytest.mark.asyncio
async def test_only_countable_exceptions_count():
    """业务 4xx（不在 countable_exceptions 里）不计入熔断统计。"""
    class SystemErr(Exception): pass
    class BizErr(Exception): pass

    cb = CircuitBreaker(
        threshold=3, window_seconds=30, open_seconds=60,
        countable_exceptions=(SystemErr,),
    )

    async def biz_bad(): raise BizErr("403 perm")

    # 5 次业务错不应触发熔断
    for _ in range(5):
        with pytest.raises(BizErr):
            await cb.call(biz_bad)
    assert cb.state == "closed"

    # 但 3 次系统错就会熔断
    async def sys_bad(): raise SystemErr("503")
    for _ in range(3):
        with pytest.raises(SystemErr):
            await cb.call(sys_bad)
    assert cb.state == "open"
```

- [ ] **Step 2: 实现 CircuitBreaker**

文件 `backend/hub/circuit_breaker/__init__.py`：
```python
from hub.circuit_breaker.erp_breaker import CircuitBreaker, CircuitOpenError

__all__ = ["CircuitBreaker", "CircuitOpenError"]
```

文件 `backend/hub/circuit_breaker/erp_breaker.py`：
```python
"""轻量熔断器：threshold + window + open + half-open。

**只统计系统级故障**：网络错误 / 5xx / 超时（即 ErpSystemError 及子类）。
业务 4xx（权限不足 / 资源不存在 / 参数错）**不**计入失败统计——这些是单个用户/请求
的问题，不应该影响其他正常用户。
"""
from __future__ import annotations
import time
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class CircuitOpenError(Exception):
    """熔断器开启状态下拒绝请求。"""


class CircuitBreaker:
    def __init__(
        self,
        *, threshold: int = 5, window_seconds: float = 30.0,
        open_seconds: float = 60.0,
        countable_exceptions: tuple[type[BaseException], ...] | None = None,
    ):
        """
        Args:
            countable_exceptions: 计入失败统计的异常类型。其他异常 raise 但不累计。
                None = 所有 Exception 计入（向后兼容）。
                生产建议传 (ErpSystemError,) 等系统级异常元组。
        """
        self.threshold = threshold
        self.window = window_seconds
        self.open_window = open_seconds
        self.countable = countable_exceptions  # None 表示全部计入
        self._failures: list[float] = []  # 失败时间戳
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is not None:
            if time.monotonic() - self._opened_at < self.open_window:
                return "open"
            return "half_open"
        return "closed"

    def _should_count(self, exc: BaseException) -> bool:
        if self.countable is None:
            return True
        return isinstance(exc, self.countable)

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        st = self.state
        if st == "open":
            raise CircuitOpenError("ERP 调用熔断中，请稍后重试")

        try:
            result = await fn()
        except Exception as e:
            if self._should_count(e):
                now = time.monotonic()
                self._failures.append(now)
                self._failures = [t for t in self._failures if now - t < self.window]
                if len(self._failures) >= self.threshold:
                    self._opened_at = now
                    self._failures.clear()
            raise

        # 成功 → 如果是 half_open 则重置；closed 则保持
        if st == "half_open":
            self._opened_at = None
            self._failures.clear()
        return result
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
pytest tests/test_erp_breaker.py -v
git add backend/hub/circuit_breaker/ backend/tests/test_erp_breaker.py
git commit -m "feat(hub): ERP 熔断器（5/30s 触发 + 60s open + half-open 探测）"
```

---

## Task 9：QueryProductUseCase + QueryCustomerHistoryUseCase

**Files:**
- Create: `backend/hub/usecases/__init__.py`
- Create: `backend/hub/usecases/query_product.py`
- Create: `backend/hub/usecases/query_customer_history.py`
- Test: `backend/tests/test_query_product_usecase.py`
- Test: `backend/tests/test_query_customer_history_usecase.py`

业务用例编排：权限校验 → 模糊匹配（必要时多轮）→ 调 ERP → 渲染卡片 → 通过 sender 回钉钉。低耦合：依赖端口接口而不是具体 adapter。

- [ ] **Step 1: 写 QueryProductUseCase 测试**

文件 `backend/tests/test_query_product_usecase.py`：
```python
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_unique_product_returns_card():
    from hub.usecases.query_product import QueryProductUseCase
    erp = AsyncMock()
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "sku": "SKU100", "name": "鼠标", "retail_price": "120.00"}],
    })

    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=type("P", (), {
        "unit_price": "120.00", "source": "retail", "customer_id": None, "notes": None,
    })())

    sender = AsyncMock()
    state = AsyncMock()

    uc = QueryProductUseCase(
        erp=erp, pricing=pricing, sender=sender, state=state,
    )
    await uc.execute(
        sku_or_keyword="SKU100", dingtalk_userid="m1", acting_as=42,
    )

    sender.send_text.assert_awaited_once()
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "鼠标" in sent
    assert "120.00" in sent


@pytest.mark.asyncio
async def test_multi_match_saves_state_and_sends_choice_card():
    from hub.usecases.query_product import QueryProductUseCase
    erp = AsyncMock()
    erp.search_products = AsyncMock(return_value={
        "items": [
            {"id": 1, "sku": "SKU100", "name": "鼠标 A", "retail_price": "100"},
            {"id": 2, "sku": "SKU101", "name": "鼠标 B", "retail_price": "110"},
        ],
    })

    sender = AsyncMock()
    state = AsyncMock()

    uc = QueryProductUseCase(erp=erp, pricing=AsyncMock(), sender=sender, state=state)
    await uc.execute(
        sku_or_keyword="鼠标", dingtalk_userid="m1", acting_as=42,
    )

    sender.send_text.assert_awaited_once()
    state.save.assert_awaited_once()
    saved = state.save.call_args.args[1]
    assert saved["resource"] == "商品"
    assert len(saved["candidates"]) == 2


@pytest.mark.asyncio
async def test_no_match_returns_friendly():
    from hub.usecases.query_product import QueryProductUseCase
    erp = AsyncMock()
    erp.search_products = AsyncMock(return_value={"items": []})

    sender = AsyncMock()
    uc = QueryProductUseCase(erp=erp, pricing=AsyncMock(), sender=sender, state=AsyncMock())
    await uc.execute(sku_or_keyword="zzz", dingtalk_userid="m1", acting_as=42)

    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "未找到" in sent or "没找到" in sent


@pytest.mark.asyncio
async def test_erp_permission_denied_translates_to_user_msg():
    from hub.usecases.query_product import QueryProductUseCase
    from hub.adapters.downstream.erp4 import ErpPermissionError

    erp = AsyncMock()
    erp.search_products = AsyncMock(side_effect=ErpPermissionError("403"))

    sender = AsyncMock()
    uc = QueryProductUseCase(erp=erp, pricing=AsyncMock(), sender=sender, state=AsyncMock())
    await uc.execute(sku_or_keyword="X", dingtalk_userid="m1", acting_as=42)

    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "权限" in sent


@pytest.mark.asyncio
async def test_circuit_open_returns_friendly_message():
    from hub.usecases.query_product import QueryProductUseCase
    from hub.circuit_breaker import CircuitOpenError

    erp = AsyncMock()
    erp.search_products = AsyncMock(side_effect=CircuitOpenError("熔断"))

    sender = AsyncMock()
    uc = QueryProductUseCase(erp=erp, pricing=AsyncMock(), sender=sender, state=AsyncMock())
    await uc.execute(sku_or_keyword="X", dingtalk_userid="m1", acting_as=42)

    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "暂时不可用" in sent or "稍后" in sent


@pytest.mark.asyncio
async def test_execute_selected_renders_card_with_name_and_stock():
    """编号选择后 execute_selected 用候选项 dict 直接渲染：必含 name + 库存。"""
    from hub.usecases.query_product import QueryProductUseCase
    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=type("P", (), {
        "unit_price": "120.00", "source": "retail", "customer_id": None, "notes": None,
    })())
    sender = AsyncMock()
    uc = QueryProductUseCase(erp=AsyncMock(), pricing=pricing, sender=sender, state=AsyncMock())

    selected = {
        "id": 1, "sku": "SKU100", "name": "鼠标 X",
        "retail_price": "120.00", "stock": 50,
    }
    await uc.execute_selected(product=selected, dingtalk_userid="m1", acting_as=42)

    sender.send_text.assert_awaited_once()
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "鼠标 X" in sent
    assert "120.00" in sent
    assert "50" in sent  # 库存渲染


@pytest.mark.asyncio
async def test_execute_selected_passes_fallback_retail_to_pricing():
    """execute_selected 必须把候选项的 retail_price 传给 PricingStrategy 当 fallback。"""
    from hub.usecases.query_product import QueryProductUseCase
    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=type("P", (), {
        "unit_price": "120.00", "source": "retail", "customer_id": None, "notes": None,
    })())
    uc = QueryProductUseCase(erp=AsyncMock(), pricing=pricing, sender=AsyncMock(), state=AsyncMock())
    await uc.execute_selected(
        product={"id": 1, "name": "X", "retail_price": "99.99"},
        dingtalk_userid="m1", acting_as=42,
    )
    args = pricing.get_price.call_args.kwargs
    assert args["fallback_retail_price"] == "99.99"


@pytest.mark.asyncio
async def test_erp_5xx_uses_retry_friendly():
    from hub.usecases.query_product import QueryProductUseCase
    from hub.adapters.downstream.erp4 import ErpSystemError

    erp = AsyncMock()
    erp.search_products = AsyncMock(side_effect=ErpSystemError("503"))

    sender = AsyncMock()
    uc = QueryProductUseCase(erp=erp, pricing=AsyncMock(), sender=sender, state=AsyncMock())
    await uc.execute(sku_or_keyword="X", dingtalk_userid="m1", acting_as=42)

    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "繁忙" in sent or "稍后" in sent
```

- [ ] **Step 2: 实现 QueryProductUseCase**

文件 `backend/hub/usecases/__init__.py`：
```python
"""HUB 业务用例编排。"""
```

文件 `backend/hub/usecases/query_product.py`：
```python
"""查商品（无客户场景）：模糊匹配 → 唯一/多/无 → 渲染 + 回钉钉。"""
from __future__ import annotations
import logging
from hub.adapters.downstream.erp4 import (
    ErpAdapterError, ErpPermissionError, ErpSystemError, ErpNotFoundError,
)
from hub.circuit_breaker import CircuitOpenError
from hub.match.resolver import MatchResolver, MatchOutcome
from hub.error_codes import BizErrorCode, build_user_message
from hub import cards
from hub.ports import OutboundMessage

logger = logging.getLogger("hub.usecase.query_product")


class QueryProductUseCase:
    def __init__(self, *, erp, pricing, sender, state, max_show: int = 5):
        self.erp = erp
        self.pricing = pricing
        self.sender = sender
        self.state = state
        self.max_show = max_show
        self.matcher = MatchResolver()

    async def execute(
        self, *, sku_or_keyword: str, dingtalk_userid: str, acting_as: int,
    ) -> None:
        """模糊搜索入口：调 ERP 搜商品 → 唯一/多/无 → 渲染。"""
        try:
            resp = await self.erp.search_products(
                query=sku_or_keyword, acting_as_user_id=acting_as,
            )
        except ErpPermissionError:
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.PERM_DOWNSTREAM_DENIED))
            return
        except CircuitOpenError:
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.ERP_CIRCUIT_OPEN))
            return
        except (ErpSystemError, ErpAdapterError):
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.ERP_TIMEOUT))
            return

        # 保留完整字段（name / stock 等）让 cards 模板能渲染
        candidates = [
            {
                "id": p["id"],
                "sku": p.get("sku"),
                "name": p.get("name", str(p["id"])),  # cards 用 name
                "label": p.get("name", str(p["id"])),  # multi_match 卡片用 label
                "subtitle": f"SKU {p.get('sku', '-')}",
                "retail_price": p.get("retail_price"),
                "stock": p.get("total_stock"),  # ERP 字段名 total_stock
            }
            for p in resp.get("items", [])
        ]
        result = self.matcher.resolve(
            keyword=sku_or_keyword, resource="商品", candidates=candidates, max_show=self.max_show,
        )

        if result.outcome == MatchOutcome.NONE:
            msg = build_user_message(BizErrorCode.MATCH_NOT_FOUND, keyword=sku_or_keyword, resource="商品")
            await self._send(dingtalk_userid, msg)
            return

        if result.outcome == MatchOutcome.MULTI:
            await self.state.save(dingtalk_userid, {
                "intent_type": "query_product",
                "resource": "商品",
                "candidates": result.choices,
                "pending_choice": "yes",
            })
            card = cards.multi_match_select_card(
                keyword=sku_or_keyword, resource="商品", items=result.choices,
            )
            await self._send_message(dingtalk_userid, card)
            return

        # UNIQUE
        prod = result.selected
        await self._render_unique(prod, dingtalk_userid, acting_as)

    async def execute_selected(
        self, *, product: dict, dingtalk_userid: str, acting_as: int,
    ) -> None:
        """编号选择后直接用候选项渲染，**不再二次模糊搜索**。

        product: 来自 conversation_state 的候选项（含 id / sku / name / retail_price）。
        """
        await self._render_unique(product, dingtalk_userid, acting_as)

    async def _render_unique(
        self, prod: dict, dingtalk_userid: str, acting_as: int,
    ) -> None:
        """从已确定的商品候选项渲染卡片。"""
        # 候选项已含 retail_price，直接传给 PricingStrategy 用作 fallback
        info = await self.pricing.get_price(
            product_id=prod["id"], customer_id=None, acting_as=acting_as,
            fallback_retail_price=prod.get("retail_price"),
        )
        card = cards.product_simple_card(prod, retail_price=info.unit_price)
        await self._send_message(dingtalk_userid, card)

    async def _send(self, userid: str, text: str) -> None:
        try:
            await self.sender.send_text(dingtalk_userid=userid, text=text)
        except Exception:
            logger.exception(f"send_text 失败 userid={userid}")

    async def _send_message(self, userid: str, msg: OutboundMessage) -> None:
        try:
            if msg.type.value == "text":
                await self.sender.send_text(dingtalk_userid=userid, text=msg.text or "")
            elif msg.type.value == "markdown":
                await self.sender.send_markdown(
                    dingtalk_userid=userid, title="HUB", markdown=msg.markdown or "",
                )
            else:
                await self.sender.send_action_card(
                    dingtalk_userid=userid, actioncard=msg.actioncard or {},
                )
        except Exception:
            logger.exception(f"send 失败 userid={userid}")
```

- [ ] **Step 3: 写 QueryCustomerHistoryUseCase 测试**

文件 `backend/tests/test_query_customer_history_usecase.py`：
```python
import pytest
from unittest.mock import AsyncMock


def _make_uc(erp, sender, state, pricing=None):
    from hub.usecases.query_customer_history import QueryCustomerHistoryUseCase
    return QueryCustomerHistoryUseCase(
        erp=erp,
        pricing=pricing or AsyncMock(),
        sender=sender, state=state,
    )


@pytest.mark.asyncio
async def test_unique_customer_unique_product_renders_history():
    from hub.usecases.query_customer_history import QueryCustomerHistoryUseCase
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [{"id": 9, "name": "阿里巴巴集团"}],
    })
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "sku": "SKU100", "name": "鼠标", "retail_price": "120"}],
    })
    erp.get_product_customer_prices = AsyncMock(return_value={
        "records": [
            {"unit_price": "98.00", "order_no": "O1", "order_date": "2026-04-01T00:00:00Z"},
            {"unit_price": "99.00", "order_no": "O2", "order_date": "2026-03-01T00:00:00Z"},
        ],
    })

    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=type("P", (), {
        "unit_price": "98.00", "source": "customer_history", "customer_id": 9, "notes": None,
    })())

    sender = AsyncMock()
    state = AsyncMock()
    uc = _make_uc(erp, sender, state, pricing)
    await uc.execute(
        sku_or_keyword="SKU100", customer_keyword="阿里",
        dingtalk_userid="m1", acting_as=42,
    )
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "阿里巴巴集团" in sent
    assert "98.00" in sent or "99.00" in sent


@pytest.mark.asyncio
async def test_multi_customer_saves_state():
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [
            {"id": 9, "name": "阿里巴巴"}, {"id": 10, "name": "阿里云"},
        ],
    })

    sender = AsyncMock()
    state = AsyncMock()
    uc = _make_uc(erp, sender, state)
    await uc.execute(
        sku_or_keyword="SKU100", customer_keyword="阿里",
        dingtalk_userid="m1", acting_as=42,
    )

    state.save.assert_awaited_once()
    saved = state.save.call_args.args[1]
    assert saved["resource"] == "客户"
    # 必须保留 sku_or_keyword 以便编号选择后继续查
    assert saved.get("sku_or_keyword") == "SKU100"


@pytest.mark.asyncio
async def test_multi_product_saves_state_with_resolved_customer():
    """客户唯一但商品多命中 → 保存状态时记下 customer_id。"""
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [{"id": 9, "name": "阿里"}],
    })
    erp.search_products = AsyncMock(return_value={
        "items": [
            {"id": 1, "sku": "SKU100A", "name": "鼠标 A"},
            {"id": 2, "sku": "SKU100B", "name": "鼠标 B"},
        ],
    })

    state = AsyncMock()
    sender = AsyncMock()
    uc = _make_uc(erp, sender, state)
    await uc.execute(
        sku_or_keyword="SKU100", customer_keyword="阿里",
        dingtalk_userid="m1", acting_as=42,
    )
    state.save.assert_awaited_once()
    saved = state.save.call_args.args[1]
    assert saved["resource"] == "商品"
    assert saved["customer_id"] == 9


@pytest.mark.asyncio
async def test_customer_not_found():
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={"items": []})

    sender = AsyncMock()
    uc = _make_uc(erp, sender, AsyncMock())
    await uc.execute(
        sku_or_keyword="X", customer_keyword="不存在的客户",
        dingtalk_userid="m1", acting_as=42,
    )

    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "不存在的客户" in sent or "客户" in sent
    assert "未找到" in sent or "没找到" in sent


@pytest.mark.asyncio
async def test_empty_history_still_renders_with_retail():
    """客户存在 + 商品存在 + 该客户无历史价 → 仍渲染商品 + 系统零售价。"""
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [{"id": 9, "name": "阿里"}],
    })
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "sku": "SKU100", "name": "鼠标", "retail_price": "120"}],
    })

    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=type("P", (), {
        "unit_price": "120.00", "source": "retail", "customer_id": 9, "notes": None,
    })())

    sender = AsyncMock()
    uc = _make_uc(erp, sender, AsyncMock(), pricing)
    await uc.execute(
        sku_or_keyword="SKU100", customer_keyword="阿里",
        dingtalk_userid="m1", acting_as=42,
    )
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "120" in sent


@pytest.mark.asyncio
async def test_erp_permission_denied():
    from hub.adapters.downstream.erp4 import ErpPermissionError
    erp = AsyncMock()
    erp.search_customers = AsyncMock(side_effect=ErpPermissionError("403"))

    sender = AsyncMock()
    uc = _make_uc(erp, sender, AsyncMock())
    await uc.execute(
        sku_or_keyword="X", customer_keyword="X",
        dingtalk_userid="m1", acting_as=42,
    )
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "权限" in sent


@pytest.mark.asyncio
async def test_history_price_403_translates_to_perm_message():
    """历史价 403 → 用户看到无权限提示，不是降级的零售价/空历史。"""
    from hub.adapters.downstream.erp4 import ErpPermissionError
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [{"id": 9, "name": "阿里"}],
    })
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "sku": "SKU100", "name": "鼠标", "retail_price": "120"}],
    })
    erp.get_product_customer_prices = AsyncMock(side_effect=ErpPermissionError("403"))

    pricing = AsyncMock()
    pricing.get_price = AsyncMock(side_effect=ErpPermissionError("403"))

    sender = AsyncMock()
    uc = _make_uc(erp, sender, AsyncMock(), pricing)
    await uc.execute(
        sku_or_keyword="SKU100", customer_keyword="阿里",
        dingtalk_userid="m1", acting_as=42,
    )
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "权限" in sent or "PERM" not in sent  # 中文提示，不暴露 code


@pytest.mark.asyncio
async def test_circuit_open_returns_friendly():
    from hub.circuit_breaker import CircuitOpenError
    erp = AsyncMock()
    erp.search_customers = AsyncMock(side_effect=CircuitOpenError("open"))

    sender = AsyncMock()
    uc = _make_uc(erp, sender, AsyncMock())
    await uc.execute(
        sku_or_keyword="X", customer_keyword="X",
        dingtalk_userid="m1", acting_as=42,
    )
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "暂时不可用" in sent or "稍后" in sent
```

- [ ] **Step 4: 实现 QueryCustomerHistoryUseCase**

文件 `backend/hub/usecases/query_customer_history.py`：
```python
"""查商品 + 客户历史价：先解客户，再解商品，最后取价格。"""
from __future__ import annotations
import logging
from hub.adapters.downstream.erp4 import (
    ErpAdapterError, ErpPermissionError, ErpSystemError,
)
from hub.circuit_breaker import CircuitOpenError
from hub.match.resolver import MatchResolver, MatchOutcome
from hub.error_codes import BizErrorCode, build_user_message
from hub import cards
from hub.ports import OutboundMessage

logger = logging.getLogger("hub.usecase.query_customer_history")


class QueryCustomerHistoryUseCase:
    def __init__(self, *, erp, pricing, sender, state, max_show: int = 5):
        self.erp = erp
        self.pricing = pricing
        self.sender = sender
        self.state = state
        self.max_show = max_show
        self.matcher = MatchResolver()

    async def execute(
        self, *, sku_or_keyword: str, customer_keyword: str,
        dingtalk_userid: str, acting_as: int,
    ) -> None:
        # 1. 解客户
        try:
            cust_resp = await self.erp.search_customers(
                query=customer_keyword, acting_as_user_id=acting_as,
            )
        except ErpPermissionError:
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.PERM_DOWNSTREAM_DENIED))
            return
        except CircuitOpenError:
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.ERP_CIRCUIT_OPEN))
            return
        except (ErpSystemError, ErpAdapterError):
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.ERP_TIMEOUT))
            return

        cust_candidates = [
            {"id": c["id"], "label": c.get("name", str(c["id"])),
             "subtitle": f"客户编号 {c['id']}"}
            for c in cust_resp.get("items", [])
        ]
        cust_result = self.matcher.resolve(
            keyword=customer_keyword, resource="客户",
            candidates=cust_candidates, max_show=self.max_show,
        )

        if cust_result.outcome == MatchOutcome.NONE:
            await self._send(dingtalk_userid, build_user_message(
                BizErrorCode.MATCH_NOT_FOUND, keyword=customer_keyword, resource="客户",
            ))
            return

        if cust_result.outcome == MatchOutcome.MULTI:
            await self.state.save(dingtalk_userid, {
                "intent_type": "query_customer_history",
                "resource": "客户",
                "candidates": cust_result.choices,
                "sku_or_keyword": sku_or_keyword,  # 后续编号选择继续用
                "pending_choice": "yes",
            })
            card = cards.multi_match_select_card(
                keyword=customer_keyword, resource="客户", items=cust_result.choices,
            )
            await self._send_message(dingtalk_userid, card)
            return

        customer = cust_result.selected

        # 2. 解商品
        try:
            prod_resp = await self.erp.search_products(
                query=sku_or_keyword, acting_as_user_id=acting_as,
            )
        except (ErpPermissionError, CircuitOpenError, ErpSystemError, ErpAdapterError) as e:
            code = (
                BizErrorCode.PERM_DOWNSTREAM_DENIED if isinstance(e, ErpPermissionError)
                else BizErrorCode.ERP_CIRCUIT_OPEN if isinstance(e, CircuitOpenError)
                else BizErrorCode.ERP_TIMEOUT
            )
            await self._send(dingtalk_userid, build_user_message(code))
            return

        prod_candidates = [
            {
                "id": p["id"], "sku": p.get("sku"),
                "name": p.get("name", str(p["id"])),
                "label": p.get("name", str(p["id"])),
                "subtitle": f"SKU {p.get('sku', '-')}",
                "retail_price": p.get("retail_price"),
                "stock": p.get("total_stock"),
            }
            for p in prod_resp.get("items", [])
        ]
        prod_result = self.matcher.resolve(
            keyword=sku_or_keyword, resource="商品",
            candidates=prod_candidates, max_show=self.max_show,
        )

        if prod_result.outcome == MatchOutcome.NONE:
            await self._send(dingtalk_userid, build_user_message(
                BizErrorCode.MATCH_NOT_FOUND, keyword=sku_or_keyword, resource="商品",
            ))
            return

        if prod_result.outcome == MatchOutcome.MULTI:
            await self.state.save(dingtalk_userid, {
                "intent_type": "query_customer_history",
                "resource": "商品",
                "candidates": prod_result.choices,
                "customer_id": customer["id"],
                "customer_name": customer["label"],
                "pending_choice": "yes",
            })
            card = cards.multi_match_select_card(
                keyword=sku_or_keyword, resource="商品", items=prod_result.choices,
            )
            await self._send_message(dingtalk_userid, card)
            return

        product = prod_result.selected
        await self._render_history(
            product=product, customer=customer,
            dingtalk_userid=dingtalk_userid, acting_as=acting_as,
        )

    async def execute_selected_customer(
        self, *, customer: dict, sku_or_keyword: str,
        dingtalk_userid: str, acting_as: int,
    ) -> None:
        """客户多命中后选定一个 → 用 customer dict 直接进入第二步（解商品）。"""
        # 复用 execute 的"解商品 + 渲染"分支，但客户已确定无需再搜
        try:
            prod_resp = await self.erp.search_products(
                query=sku_or_keyword, acting_as_user_id=acting_as,
            )
        except (ErpPermissionError, CircuitOpenError, ErpSystemError, ErpAdapterError) as e:
            code = (
                BizErrorCode.PERM_DOWNSTREAM_DENIED if isinstance(e, ErpPermissionError)
                else BizErrorCode.ERP_CIRCUIT_OPEN if isinstance(e, CircuitOpenError)
                else BizErrorCode.ERP_TIMEOUT
            )
            await self._send(dingtalk_userid, build_user_message(code))
            return

        prod_candidates = [
            {
                "id": p["id"], "sku": p.get("sku"),
                "name": p.get("name", str(p["id"])),
                "label": p.get("name", str(p["id"])),
                "subtitle": f"SKU {p.get('sku', '-')}",
                "retail_price": p.get("retail_price"),
                "stock": p.get("total_stock"),
            }
            for p in prod_resp.get("items", [])
        ]
        prod_result = self.matcher.resolve(
            keyword=sku_or_keyword, resource="商品",
            candidates=prod_candidates, max_show=self.max_show,
        )
        if prod_result.outcome == MatchOutcome.NONE:
            await self._send(dingtalk_userid, build_user_message(
                BizErrorCode.MATCH_NOT_FOUND, keyword=sku_or_keyword, resource="商品",
            ))
            return
        if prod_result.outcome == MatchOutcome.MULTI:
            await self.state.save(dingtalk_userid, {
                "intent_type": "query_customer_history",
                "resource": "商品",
                "candidates": prod_result.choices,
                "customer_id": customer["id"],
                "customer_name": customer["label"],
                "pending_choice": "yes",
            })
            card = cards.multi_match_select_card(
                keyword=sku_or_keyword, resource="商品", items=prod_result.choices,
            )
            await self._send_message(dingtalk_userid, card)
            return
        await self._render_history(
            product=prod_result.selected, customer=customer,
            dingtalk_userid=dingtalk_userid, acting_as=acting_as,
        )

    async def execute_selected_product(
        self, *, product: dict, customer_id: int, customer_name: str,
        dingtalk_userid: str, acting_as: int,
    ) -> None:
        """商品多命中后选定一个 + 客户已确定 → 直接渲染历史价。"""
        await self._render_history(
            product=product,
            customer={"id": customer_id, "label": customer_name},
            dingtalk_userid=dingtalk_userid, acting_as=acting_as,
        )

    async def _render_history(
        self, *, product: dict, customer: dict,
        dingtalk_userid: str, acting_as: int,
    ) -> None:
        """从已确定的 product + customer 渲染历史价卡。

        权限错误向上抛由 handler 翻译；仅系统错误才降级成空 history。
        """
        try:
            info = await self.pricing.get_price(
                product_id=product["id"], customer_id=customer["id"], acting_as=acting_as,
                fallback_retail_price=product.get("retail_price"),
            )
        except ErpPermissionError:
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.PERM_NO_CUSTOMER_HISTORY))
            return

        try:
            history_resp = await self.erp.get_product_customer_prices(
                product_id=product["id"], customer_id=customer["id"], limit=5,
                acting_as_user_id=acting_as,
            )
            history = history_resp.get("records", [])
        except ErpPermissionError:
            await self._send(dingtalk_userid, build_user_message(BizErrorCode.PERM_NO_CUSTOMER_HISTORY))
            return
        except (ErpSystemError, ErpAdapterError):
            history = []  # 系统错才降级到空历史，渲染时仍出零售价

        card = cards.product_with_customer_history_card(
            product=product,
            customer={"id": customer["id"], "name": customer.get("name") or customer.get("label", "")},
            history=history, retail_price=info.unit_price,
        )
        await self._send_message(dingtalk_userid, card)

    async def _send(self, userid: str, text: str) -> None:
        try:
            await self.sender.send_text(dingtalk_userid=userid, text=text)
        except Exception:
            logger.exception(f"send_text 失败 userid={userid}")

    async def _send_message(self, userid: str, msg: OutboundMessage) -> None:
        try:
            if msg.type.value == "text":
                await self.sender.send_text(dingtalk_userid=userid, text=msg.text or "")
            elif msg.type.value == "markdown":
                await self.sender.send_markdown(
                    dingtalk_userid=userid, title="HUB", markdown=msg.markdown or "",
                )
            else:
                await self.sender.send_action_card(
                    dingtalk_userid=userid, actioncard=msg.actioncard or {},
                )
        except Exception:
            logger.exception(f"send 失败 userid={userid}")
```

- [ ] **Step 5: 跑测试 + 提交**

```bash
pytest tests/test_query_product_usecase.py tests/test_query_customer_history_usecase.py -v
git add backend/hub/usecases/ \
        backend/tests/test_query_product_usecase.py \
        backend/tests/test_query_customer_history_usecase.py
git commit -m "feat(hub): QueryProduct + QueryCustomerHistory UseCase（模糊匹配 + 多轮 + 错误降级）"
```

---

## Task 10：Erp4Adapter 接入熔断器 + 历史价超时降级

**Files:**
- Modify: `backend/hub/adapters/downstream/erp4.py`（包装 `_act_as_get` 用 CircuitBreaker；历史价加 3s 超时）
- Modify: `backend/tests/test_erp4_adapter.py`（追加熔断 / 超时降级测试）

- [ ] **Step 1: 修改 Erp4Adapter（搜索参数对齐 + 熔断 + 历史价超时）**

修改 `backend/hub/adapters/downstream/erp4.py`：

(0) **重要：修正 ERP 搜索参数名为 `keyword`**（Plan 3 v6 用的是 `q`，但 ERP-4 实际接口 `backend/app/routers/products.py:39` 与 `customers.py:15` 都是 `keyword` 参数）：

```python
async def search_products(self, query: str, *, acting_as_user_id: int | None) -> dict:
    return await self._act_as_get(
        "/api/v1/products", acting_as_user_id, params={"keyword": query},
    )

async def search_customers(self, query: str, *, acting_as_user_id: int | None) -> dict:
    return await self._act_as_get(
        "/api/v1/customers", acting_as_user_id, params={"keyword": query},
    )
```

(0.5) **新增 `get_product(product_id)` 精确查询**——用于 PricingStrategy 用 ID 精确反查（避免按数字 id 走 keyword 模糊搜索找不到的问题）。Plan 1 ERP `/api/v1/products/{id}` 已有（GET 详情），HUB 这边补一个调用：

```python
async def get_product(self, product_id: int, *, acting_as_user_id: int | None) -> dict:
    return await self._act_as_get(
        f"/api/v1/products/{product_id}", acting_as_user_id,
    )
```

(1) 顶部 import 加：
```python
from hub.circuit_breaker import CircuitBreaker, CircuitOpenError
```

(2) `__init__` 末尾加（**只统计 ErpSystemError**——4xx 业务错不计入避免污染熔断）：
```python
        self._breaker = CircuitBreaker(
            threshold=5, window_seconds=30, open_seconds=60,
            countable_exceptions=(ErpSystemError,),
        )
```

(3) `_act_as_get` / `_system_get` / `_system_post` 三个方法包装：把原来的 try/except 部分挪到一个内部协程，外层用 `await self._breaker.call(...)`：

```python
async def _act_as_get(self, path, acting_as_user_id, params=None):
    if acting_as_user_id is None:
        raise RuntimeError("Erp4Adapter 业务调用必须传 acting_as_user_id（spec §11 模型 Y 强制）")

    async def _do():
        try:
            r = await self._client.get(
                path, headers=self._act_as_headers(acting_as_user_id), params=params,
            )
            self._raise_for_status(r)
            return r.json()
        except httpx.RequestError as e:
            raise ErpSystemError(f"网络错误: {e}")

    return await self._breaker.call(_do)
```

`_system_get` / `_system_post` 同样包装。

(4) `get_product_customer_prices` 单独覆盖 timeout（3s）。spec §13.4 要求"历史价查询 ≤ 3s，超时降级"：

```python
async def get_product_customer_prices(
    self, product_id: int, customer_id: int, limit: int = 5,
    *, acting_as_user_id: int | None,
) -> dict:
    """历史价查询：3s 超时，超时抛 ErpSystemError 由上游降级处理。"""
    if acting_as_user_id is None:
        raise RuntimeError("acting_as_user_id 必填")

    async def _do():
        try:
            r = await self._client.get(
                f"/api/v1/products/{product_id}/customer-prices",
                headers=self._act_as_headers(acting_as_user_id),
                params={"customer_id": customer_id, "limit": limit},
                timeout=3.0,  # 覆盖默认 timeout
            )
            self._raise_for_status(r)
            return r.json()
        except httpx.TimeoutException:
            raise ErpSystemError("历史价查询超时（3s）")
        except httpx.RequestError as e:
            raise ErpSystemError(f"网络错误: {e}")

    return await self._breaker.call(_do)
```

- [ ] **Step 2: 追加测试**

在 `backend/tests/test_erp4_adapter.py` 末尾追加：
```python
@pytest.mark.asyncio
async def test_search_products_uses_keyword_param():
    """ERP 搜索参数必须是 keyword（与 ERP-4 实际接口对齐），不是 q。"""
    from hub.adapters.downstream.erp4 import Erp4Adapter

    captured = {}
    def handler(req):
        captured["url"] = str(req.url)
        return Response(200, json={"items": []})

    adapter = Erp4Adapter(
        base_url="http://x", api_key="k", transport=MockTransport(handler),
    )
    await adapter.search_products(query="SKU100", acting_as_user_id=1)
    assert "keyword=SKU100" in captured["url"]
    assert "q=SKU100" not in captured["url"]


@pytest.mark.asyncio
async def test_search_customers_uses_keyword_param():
    from hub.adapters.downstream.erp4 import Erp4Adapter

    captured = {}
    def handler(req):
        captured["url"] = str(req.url)
        return Response(200, json={"items": []})

    adapter = Erp4Adapter(
        base_url="http://x", api_key="k", transport=MockTransport(handler),
    )
    await adapter.search_customers(query="阿里", acting_as_user_id=1)
    assert "keyword=" in captured["url"]
    # 中文会被 url-encode
    assert "%E9%98%BF%E9%87%8C" in captured["url"] or "阿里" in captured["url"]


@pytest.mark.asyncio
async def test_circuit_opens_after_repeated_failures():
    from hub.adapters.downstream.erp4 import Erp4Adapter
    from hub.circuit_breaker import CircuitOpenError

    def handler(req): return Response(503)
    adapter = Erp4Adapter(
        base_url="http://x", api_key="k", transport=MockTransport(handler),
    )
    # 触发 5 次 5xx → 熔断
    for _ in range(5):
        with pytest.raises(Exception):
            await adapter.search_products(query="x", acting_as_user_id=1)
    # 第 6 次直接 CircuitOpenError
    with pytest.raises(CircuitOpenError):
        await adapter.search_products(query="x", acting_as_user_id=1)


@pytest.mark.asyncio
async def test_customer_prices_timeout_raises_system_error():
    """历史价查询走 3s 超时；mock 慢响应 → 抛 ErpSystemError。"""
    import httpx
    from hub.adapters.downstream.erp4 import Erp4Adapter, ErpSystemError

    def handler(req): raise httpx.TimeoutException("timeout")
    adapter = Erp4Adapter(
        base_url="http://x", api_key="k", transport=MockTransport(handler),
    )
    with pytest.raises(ErpSystemError):
        await adapter.get_product_customer_prices(
            product_id=1, customer_id=2, acting_as_user_id=42,
        )
```

- [ ] **Step 3: 跑测试 + 提交**

```bash
pytest tests/test_erp4_adapter.py -v
git add backend/hub/adapters/downstream/erp4.py backend/tests/test_erp4_adapter.py
git commit -m "feat(hub): Erp4Adapter 接入熔断器 + 历史价 3s 超时"
```

---

## Task 11：dingtalk_inbound handler 接入 ChainParser + UseCase

**Files:**
- Modify: `backend/hub/handlers/dingtalk_inbound.py`（在"已绑定 + ERP 启用"路径接 ChainParser + 路由 UseCase）
- Modify: `backend/worker.py`（构造所有依赖注入到 inbound handler）
- Test: `backend/tests/test_inbound_handler_with_intent.py`

- [ ] **Step 1: 写测试**

文件 `backend/tests/test_inbound_handler_with_intent.py`：
```python
import pytest
from unittest.mock import AsyncMock
from hub.ports import ParsedIntent
from hub.services.identity_service import IdentityResolution


@pytest.mark.asyncio
async def test_rule_query_product_routes_to_query_product_usecase():
    from hub.handlers.dingtalk_inbound import handle_inbound

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    chain = AsyncMock()
    chain.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product",
        fields={"sku_or_keyword": "SKU100", "customer_keyword": None},
        confidence=0.95, parser="rule",
    ))
    query_product = AsyncMock()
    query_customer = AsyncMock()
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)

    binding_svc = AsyncMock()
    sender = AsyncMock()

    payload = {
        "task_id": "t1", "task_type": "dingtalk_inbound",
        "payload": {"channel_userid": "m1", "content": "查 SKU100",
                    "conversation_id": "c1", "timestamp": 1700000000},
    }
    await handle_inbound(
        payload,
        binding_service=binding_svc, identity_service=identity_svc,
        sender=sender,
        chain_parser=chain, conversation_state=state,
        query_product_usecase=query_product,
        query_customer_history_usecase=query_customer,
        require_permissions=AsyncMock(return_value=None),
    )
    query_product.execute.assert_awaited_once()
    args = query_product.execute.call_args.kwargs
    assert args["sku_or_keyword"] == "SKU100"
    assert args["acting_as"] == 42


@pytest.mark.asyncio
async def test_query_customer_history_routes_correctly():
    from hub.handlers.dingtalk_inbound import handle_inbound

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    chain = AsyncMock()
    chain.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_customer_history",
        fields={"sku_or_keyword": "SKU100", "customer_keyword": "阿里"},
        confidence=0.9, parser="rule",
    ))
    query_product = AsyncMock()
    query_customer = AsyncMock()
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)

    payload = {
        "task_id": "t2", "task_type": "dingtalk_inbound",
        "payload": {"channel_userid": "m1", "content": "查 SKU100 给阿里",
                    "conversation_id": "c1", "timestamp": 1700000000},
    }
    await handle_inbound(
        payload,
        binding_service=AsyncMock(), identity_service=identity_svc, sender=AsyncMock(),
        chain_parser=chain, conversation_state=state,
        query_product_usecase=query_product,
        query_customer_history_usecase=query_customer,
        require_permissions=AsyncMock(return_value=None),
    )
    query_customer.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_low_confidence_sends_confirm_card():
    from hub.handlers.dingtalk_inbound import handle_inbound

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    chain = AsyncMock()
    chain.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product",
        fields={"sku_or_keyword": "X"},
        confidence=0.5, parser="llm", notes="low_confidence",
    ))
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)
    sender = AsyncMock()

    payload = {
        "task_id": "t3", "task_type": "dingtalk_inbound",
        "payload": {"channel_userid": "m1", "content": "嗯帮我看看那个东西",
                    "conversation_id": "c1", "timestamp": 1700000000},
    }
    await handle_inbound(
        payload, binding_service=AsyncMock(), identity_service=identity_svc,
        sender=sender, chain_parser=chain, conversation_state=state,
        query_product_usecase=AsyncMock(),
        query_customer_history_usecase=AsyncMock(),
        require_permissions=AsyncMock(return_value=None),
    )
    sender.send_text.assert_awaited_once()
    state.save.assert_awaited_once()
    saved = state.save.call_args.args[1]
    assert saved.get("pending_confirm") == "yes"


@pytest.mark.asyncio
async def test_select_choice_with_pending_state():
    """用户回 "2" 时取上次保存的候选项进入对应 use case。"""
    from hub.handlers.dingtalk_inbound import handle_inbound

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    chain = AsyncMock()
    chain.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="select_choice", fields={"choice": 2},
        confidence=0.95, parser="rule",
    ))
    state = AsyncMock()
    state.load = AsyncMock(return_value={
        "intent_type": "query_product",
        "resource": "商品",
        "candidates": [
            {"id": 1, "label": "A", "retail_price": "100"},
            {"id": 2, "label": "B", "retail_price": "200"},
        ],
        "pending_choice": "yes",
    })
    sender = AsyncMock()
    qp = AsyncMock()

    payload = {
        "task_id": "t4", "task_type": "dingtalk_inbound",
        "payload": {"channel_userid": "m1", "content": "2",
                    "conversation_id": "c1", "timestamp": 1700000000},
    }
    await handle_inbound(
        payload, binding_service=AsyncMock(), identity_service=identity_svc,
        sender=sender, chain_parser=chain, conversation_state=state,
        query_product_usecase=qp,
        query_customer_history_usecase=AsyncMock(),
        require_permissions=AsyncMock(return_value=None),
    )
    # 选定后清除 state
    state.clear.assert_awaited_once_with("m1")
    # 关键：调 execute_selected（用确定的候选项），不调 execute（不二次模糊搜索）
    qp.execute_selected.assert_awaited_once()
    qp.execute.assert_not_called()
    args = qp.execute_selected.call_args.kwargs
    assert args["product"]["id"] == 2  # 候选项的第 2 项


@pytest.mark.asyncio
async def test_permission_error_translates():
    from hub.handlers.dingtalk_inbound import handle_inbound
    from hub.error_codes import BizError, BizErrorCode

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    chain = AsyncMock()
    chain.parse = AsyncMock(return_value=ParsedIntent(
        intent_type="query_product", fields={"sku_or_keyword": "X"},
        confidence=0.95, parser="rule",
    ))

    require_permissions = AsyncMock(side_effect=BizError(BizErrorCode.PERM_NO_PRODUCT_QUERY))
    state = AsyncMock()
    state.load = AsyncMock(return_value=None)
    sender = AsyncMock()

    payload = {
        "task_id": "t5", "task_type": "dingtalk_inbound",
        "payload": {"channel_userid": "m1", "content": "查 X",
                    "conversation_id": "c1", "timestamp": 1700000000},
    }
    await handle_inbound(
        payload, binding_service=AsyncMock(), identity_service=identity_svc,
        sender=sender, chain_parser=chain, conversation_state=state,
        query_product_usecase=AsyncMock(),
        query_customer_history_usecase=AsyncMock(),
        require_permissions=require_permissions,
    )
    sender.send_text.assert_awaited_once()
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "权限" in sent
```

- [ ] **Step 2: 修改 dingtalk_inbound handler**

修改 `backend/hub/handlers/dingtalk_inbound.py`，重写"已绑定 + 启用"路径以接业务路由：

```python
"""钉钉入站消息 task handler。"""
from __future__ import annotations
import re
import logging
from hub import messages
from hub.error_codes import BizError, build_user_message, BizErrorCode

logger = logging.getLogger("hub.handler.dingtalk_inbound")


RE_BIND = re.compile(r"^/?绑定\s+(\S+)\s*$")
RE_UNBIND = re.compile(r"^/?解绑\s*$")
RE_HELP = re.compile(r"^/?(help|帮助|\?|菜单)\s*$", re.IGNORECASE)


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
) -> None:
    payload = task_data.get("payload", {})
    channel_userid = payload.get("channel_userid", "")
    content = (payload.get("content") or "").strip()

    # 1. 绑定/解绑/帮助命令（不需要 IdentityService）
    m_bind = RE_BIND.match(content)
    if m_bind:
        result = await binding_service.initiate_binding(
            dingtalk_userid=channel_userid, erp_username=m_bind.group(1),
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
            "查 SKU100 — 查商品",
            "查 SKU100 给阿里 — 查客户历史价",
        ]
        await _send_text(sender, channel_userid, messages.help_message(cmds))
        return

    # 2. 解析身份 + 检查 ERP 启用
    resolution = await identity_service.resolve(dingtalk_userid=channel_userid)
    if not resolution.found:
        await _send_text(sender, channel_userid,
                         build_user_message(BizErrorCode.USER_NOT_BOUND))
        return
    if not resolution.erp_active:
        await _send_text(sender, channel_userid,
                         build_user_message(BizErrorCode.USER_ERP_DISABLED))
        return

    # 3. 进入业务用例（需 chain_parser 等依赖；Plan 4 后才有，Plan 3 时这些是 None）
    if chain_parser is None:
        await _send_text(sender, channel_userid,
                         "我没听懂，请发送「帮助」查看可用功能。")
        return

    # 取多轮上下文
    state = await conversation_state.load(channel_userid) if conversation_state else None
    parser_context = {}
    if state:
        if state.get("pending_choice"):
            parser_context["pending_choice"] = "yes"
        if state.get("pending_confirm"):
            parser_context["pending_confirm"] = "yes"

    intent = await chain_parser.parse(content, context=parser_context)

    # 4. 路由 + 权限校验
    try:
        if intent.intent_type == "select_choice":
            await _handle_select_choice(
                intent, state, channel_userid, sender,
                conversation_state, resolution,
                query_product_usecase, query_customer_history_usecase,
                require_permissions,
            )
            return

        if intent.intent_type == "confirm_yes":
            # 确认上次低置信度的解析结果，按解析过的 intent 执行
            if state and state.get("pending_confirm"):
                await _execute_confirmed(
                    state, channel_userid, sender, conversation_state, resolution,
                    query_product_usecase, query_customer_history_usecase,
                    require_permissions,
                )
            else:
                await _send_text(sender, channel_userid,
                                 "没有需要确认的待办；请重新描述你的需求。")
            return

        if intent.intent_type == "unknown":
            await _send_text(sender, channel_userid,
                             build_user_message(BizErrorCode.INTENT_LOW_CONFIDENCE))
            return

        if intent.notes == "low_confidence":
            # 保存上下文，让用户回 "是" 确认
            await conversation_state.save(channel_userid, {
                "intent_type": intent.intent_type,
                "fields": intent.fields,
                "pending_confirm": "yes",
            })
            summary = _summarize_intent(intent)
            await _send_text(sender, channel_userid,
                             f"我大概理解为：{summary}\n\n如果是这个意思请回复「是」继续，否则请用更明确的方式重新描述。")
            return

        # 高置信度直接执行
        await _execute_intent(
            intent, channel_userid, sender, resolution,
            query_product_usecase, query_customer_history_usecase,
            require_permissions,
        )
    except BizError as e:
        await _send_text(sender, channel_userid, str(e))


async def _execute_intent(
    intent, channel_userid, sender, resolution,
    query_product, query_customer, require_permissions,
):
    if intent.intent_type == "query_product":
        await require_permissions(resolution.hub_user_id, [
            "channel.dingtalk.use", "downstream.erp.use", "usecase.query_product.use",
        ])
        await query_product.execute(
            sku_or_keyword=intent.fields["sku_or_keyword"],
            dingtalk_userid=channel_userid, acting_as=resolution.erp_user_id,
        )
    elif intent.intent_type == "query_customer_history":
        await require_permissions(resolution.hub_user_id, [
            "channel.dingtalk.use", "downstream.erp.use",
            "usecase.query_customer_history.use",
        ])
        await query_customer.execute(
            sku_or_keyword=intent.fields["sku_or_keyword"],
            customer_keyword=intent.fields["customer_keyword"],
            dingtalk_userid=channel_userid, acting_as=resolution.erp_user_id,
        )
    else:
        await _send_text(sender, channel_userid,
                         build_user_message(BizErrorCode.INTENT_LOW_CONFIDENCE))


async def _handle_select_choice(
    intent, state, channel_userid, sender, conversation_state, resolution,
    query_product, query_customer, require_permissions,
):
    if not state or not state.get("candidates"):
        await _send_text(sender, channel_userid, "没有需要选择的待办；请重新描述你的需求。")
        return

    choice = intent.fields.get("choice", 0)
    candidates = state["candidates"]
    if not (1 <= choice <= len(candidates)):
        await _send_text(sender, channel_userid, "编号超出范围，请重新输入。")
        return

    selected = candidates[choice - 1]
    await conversation_state.clear(channel_userid)

    # 根据上次保存的 intent_type + 已确定的候选项**直接渲染**（不二次模糊搜索）
    if state.get("intent_type") == "query_product":
        await require_permissions(resolution.hub_user_id, [
            "channel.dingtalk.use", "downstream.erp.use", "usecase.query_product.use",
        ])
        await query_product.execute_selected(
            product=selected,
            dingtalk_userid=channel_userid, acting_as=resolution.erp_user_id,
        )
    elif state.get("intent_type") == "query_customer_history":
        await require_permissions(resolution.hub_user_id, [
            "channel.dingtalk.use", "downstream.erp.use",
            "usecase.query_customer_history.use",
        ])
        if state.get("resource") == "客户":
            # 选定客户 → 用确定的 customer 进入第二步（解商品）
            await query_customer.execute_selected_customer(
                customer=selected,
                sku_or_keyword=state["sku_or_keyword"],
                dingtalk_userid=channel_userid, acting_as=resolution.erp_user_id,
            )
        elif state.get("resource") == "商品":
            # 客户 + 商品都确定 → 直接渲染
            await query_customer.execute_selected_product(
                product=selected,
                customer_id=state["customer_id"],
                customer_name=state["customer_name"],
                dingtalk_userid=channel_userid, acting_as=resolution.erp_user_id,
            )


async def _execute_confirmed(
    state, channel_userid, sender, conversation_state, resolution,
    query_product, query_customer, require_permissions,
):
    from hub.ports import ParsedIntent
    intent = ParsedIntent(
        intent_type=state["intent_type"],
        fields=state.get("fields", {}),
        confidence=1.0, parser="confirmed",
    )
    await conversation_state.clear(channel_userid)
    await _execute_intent(
        intent, channel_userid, sender, resolution,
        query_product, query_customer, require_permissions,
    )


def _summarize_intent(intent) -> str:
    if intent.intent_type == "query_product":
        return f"查商品 {intent.fields.get('sku_or_keyword', '')}"
    if intent.intent_type == "query_customer_history":
        return (f"查 {intent.fields.get('sku_or_keyword', '')} "
                f"给客户「{intent.fields.get('customer_keyword', '')}」的历史价")
    return "未知操作"


async def _send_text(sender, userid: str, text: str) -> None:
    try:
        await sender.send_text(dingtalk_userid=userid, text=text)
    except Exception:
        logger.exception(f"send_text 失败 userid={userid}")
```

- [ ] **Step 3: 跑测试**

```bash
pytest tests/test_inbound_handler_with_intent.py tests/test_dingtalk_inbound_handler.py -v
```
期望：Plan 4 新增 5 测试 + Plan 3 原 6 测试 全 PASS。

- [ ] **Step 4: 提交**

```bash
git add backend/hub/handlers/dingtalk_inbound.py \
        backend/tests/test_inbound_handler_with_intent.py
git commit -m "feat(hub): inbound handler 接入 ChainParser + UseCase 路由 + 多轮选编号 + 低置信度确认"
```

---

## Task 12：worker.py 注入所有新依赖 + 端到端验证

**Files:**
- Modify: `backend/worker.py`
- Test: 端到端手工验证

- [ ] **Step 1: 修改 worker.py 构造业务依赖**

修改 `backend/worker.py`，在 `binding_service = BindingService(...)` 之后追加：

```python
    # 业务依赖（Plan 4）
    from hub.intent.rule_parser import RuleParser
    from hub.intent.llm_parser import LLMParser
    from hub.intent.chain_parser import ChainParser
    from hub.match.conversation_state import ConversationStateRepository
    from hub.strategy.pricing import DefaultPricingStrategy
    from hub.usecases.query_product import QueryProductUseCase
    from hub.usecases.query_customer_history import QueryCustomerHistoryUseCase
    from hub.capabilities.factory import load_active_ai_provider
    from hub.permissions import require_permissions

    ai_provider = await load_active_ai_provider()
    chain_parser = ChainParser(
        rule=RuleParser(), llm=LLMParser(ai=ai_provider),
        low_confidence_threshold=0.7,
    )
    conversation_state = ConversationStateRepository(redis=redis_client, ttl_seconds=300)
    pricing = DefaultPricingStrategy(erp_adapter=erp_adapter)
    query_product = QueryProductUseCase(
        erp=erp_adapter, pricing=pricing, sender=sender, state=conversation_state,
    )
    query_customer = QueryCustomerHistoryUseCase(
        erp=erp_adapter, pricing=pricing, sender=sender, state=conversation_state,
    )
```

修改 `dingtalk_inbound_handler` 把所有依赖传进去：

```python
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
        )
```

`finally` 块加 `if ai_provider: await ai_provider.aclose()`。

- [ ] **Step 2: 端到端手工验证**

```bash
cd /Users/lin/Desktop/hub
docker compose up -d --build
sleep 8

# 1. 完成初始化向导（在 admin 手工写入或 Plan 5 UI）：
#    - DownstreamSystem(erp + apikey)
#    - ChannelApp(dingtalk + appkey/secret)
#    - AIProvider(deepseek 或 qwen + apikey)

# 2. 用钉钉测试组织成员发以下消息（已绑定状态）
#    - "查 SKU100" → 收到商品卡片（含系统零售价）
#    - "查 SKU100 给阿里" → 收到商品 + 客户历史价
#    - "查 阿里" → 多客户匹配，收到选编号卡片
#    - 回 "1" → 收到对应客户/商品的卡片
#    - "帮我看看那个东西" → 收到低置信度确认卡
#    - "是" → 按解析结果继续
```

- [ ] **Step 3: 验证记录**

文件 `docs/superpowers/plans/notes/2026-04-27-plan4-end-to-end-verification.md`：
```markdown
# Plan 4 端到端验证记录

日期：____ / 执行人：____

## 单测验证（合计 81 PASS）
1. test_error_codes.py：3
2. test_permissions.py：4
3. test_rule_parser.py：6
4. test_deepseek_provider.py：4
5. test_qwen_provider.py：3
6. test_llm_parser.py：9（含缺必填字段降级 3 个 + confidence 非数字降级）
7. test_chain_parser.py：4
8. test_conversation_state.py：4
9. test_match_resolver.py：6
10. test_pricing_strategy.py：7（含历史价 403 上抛）
11. test_erp_breaker.py：6（含 countable_exceptions）
12. test_query_product_usecase.py：8（含 execute_selected 渲染 + fallback_retail_price 透传）
13. test_query_customer_history_usecase.py：8（含历史价 403 → PERM 翻译）
14. test_inbound_handler_with_intent.py：5
15. test_erp4_adapter.py 追加：4
合计：80 PASS

## 端到端
- 查 SKU100 → 卡片：✅/❌
- 查 SKU100 给阿里 → 历史价卡：✅/❌
- 多商品/客户 → 选编号 → 命中：✅/❌
- 自然语言 → AI 解析 → 高置信度直接执行：✅/❌
- 自然语言 → 低置信度确认卡 → "是" → 执行：✅/❌
- 无权限用户 → 中文文案拒绝：✅/❌
- ERP 5xx 5 次 → 熔断 → 友好提示：✅/❌
- 历史价 3s 超时 → 降级到零售价：✅/❌

## 已知缺口（Plan 5 处理）
- 完整 Web 后台对话监控
- AI 提供商管理 UI
- 任务流水查询 UI
- cron 调度器（每日巡检）
```

```bash
git add docs/superpowers/plans/notes/
git add backend/worker.py
git commit -m "feat(hub): worker 注入业务用例依赖 + Plan 4 端到端验证记录"
```

---

## Self-Review（v4，应用第三轮 review 反馈后）

### Spec 覆盖检查

| Spec 章节 | Plan 任务 | ✓ |
|---|---|---|
| §5.4 IntentParser | Task 3 RuleParser + Task 5 LLMParser/ChainParser | ✓ |
| §5.6 PricingStrategy | Task 7 DefaultPricingStrategy | ✓ |
| §5.3 CapabilityProvider | Task 4 DeepSeek + Qwen + factory | ✓ |
| §6.2 ConversationStateRepository（Redis） | Task 6 | ✓ |
| §13.2 重试 + §13.4 ERP 故障降级 + 熔断 | Task 8 + Task 10 | ✓ |
| §19.1 错误码 20 条 | Task 1 error_codes.py | ✓ |
| 业务用例：查商品 / 查客户历史价 | Task 9 | ✓ |
| AI fallback（DeepSeek 默认 + Qwen 备选） | Task 4 + factory + Task 5 | ✓ |
| 模糊匹配（unique/multi/none）+ 多轮选编号 | Task 6 + Task 9 | ✓ |
| HUB 权限聚合（usecase/downstream/channel） | Task 2 | ✓ |
| UI 大白话原则（错误码 / 卡片） | Task 1 messages + cards | ✓ |

### Placeholder Scan

- ✓ 无 "TODO" / "TBD"
- ✓ 所有测试有完整代码
- ✓ Plan 3 inbound handler 中 `chain_parser=None` 时降级为占位提示，向后兼容（Plan 4 启用后注入即生效）

### 类型一致性

- ✓ ParsedIntent 字段（intent_type / fields / confidence / parser / notes）跨文件一致
- ✓ MatchOutcome 三态（unique/multi/none）使用一致
- ✓ Decimal 全程用 str（不用 float）—— pricing / cards / 测试断言一致
- ✓ ErpAdapterError 子类（ErpPermissionError / ErpSystemError / ErpNotFoundError）翻译为 BizErrorCode 一致

### 范围检查

Plan 4 完成后达到：
- ✅ 用户在钉钉发"查 SKU"→ 收到商品卡片
- ✅ 发"查 SKU 给客户"→ 收到历史价卡片
- ✅ 多命中→ 文本编号列表 → 用户回复编号 → 命中
- ✅ 自然语言 → AI 解析（DeepSeek / Qwen）
- ✅ 低置信度 → 确认卡片
- ✅ 无权限 → 中文文案拒绝
- ✅ ERP 5xx / 超时 → 熔断 + 降级
- ❌ 无 Web 后台监控（Plan 5）
- ❌ cron 调度器（Plan 5）

### 与 Plan 1 / 2 / 3 的接口

- 调 ERP `search_products` / `search_customers` / `get_product_customer_prices`：Plan 1 已实现接收方
- 用 `Erp4Adapter`（强制 X-Acting-As-User-Id）：Plan 3 已实现，Plan 4 增加熔断
- 用 `IdentityService.resolve`：Plan 3 已实现
- 用 `DingTalkSender.send_text/send_markdown`：Plan 3 已实现
- 用 `RedisStreamsRunner`（任务调度）：Plan 2 已实现
- ConversationState 用 Plan 2 已暴露的 redis_client

### 与 Plan 5 的预留

- Web 后台对话监控查 `task_log`（Plan 2 已建表，handler 内部应填充 task_log 写入；本 plan 暂未显式做—— Plan 5 加观察性增强）
- AI 提供商管理 UI 复用 `ai_provider` 表（Plan 2 已建）+ factory（本 plan 已建）
- 任务流水查询用 `task_log` / `task_payload`（Plan 2 已建）

---

### v2 第一轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-V2-A | ERP 搜索参数 `q` 与实际 ERP-4 接口不匹配（实际是 `keyword`） | Task 10 Step 1 (0) 修正 `Erp4Adapter.search_products` / `search_customers` 改为 `params={"keyword": query}`；新增 `test_search_products_uses_keyword_param` / `test_search_customers_uses_keyword_param` 两个测试断言 url 含 `keyword=` |
| P1-V2-B | 多轮选编号后又重新模糊搜索，可能选错或 0 命中 | UseCase 增 `execute_selected` / `execute_selected_customer` / `execute_selected_product` 三个方法用确定的 dict 直接渲染；inbound handler `_handle_select_choice` 改为调 `execute_selected_*`；select_choice 测试断言 `qp.execute_selected.assert_awaited_once()` + `qp.execute.assert_not_called()` + 候选项 id 匹配 |
| P1-V2-C | 熔断器把 4xx 业务错也计入 ERP 故障 | CircuitBreaker 加 `countable_exceptions` 参数（默认 None=向后兼容；生产传 `(ErpSystemError,)`）；Erp4Adapter `__init__` 用 `countable_exceptions=(ErpSystemError,)`；新增 `test_only_countable_exceptions_count` 验证业务错不熔断、系统错才熔断 |
| P2-V2-D | 零售价 fallback 用 search_products(数字 id) 不可靠 | 新增 `Erp4Adapter.get_product(product_id)` 精确反查；PricingStrategy `get_price` 增 `fallback_retail_price` 参数（调用方传入候选项已含的 retail_price 优先用）；UseCase `_render_unique` / `_render_history` 把 prod.retail_price 透传；新增/调整 PricingStrategy 测试 |
| P2-V2-E | ActionCard 目标与实现不一致 | Goal 节加"消息形态说明"明确 Plan 4 全部用 TEXT，不实现 ActionCard 富按钮；Task 1 标题改"文本卡片模板"；保留 cards.py 函数命名（语义性"卡片"=格式化文本） |
| P3-V2-F | 测试统计漏算 Erp4Adapter 追加 | 顶部测试表加一行 `test_erp4_adapter.py（追加） 4`；合计 67→71；erp_breaker 测试 5→6（含新增 countable_exceptions 测试）；Task 12 验证记录同步 |

---

### v3 第二轮 review 修复清单

| # | 反馈 | 修复 |
|---|---|---|
| P1-V3-A | 候选商品丢了 name/库存字段，渲染会 KeyError | UseCase 投影 candidates 时保留 `name` / `stock=p.get("total_stock")` 完整字段（products + customer_history 两处都改）；新增 `test_execute_selected_renders_card_with_name_and_stock` 测试断言渲染含 name + stock + price；新增 `test_execute_selected_passes_fallback_retail_to_pricing` |
| P2-V3-B | 历史价权限错误被降级成零售价/空历史 | PricingStrategy `get_price` 改为：`ErpPermissionError` **向上抛**，仅 `ErpSystemError` 降级；`_render_history` 在 pricing.get_price 和 get_product_customer_prices 两处分别捕获 `ErpPermissionError` → 翻译 `PERM_NO_CUSTOMER_HISTORY`；新增 `test_history_403_raises_not_falls_back` + `test_history_price_403_translates_to_perm_message` |
| P2-V3-C | LLM 返回缺字段会让 handler KeyError | LLMParser 加 `_REQUIRED_FIELDS` 映射；缺必填字段返回 `_unknown()`；fields 非 dict 也降级；新增 3 个测试覆盖（缺 sku_or_keyword / 缺 customer_keyword / fields 非 dict） |
| P3-V3-D | 测试统计/ActionCard 文案残留 | 顶部测试表数量同步实际：llm 5→8 / pricing 5→7 / query_product 6→8 / query_customer_history 7→8；合计 71→80；Architecture / 文件结构 / Step 3 标题 / commit msg / 验收口径全部把"ActionCard"改"文本卡片/编号列表" |

---

### v4 第三轮 review 修复清单（AI fallback 边界）

| # | 反馈 | 修复 |
|---|---|---|
| P2-V4-A | LLM 返回 `confidence` 非数字（None / "high" / 字符串）会让 `float(...)` 抛 TypeError，整条入站任务失败 | LLMParser 把 `float(raw.get("confidence", 0.0))` 包进 try/except (TypeError, ValueError)；失败 → confidence=0.0 + warning 日志（不抛异常）；新增 `test_llm_parser_confidence_non_numeric_falls_back_to_zero` 覆盖 None / "high" / "0.8x" / [] / dict 五种非数字输入 |

---

**Plan 4 v4 结束（已修复 v1 + v2 + v3 + v4 四轮 review 反馈，共 11 处问题）**
