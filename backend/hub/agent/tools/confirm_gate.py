# hub/agent/tools/confirm_gate.py
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
        action_id = uuid.uuid4().hex  # v10 round 2 P2：32-hex 完整 (128 bit) 防长期碰撞
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
        pending 不在这里清；ToolRegistry.call 调 claim_action（v6 round 2 P1 加固后）会用
        Lua 脚本**原子同时 HDEL confirmed + pending**，所以成功路径无需任何后续 cleanup（持久终态）。
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
        if pending_raw_or_false:
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
