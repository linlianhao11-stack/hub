"""ToolRegistry + ConfirmGate + EntityExtractor 测试（Task 2, 25+ case）。

依赖：
- 真 redis（localhost:6380/0）跑 Lua 脚本（mock 走不了原子语义）
- hub.permissions.has_permission / require_permissions 用 patch mock
- SessionMemory 用 AsyncMock
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from hub.agent.tools.confirm_gate import ConfirmGate
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import (
    ClaimFailedError,
    MissingConfirmationError,
    ToolArgsValidationError,
    ToolRegistrationError,
    ToolType,
    UnconfirmedWriteToolError,
)
from hub.error_codes import BizError

# ====== Fixtures ======

@pytest.fixture
async def redis_client():
    """真 redis 客户端（decode_responses=True 让 Lua 返回 str）。"""
    import redis.asyncio as redis_async
    client = redis_async.Redis.from_url("redis://localhost:6380/0", decode_responses=True)
    yield client
    # 清掉测试期间产生的 hub:agent:* keys 防测试间污染
    async for key in client.scan_iter("hub:agent:*"):
        await client.delete(key)
    await client.aclose()


@pytest.fixture
async def confirm_gate(redis_client):
    return ConfirmGate(redis_client)


@pytest.fixture
def session_memory():
    """SessionMemory mock：自动 AsyncMock。"""
    mock = AsyncMock()
    mock.get_entity_refs = AsyncMock(return_value=None)
    mock.add_entity_refs = AsyncMock()
    return mock


@pytest.fixture
async def reg(confirm_gate, session_memory):
    return ToolRegistry(confirm_gate=confirm_gate, session_memory=session_memory)


# ====== 测试辅助工具函数 ======

async def _fake_voucher_fn(amount: int, *, hub_user_id, conversation_id,
                           confirmation_action_id: str, **_):
    """写类 tool 测试桩，声明了必须的 confirmation_action_id 参数。"""
    return {"draft_id": 1, "amount": amount}


async def _confirm_one(reg, conversation_id, hub_user_id, tool_name, args):
    """模拟 ChainAgent 把单条 pending 标 confirmed；返 (action_id, token)。"""
    await reg.confirm_gate.add_pending(conversation_id, hub_user_id, tool_name, args)
    confirmed = await reg.confirm_gate.confirm_all_pending(conversation_id, hub_user_id)
    a = confirmed[-1]
    return a["action_id"], a["token"]


def _make_perm_patch(allow: bool = True):
    """创建 has_permission / require_permissions 的 mock patch context manager。"""
    return patch("hub.agent.tools.registry.has_permission", AsyncMock(return_value=allow)), \
           patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None) if allow
                 else AsyncMock(side_effect=BizError("PERM_DOWNSTREAM_DENIED")))


# ====== schema 自动生成 + 权限过滤（4 case）======

@pytest.mark.asyncio
async def test_register_extracts_openai_schema(reg):
    """注册后 schema_for_user 返回 OpenAI function schema 格式。"""
    async def search_products(query: str, limit: int = 10) -> list[dict]:
        """搜索商品。

        Args:
            query: 搜索关键字
            limit: 最大返回数量
        """
        return []

    reg.register("search_products", search_products,
                 perm="usecase.query_product.use",
                 tool_type=ToolType.READ,
                 description="搜索商品列表")

    with patch("hub.agent.tools.registry.has_permission", AsyncMock(return_value=True)):
        schema = await reg.schema_for_user(hub_user_id=1)

    assert len(schema) == 1
    fn_schema = schema[0]["function"]
    assert fn_schema["name"] == "search_products"
    assert fn_schema["description"] == "搜索商品列表"
    params = fn_schema["parameters"]
    assert params["required"] == ["query"]
    assert "query" in params["properties"]
    assert params["properties"]["query"]["type"] == "string"
    assert "limit" in params["properties"]
    assert params["properties"]["limit"]["type"] == "integer"
    # schema 中不含内部 ctx 参数
    assert "hub_user_id" not in params["properties"]
    assert "confirmation_action_id" not in params["properties"]
    assert "confirmation_token" not in params["properties"]


@pytest.mark.asyncio
async def test_register_handles_plain_dict_and_list_types(reg):
    """钉钉实测 hotfix（task=aFzEpPml 13:07）：plain `dict` / `list` /
    `dict | None` 不能落 string fallback —— generate_contract_draft
    签名 `extras: dict | None = None` 之前推断成 string，contract subgraph
    传 `extras={}` 触发 ToolArgsValidationError: '{}' is not of type 'string'。
    """
    async def fake_tool(
        items_arr: list,                    # plain list → array
        ctx_dict: dict,                     # plain dict → object
        opt_dict: dict | None = None,       # dict | None → object
        opt_list: list | None = None,       # list | None → array
        amount: float = 0.0,
    ) -> dict:
        """fake."""
        return {}

    from hub.agent.tools.types import ToolType
    reg.register("fake_tool", fake_tool,
                 perm="usecase.test.use",
                 tool_type=ToolType.READ,
                 description="测试 plain 类型推断")

    with patch("hub.agent.tools.registry.has_permission", AsyncMock(return_value=True)):
        schemas = await reg.schema_for_user(hub_user_id=1)

    props = schemas[0]["function"]["parameters"]["properties"]
    assert props["items_arr"]["type"] == "array", "plain list 应推断为 array"
    assert props["ctx_dict"]["type"] == "object", "plain dict 应推断为 object（不是 string fallback）"
    assert props["opt_dict"]["type"] == "object", "dict | None 应推断为 object"
    assert props["opt_list"]["type"] == "array", "list | None 应推断为 array"
    assert props["amount"]["type"] == "number"


@pytest.mark.asyncio
async def test_register_decimal_type_returns_number(reg):
    """Decimal 应推断成 number（合同 / 报价单的 price 字段都是 Decimal）。"""
    from hub.agent.tools.types import ToolType
    async def fake_dec_tool(amount: Decimal) -> dict:
        """f."""
        return {}
    reg.register("fake_dec", fake_dec_tool,
                 perm="usecase.test.use",
                 tool_type=ToolType.READ,
                 description="d")
    with patch("hub.agent.tools.registry.has_permission", AsyncMock(return_value=True)):
        schemas = await reg.schema_for_user(hub_user_id=1)
    assert schemas[0]["function"]["parameters"]["properties"]["amount"]["type"] == "number"


@pytest.mark.asyncio
async def test_schema_for_user_filters_by_permission(reg):
    """没权限的 tool 不在返回列表里。"""
    async def search_fn(query: str) -> list: return []
    async def admin_fn(user_id: int) -> dict: return {}

    reg.register("search_products", search_fn,
                 perm="usecase.query_product.use",
                 tool_type=ToolType.READ, description="搜索商品")
    reg.register("admin_op", admin_fn,
                 perm="usecase.admin.use",
                 tool_type=ToolType.READ, description="管理操作")

    # 第一个 tool 有权，第二个没权
    async def fake_has_perm(user_id, perm):
        return perm == "usecase.query_product.use"

    with patch("hub.agent.tools.registry.has_permission", fake_has_perm):
        schema = await reg.schema_for_user(hub_user_id=1)

    names = [s["function"]["name"] for s in schema]
    assert "search_products" in names
    assert "admin_op" not in names


@pytest.mark.asyncio
async def test_call_checks_permission(reg):
    """call() 调用前先 require_permissions，缺权限抛 BizError。"""
    async def search_fn(query: str) -> list: return []
    reg.register("search_products", search_fn,
                 perm="usecase.query_product.use",
                 tool_type=ToolType.READ, description="搜索商品")

    with patch("hub.agent.tools.registry.require_permissions",
               AsyncMock(side_effect=BizError("PERM_DOWNSTREAM_DENIED"))):
        with pytest.raises(BizError):
            await reg.call("search_products", {"query": "苹果"},
                           hub_user_id=1, acting_as=2,
                           conversation_id="c1", round_idx=0)


@pytest.mark.asyncio
async def test_call_handles_tool_exception(reg):
    """tool 抛错 → 向上传播（ToolCallLog 记 error）。"""
    async def buggy_fn(query: str) -> list:
        raise RuntimeError("ERP 超时")

    reg.register("search_products", buggy_fn,
                 perm="usecase.query_product.use",
                 tool_type=ToolType.READ, description="搜索商品")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        with pytest.raises(RuntimeError, match="ERP 超时"):
            await reg.call("search_products", {"query": "苹果"},
                           hub_user_id=1, acting_as=2,
                           conversation_id="c1", round_idx=0)


# ====== 写门禁硬校验 ======

@pytest.mark.asyncio
async def test_write_tool_without_confirmation_token_raises(reg):
    """无 confirmation_action_id / confirmation_token → MissingConfirmationError。"""
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT,
                 description="创建凭证草稿")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        with pytest.raises(MissingConfirmationError):
            await reg.call("create_voucher_draft", {"amount": 1000},
                           hub_user_id=1, acting_as=2,
                           conversation_id="c1", round_idx=0)


@pytest.mark.asyncio
async def test_write_tool_with_wrong_token_raises(reg):
    """confirmation_token 不匹配 → ClaimFailedError；合法调用方 token 不被污染。"""
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        action_id, _ = await _confirm_one(reg, "c1", 1, "create_voucher_draft", {"amount": 1000})

        # 用错的 token
        with pytest.raises(ClaimFailedError):
            await reg.call("create_voucher_draft", {
                "amount": 1000,
                "confirmation_action_id": action_id,
                "confirmation_token": "x" * 32,
            }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)

        # 合法 token 还在（pending 也还在，因为 restore 把它还回去了）
        pending_after = await reg.confirm_gate.list_pending("c1", 1)
        assert any(p["action_id"] == action_id for p in pending_after)


@pytest.mark.asyncio
async def test_write_tool_with_args_changed_after_confirm_raises(reg):
    """用户确认 args A → LLM 偷偷改成 args B → claim 校验 args 不一致 → 拦截 + restore。"""
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft", {"amount": 1000})

        # LLM 偷偷把 amount 改成 9999
        with pytest.raises(ClaimFailedError):
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


@pytest.mark.asyncio
async def test_write_tool_with_correct_token_passes(reg):
    """正确 (action_id, token) + 一致 args → 通过。"""
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft", {"amount": 1000})
        result = await reg.call("create_voucher_draft", {
            "amount": 1000,
            "confirmation_action_id": action_id,
            "confirmation_token": token,
        }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)
        assert result is not None
        assert result["draft_id"] == 1


@pytest.mark.asyncio
async def test_write_tool_token_is_one_time_use(reg):
    """同 (action_id, token) 第二次调用被拒（claim 已原子 HDEL）。"""
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft", {"amount": 1000})
        payload = {
            "amount": 1000,
            "confirmation_action_id": action_id,
            "confirmation_token": token,
        }

        # 第一次：成功
        result1 = await reg.call("create_voucher_draft", payload,
                                  hub_user_id=1, acting_as=2,
                                  conversation_id="c1", round_idx=0)
        assert result1 is not None

        # 第二次（同 action_id+token）：claim_action 返 None → 拦截
        with pytest.raises(ClaimFailedError):
            await reg.call("create_voucher_draft", payload,
                           hub_user_id=1, acting_as=2,
                           conversation_id="c1", round_idx=1)


@pytest.mark.asyncio
async def test_write_tool_token_preserved_when_tool_fails(reg):
    """tool fn 抛异常时 restore_action 还原 confirmed → 用户重试用同 token 还能成功。"""
    counter = {"n": 0}

    async def sometimes_flaky(amount: int, *, hub_user_id, conversation_id,
                               confirmation_action_id: str, **_):
        counter["n"] += 1
        if counter["n"] == 1:
            raise RuntimeError("ERP 5xx")
        return {"draft_id": 99}

    reg.register("create_voucher_draft", sometimes_flaky,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft", {"amount": 1000})
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


@pytest.mark.asyncio
async def test_write_tool_concurrent_claim_executes_only_once(reg):
    """asyncio.gather 同 (action_id, token) N 个并发，只有 1 个 tool.fn 跑。"""
    counter = {"n": 0}

    async def slow_fn(amount: int, *, hub_user_id, conversation_id,
                      confirmation_action_id: str, **_):
        counter["n"] += 1
        await asyncio.sleep(0.05)  # 给并发窗口
        return {"draft_id": counter["n"]}

    reg.register("create_voucher_draft", slow_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft", {"amount": 1000})
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

        # 1 个成功，4 个 UnconfirmedWriteToolError（ClaimFailedError）
        successes = [r for r in results if not isinstance(r, BaseException)]
        blocked = [r for r in results if isinstance(r, UnconfirmedWriteToolError)]
        assert len(successes) == 1
        assert len(blocked) == 4
        assert counter["n"] == 1  # tool.fn 只跑了 1 次


@pytest.mark.asyncio
async def test_action_id_uniqueness_for_duplicate_args(reg):
    """单 round 同 tool + 同 args 两个 pending → 两个独立 token，互不影响。"""
    counter = {"n": 0}

    async def fn(amount: int, *, hub_user_id, conversation_id,
                 confirmation_action_id: str, **_):
        counter["n"] += 1
        return {"draft_id": counter["n"]}

    reg.register("create_voucher_draft", fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    # 模拟 LLM 同 round 调两次 create_voucher_draft({amount:1000}) → 都拦截
    args = {"amount": 1000}
    await reg.confirm_gate.add_pending("c1", 1, "create_voucher_draft", args)
    await reg.confirm_gate.add_pending("c1", 1, "create_voucher_draft", args)

    # 用户回'是' → 两个 action_id 各自 token
    confirmed = await reg.confirm_gate.confirm_all_pending("c1", 1)
    assert len(confirmed) == 2
    assert confirmed[0]["action_id"] != confirmed[1]["action_id"]
    assert confirmed[0]["token"] != confirmed[1]["token"]  # token 含 action_id 所以不同

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        # 调用第一个：成功，第二个仍可用
        _r1 = await reg.call("create_voucher_draft", {
            **args, "confirmation_action_id": confirmed[0]["action_id"],
            "confirmation_token": confirmed[0]["token"],
        }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)
        _r2 = await reg.call("create_voucher_draft", {
            **args, "confirmation_action_id": confirmed[1]["action_id"],
            "confirmation_token": confirmed[1]["token"],
        }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)

    assert counter["n"] == 2  # 真的跑了 2 次，无相互干扰


@pytest.mark.asyncio
async def test_token_cross_action_replay_blocked(reg):
    """把 action_A 的 token 用在 action_B 上 → 拦截（防跨 action 复用）。"""
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    args = {"amount": 1000}
    await reg.confirm_gate.add_pending("c1", 1, "create_voucher_draft", args)
    await reg.confirm_gate.add_pending("c1", 1, "create_voucher_draft", args)
    confirmed = await reg.confirm_gate.confirm_all_pending("c1", 1)
    a, b = confirmed[0], confirmed[1]

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        # 拿 a.token 配 b.action_id → 校验失败 → restore + 拦截
        with pytest.raises(ClaimFailedError):
            await reg.call("create_voucher_draft", {
                **args, "confirmation_action_id": b["action_id"],
                "confirmation_token": a["token"],
            }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)


@pytest.mark.asyncio
async def test_permission_denied_does_not_consume_token(reg):
    """用户无权限 → claim 之前就抛 → confirmed token 不消费 + pending 仍在。"""
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.approve",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    # mock：require_permissions 抛 BizError（权限不足）
    with patch("hub.agent.tools.registry.require_permissions",
               AsyncMock(side_effect=BizError("PERM_DOWNSTREAM_DENIED"))):
        action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft", {"amount": 1000})
        with pytest.raises(BizError):
            await reg.call("create_voucher_draft", {
                "amount": 1000,
                "confirmation_action_id": action_id,
                "confirmation_token": token,
            }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)

    # 关键断言：pending 仍在（token 没被 claim 消费）
    pending_after = await reg.confirm_gate.list_pending("c1", 1)
    assert any(p["action_id"] == action_id for p in pending_after)

    # claim 仍可成功（token 没被消耗）
    bundle = await reg.confirm_gate.claim_action(
        "c1", 1, action_id, token, "create_voucher_draft", {"amount": 1000},
    )
    assert bundle is not None


@pytest.mark.asyncio
async def test_schema_validation_failure_does_not_consume_token(reg):
    """schema 校验失败 → claim 之前就抛 → confirmed token 不消费。"""
    async def fn(amount: int, *, hub_user_id, conversation_id,
                 confirmation_action_id: str, **_):
        return {"id": 1}

    reg.register("create_voucher_draft", fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft", {"amount": 1000})

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


@pytest.mark.asyncio
async def test_claim_atomically_removes_pending_so_reconfirm_safe(reg):
    """claim 成功后 pending 同步删除 → 用户再回'是'不会重 mark 同 action。"""
    counter = {"n": 0}

    async def fn(amount: int, *, hub_user_id, conversation_id,
                 confirmation_action_id: str, **_):
        counter["n"] += 1
        return {"draft_id": counter["n"]}

    reg.register("create_voucher_draft", fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft", {"amount": 1000})

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

        # 即使 LLM 在 TTL 内偶然记得旧 token，再调用也被拒（confirmed 已删）
        with pytest.raises(ClaimFailedError):
            await reg.call("create_voucher_draft", {
                "amount": 1000,
                "confirmation_action_id": action_id,
                "confirmation_token": token,
            }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=1)
        assert counter["n"] == 1  # 没有重复执行


@pytest.mark.asyncio
async def test_call_does_not_mutate_caller_args(reg):
    """reg.call 不能把 confirmation 字段从调用方原 dict 里 pop 掉。"""
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft", {"amount": 1000})
        payload = {
            "amount": 1000,
            "confirmation_action_id": action_id,
            "confirmation_token": token,
        }
        payload_snapshot = dict(payload)  # 记一份调用前内容

        await reg.call("create_voucher_draft", payload,
                        hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)

        # 调用后原 dict 应保持不变
        assert payload == payload_snapshot
        assert payload["confirmation_action_id"] == action_id
        assert payload["confirmation_token"] == token


@pytest.mark.asyncio
async def test_claim_failed_does_not_add_pending(reg):
    """v7 round 2 P1-#2：MissingConfirmationError vs ClaimFailedError 正确分流。

    完全不传 confirmation 字段 → MissingConfirmationError（ChainAgent 应 add_pending）。
    传了 confirmation_token 但 token 错 → ClaimFailedError（ChainAgent 不应 add_pending）。
    两个都是 UnconfirmedWriteToolError 子类（向后兼容 except 兜底语义）。
    """
    reg.register("create_voucher_draft", _fake_voucher_fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    args = {"amount": 1000}
    await reg.confirm_gate.add_pending("c1", 1, "create_voucher_draft", args)
    confirmed = await reg.confirm_gate.confirm_all_pending("c1", 1)
    a = confirmed[0]

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        # 完全不传 confirmation 字段 → MissingConfirmationError
        with pytest.raises(MissingConfirmationError):
            await reg.call("create_voucher_draft", {**args},
                           hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)

        # 传了 confirmation_token 但 token 错 → ClaimFailedError
        with pytest.raises(ClaimFailedError):
            await reg.call("create_voucher_draft", {
                **args,
                "confirmation_action_id": a["action_id"],
                "confirmation_token": "x" * 32,  # 错 token
            }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=1)

    # 两个都是 UnconfirmedWriteToolError 子类（向后兼容 except 兜底语义）
    assert issubclass(MissingConfirmationError, UnconfirmedWriteToolError)
    assert issubclass(ClaimFailedError, UnconfirmedWriteToolError)


@pytest.mark.asyncio
async def test_write_tool_must_declare_action_id_param_at_register_time(reg):
    """写类 tool fn 不声明 confirmation_action_id → register 时就 RuntimeError。"""
    # 写 tool fn 故意不声明 confirmation_action_id
    async def bad_write_fn(amount: int, *, hub_user_id, conversation_id):
        return {"id": 1}

    # 关键：注册时就 raise，不等到 call
    with pytest.raises(ToolRegistrationError, match="confirmation_action_id"):
        reg.register("create_voucher_draft", bad_write_fn,
                     perm="usecase.create_voucher.use",
                     tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    # 注册失败的 tool 不在 _tools 字典中
    assert "create_voucher_draft" not in reg._tools


@pytest.mark.asyncio
async def test_read_tool_does_not_need_action_id_param(reg):
    """READ / GENERATE 类 tool 不需要声明 confirmation_action_id（仅写类要）。"""
    async def search_fn(query: str) -> list: return []

    # 不抛 → 注册成功
    reg.register("search_products", search_fn,
                 perm="usecase.query_product.use",
                 tool_type=ToolType.READ, description="搜索商品")
    assert "search_products" in reg._tools


@pytest.mark.asyncio
async def test_action_id_injected_to_write_tool_fn(reg):
    """v8 round 2 P1：ToolRegistry 把 action_id 作为 confirmation_action_id 注入 fn。"""
    captured = {}

    async def fn(amount: int, *,
                 hub_user_id, conversation_id, confirmation_action_id: str, **_):
        captured["action_id"] = confirmation_action_id
        captured["hub_user_id"] = hub_user_id
        return {"id": 1}

    reg.register("create_voucher_draft", fn,
                 perm="usecase.create_voucher.use",
                 tool_type=ToolType.WRITE_DRAFT, description="创建凭证草稿")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        action_id, token = await _confirm_one(reg, "c1", 1, "create_voucher_draft", {"amount": 1000})

        await reg.call("create_voucher_draft", {
            "amount": 1000,
            "confirmation_action_id": action_id,
            "confirmation_token": token,
        }, hub_user_id=1, acting_as=2, conversation_id="c1", round_idx=0)

    assert captured["action_id"] == action_id  # 注入到 fn
    assert captured["hub_user_id"] == 1


@pytest.mark.asyncio
async def test_call_extracts_customer_id_from_result(session_memory, confirm_gate):
    """tool 返回含 customer_id → 写回 session.add_entity_refs。"""
    reg = ToolRegistry(confirm_gate=confirm_gate, session_memory=session_memory)

    async def fake_search(query: str) -> dict:
        return {"items": [{"customer_id": 9, "name": "阿里"}, {"customer_id": 10}]}

    reg.register("search_customers", fake_search,
                 perm="usecase.query_customer_history.use",
                 tool_type=ToolType.READ, description="搜索客户")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        await reg.call("search_customers", {"query": "阿里"},
                       hub_user_id=1, acting_as=2,
                       conversation_id="c1", round_idx=0)

    session_memory.add_entity_refs.assert_called_once()
    call_kwargs = session_memory.add_entity_refs.call_args
    assert call_kwargs.kwargs["customer_ids"] == {9, 10}


@pytest.mark.asyncio
async def test_call_extracts_product_id_from_nested_result(session_memory, confirm_gate):
    """从嵌套 items[].product_id 提取。"""
    reg = ToolRegistry(confirm_gate=confirm_gate, session_memory=session_memory)

    async def fake_search(query: str) -> dict:
        return {"results": [{"product_id": 42}, {"product_id": 99}]}

    reg.register("search_products", fake_search,
                 perm="usecase.query_product.use",
                 tool_type=ToolType.READ, description="搜索商品")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        await reg.call("search_products", {"query": "苹果"},
                       hub_user_id=1, acting_as=2,
                       conversation_id="c1", round_idx=0)

    session_memory.add_entity_refs.assert_called_once()
    call_kwargs = session_memory.add_entity_refs.call_args
    assert call_kwargs.kwargs["product_ids"] == {42, 99}


@pytest.mark.asyncio
async def test_register_validates_signature(reg):
    """register 对写类 tool 作签名校验；READ 类不校验。

    额外验证：WRITE_ERP 类同样需要 confirmation_action_id。
    """
    # WRITE_ERP 也需要 confirmation_action_id
    async def erp_fn_bad(amount: int) -> dict:
        return {}

    with pytest.raises(ToolRegistrationError, match="confirmation_action_id"):
        reg.register("erp_op", erp_fn_bad,
                     perm="usecase.erp.use",
                     tool_type=ToolType.WRITE_ERP, description="ERP 写操作")

    # GENERATE 类不需要
    async def gen_fn(prompt: str) -> str:
        return ""

    reg.register("generate_text", gen_fn,
                 perm="usecase.generate.use",
                 tool_type=ToolType.GENERATE, description="生成文本")
    assert "generate_text" in reg._tools


@pytest.mark.asyncio
async def test_call_validates_args_against_schema(reg):
    """_validate_args 对错误类型参数抛 ToolArgsValidationError。"""
    async def fn(amount: int) -> dict:
        return {}

    reg.register("read_op", fn,
                 perm="usecase.query_product.use",
                 tool_type=ToolType.READ, description="读操作")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        # amount 应为 integer，传 string → ToolArgsValidationError
        with pytest.raises(ToolArgsValidationError):
            await reg.call("read_op", {"amount": "not-an-int"},
                           hub_user_id=1, acting_as=2,
                           conversation_id="c1", round_idx=0)

        # 正确类型通过
        result = await reg.call("read_op", {"amount": 100},
                                 hub_user_id=1, acting_as=2,
                                 conversation_id="c1", round_idx=1)
        assert result == {}


@pytest.mark.asyncio
async def test_call_rejects_extra_args_when_strict(reg):
    """v11 round 2 I-4：jsonschema strict mode 拦截 LLM 塞额外字段（如试图越权字段）。"""
    async def fn(query: str) -> list:
        return []

    reg.register("search", fn,
                 perm="usecase.query_product.use",
                 tool_type=ToolType.READ, description="搜索")

    with patch("hub.agent.tools.registry.require_permissions", AsyncMock(return_value=None)):
        with pytest.raises(ToolArgsValidationError):
            await reg.call("search", {"query": "X", "evil_extra_field": 999},
                           hub_user_id=1, acting_as=2,
                           conversation_id="c1", round_idx=0)


@pytest.mark.asyncio
async def test_schema_for_user_caches_per_user(reg):
    """schema_for_user 对同一用户缓存（5 min TTL），不同用户独立缓存。"""
    async def search_fn(query: str) -> list: return []
    reg.register("search_products", search_fn,
                 perm="usecase.query_product.use",
                 tool_type=ToolType.READ, description="搜索商品")

    call_count = {"n": 0}

    async def counting_has_perm(user_id, perm):
        call_count["n"] += 1
        return True

    with patch("hub.agent.tools.registry.has_permission", counting_has_perm):
        # 第一次调用 user=1
        s1 = await reg.schema_for_user(hub_user_id=1)
        assert call_count["n"] == 1

        # 第二次同 user=1 → 命中缓存，has_permission 不再调用
        s2 = await reg.schema_for_user(hub_user_id=1)
        assert call_count["n"] == 1  # 没增加
        assert s1 is s2  # 同一对象

        # user=2 → 单独查询，has_permission 调用
        s3 = await reg.schema_for_user(hub_user_id=2)
        assert call_count["n"] == 2
        assert s3 is not s1  # 独立缓存
