# hub/agent/tools/confirm_gate.py
from __future__ import annotations

import hashlib
import json
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone

from redis.asyncio import Redis
from redis.exceptions import WatchError


def uuid4_hex() -> str:
    """生成 32 位 hex UUID（无连字符）。"""
    return uuid.uuid4().hex


# ====== 新增异常（Task 0.5 Plan 6 v9）======

class CrossContextClaim(Exception):
    """claim 时 (conversation_id, hub_user_id) 与 pending 创建时不一致。"""
    pass


class CrossContextIdempotency(Exception):
    """idempotency_key 跨 context 命中，拒绝复用。"""
    pass


# ====== PendingAction dataclass（Task 0.5 Plan 6 v9）======

@dataclass
class PendingAction:
    """pending 写动作的完整描述。

    Task 0.5 新增字段（加在现有 ConfirmGate.add_pending 之上）：
    - conversation_id, hub_user_id：隔离 key
    - subgraph：触发该 pending 的 subgraph 名（如 "adjust_price"）
    - summary：给用户看的多行摘要（多 pending 时枚举用）
    - payload：完整执行载荷，含 tool_name + args
    - created_at：排序用，按 UTC 时间戳
    - ttl_seconds：默认 600 s
    - token：confirm 后的 HMAC token
    """
    action_id: str
    conversation_id: str
    hub_user_id: int
    subgraph: str
    summary: str
    payload: dict
    created_at: datetime
    ttl_seconds: int = 600
    token: str | None = None
    idempotency_key: str | None = None

    def is_expired(self) -> bool:
        """按 created_at + ttl_seconds 检查是否过期。"""
        from datetime import timedelta
        delta = datetime.now(tz=timezone.utc) - self.created_at
        return delta.total_seconds() > self.ttl_seconds


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

    # ====== Task 0.5 新增 Redis key：claimed audit log ======
    CLAIMED_KEY = "hub:agent:claimed:"  # string key per action_id，TTL 24h

    def _claimed_key(self, action_id: str) -> str:
        return f"{self.CLAIMED_KEY}{action_id}"

    def _idempotency_key(self, idempotency_key: str) -> str:
        return f"hub:agent:idempotency:{idempotency_key}"

    @staticmethod
    def _record_is_expired(data: dict) -> bool:
        """根据 pending record 的 created_at + ttl_seconds 判定逻辑是否过期。

        review round 4 / P1：物理 hash TTL = max(ttl_seconds, self.TTL=1800s)，
        但 record 自己的 ttl_seconds 是真实可确认窗口。两者不一致时，必须用
        record-level TTL 做判定，否则 ttl_seconds < self.TTL 时旧 pending 还会被
        list / claim / 当作 alive 复用。
        """
        try:
            created_at = datetime.fromisoformat(data["created_at"])
            ttl = int(data.get("ttl_seconds", 600))
        except (KeyError, ValueError, TypeError):
            return False  # 数据缺字段 → 保守认为不过期，沿用旧行为
        delta = datetime.now(tz=timezone.utc) - created_at
        return delta.total_seconds() > ttl

    async def _is_pending_alive_not_expired(
        self, *, conversation_id: str, hub_user_id: int, action_id: str,
    ) -> bool:
        """物理存在 + 逻辑未过期 → True。

        review round 4 / P1 修：旧实现仅 HEXISTS，逻辑过期 action 仍当 alive 复用。
        """
        if not action_id:
            return False
        pending_key = self._pending_key(conversation_id, hub_user_id)
        raw = await self.redis.hget(pending_key, action_id)
        if raw is None:
            return False
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return False
        return not self._record_is_expired(data)

    async def _cas_delete_idempotency_key(self, idem_key: str, expected_raw) -> bool:
        """CAS 删除：仅当当前值与 expected_raw 完全一致才 DEL，否则不动。

        review round 2 / P1：stale 清理路径必须避免盲删；并发下另一个协程可能已经
        清理 stale 并 SET NX 写入新 reservation，盲 DEL 会误清除新 reservation 导致
        同 idempotency_key 双创。

        实现：WATCH/MULTI/EXEC 事务保证 GET → DEL 之间 key 没被改才执行 DEL；
        WATCH 期间被改 → EXEC 失败 → 返 False（不删）。

        返：
          - True：当前值确为 expected_raw，已删除
          - False：当前值已被改 / 不存在 / 事务失败，未删除（也不应再删）
        """
        if expected_raw is None:
            return False
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(idem_key)
                    current = await pipe.get(idem_key)
                    if current != expected_raw:
                        # 别人改过 / 删过 → 我们不动
                        await pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.delete(idem_key)
                    await pipe.execute()
                    return True
                except Exception:
                    # WatchError 或其他 → 视为 CAS 失败，不删
                    try:
                        await pipe.reset()
                    except Exception:
                        pass
                    return False
        except Exception:
            return False

    # ====== Task 0.5 新 API：create_pending / list_pending_for_context / get_pending_by_id / is_claimed / claim ======

    async def create_pending(
        self, *,
        hub_user_id: int,
        conversation_id: str,
        subgraph: str,
        summary: str,
        payload: dict,
        action_prefix: str = "act",
        ttl_seconds: int = 600,
        idempotency_key: str | None = None,
        action_id: str | None = None,  # 测试 fixture 允许覆盖；生产路径用 None 自动生成
    ) -> PendingAction:
        """创建 PendingAction 并写入 Redis。

        - action_id 生产路径：f"{action_prefix}-{uuid4().hex}"（32-hex）
        - idempotency_key 同 (conv, user) 命中 → 复用；跨 context 命中 → raise CrossContextIdempotency

        review round 2 / round 3 加固：
          - **原子事务创建**（round 3 P1）：SET idem_key + HSET pending + EXPIRE 全放
            WATCH/MULTI/EXEC 内一次提交。旧实现先 SET NX 再 HSET 之间有窗口，T1 SET 后
            yield → T2 GET 见 idem 但 HEXISTS 假阴 → T2 把 T1 当 stale 删除 → 双 pending。
          - **stale CAS DELETE**（round 2 P1）：删除前用 CAS 校验当前值未被改，避免
            盲删另一个协程刚 SET 的新 reservation。
          - **跨 context fail-closed**：idempotency_key 命中跨 (conv, user) → 抛
            CrossContextIdempotency，绝不返别人的 action_id。
        """
        if idempotency_key is None:
            # 无幂等需求 — 直接走非幂等路径
            return await self._create_pending_record(
                hub_user_id=hub_user_id, conversation_id=conversation_id,
                subgraph=subgraph, summary=summary, payload=payload,
                action_prefix=action_prefix, ttl_seconds=ttl_seconds,
                idempotency_key=None, action_id=action_id,
            )

        idem_key = self._idempotency_key(idempotency_key)
        pending_key = self._pending_key(conversation_id, hub_user_id)
        max_pending_ttl = max(ttl_seconds, self.TTL)

        # 最多 4 次 attempt：每次可能因 WATCH 冲突或 stale 清理 retry
        for attempt in range(4):
            # 准备本次 attempt 的候选 record
            candidate_action_id = (
                action_id if (action_id and attempt == 0)
                else f"{action_prefix}-{uuid4_hex()}"
            )
            now = datetime.now(tz=timezone.utc)
            token = self.compute_token(
                conversation_id, hub_user_id, candidate_action_id,
                payload.get("tool_name", ""),
                self.canonicalize(payload.get("args", {})),
            )
            record = {
                "action_id": candidate_action_id,
                "conversation_id": conversation_id,
                "hub_user_id": hub_user_id,
                "subgraph": subgraph,
                "summary": summary,
                "payload": payload,
                "created_at": now.isoformat(),
                "ttl_seconds": ttl_seconds,
                "token": token,
                "idempotency_key": idempotency_key,
            }
            record_json = json.dumps(record, ensure_ascii=False)

            # round 3 P1：用 WATCH/MULTI/EXEC 把"检查 idem 不存在 + SET idem +
            # HSET pending + EXPIRE pending"做成一个原子事务，消除 SET 与 HSET 之间
            # 的中间窗口。T2 不可能在 T1 commit 之前观察到"idem 已 SET 但 pending 未 HSET"。
            existing_raw_for_stale = None
            try:
                async with self.redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(idem_key)
                    existing_raw = await pipe.get(idem_key)
                    if existing_raw is None:
                        # 原子提交 reservation + pending HSET
                        # review round 4 / P1：idem TTL 与 pending 物理 TTL 同步（max_pending_ttl），
                        # 不能用 ttl_seconds —— 否则 ttl_seconds < self.TTL 时 idem 先过期，旧 pending
                        # 仍在物理 hash 里，新请求绕过 stale 检查再创建 → 同 idempotency_key 双 pending。
                        pipe.multi()
                        pipe.set(idem_key, record_json, ex=max_pending_ttl)
                        pipe.hset(pending_key, candidate_action_id, record_json)
                        pipe.expire(pending_key, max_pending_ttl)
                        await pipe.execute()
                        return PendingAction(
                            action_id=candidate_action_id,
                            conversation_id=conversation_id,
                            hub_user_id=hub_user_id,
                            subgraph=subgraph,
                            summary=summary,
                            payload=payload,
                            created_at=now,
                            ttl_seconds=ttl_seconds,
                            token=token,
                            idempotency_key=idempotency_key,
                        )
                    # existing != None → 在 pipeline 外做 reuse / stale / cross-context 处理
                    await pipe.unwatch()
                    existing_raw_for_stale = existing_raw
            except WatchError:
                # WATCH 期间 idem_key 被改 → 别人抢了 reservation → 下一轮 attempt
                # 会重新 GET 看到新 record（可能 reuse / 可能 stale）
                continue

            existing = json.loads(existing_raw_for_stale)

            # 跨 context → 立即拒绝，绝不返别人的 action_id
            if (existing.get("conversation_id") != conversation_id
                    or existing.get("hub_user_id") != hub_user_id):
                raise CrossContextIdempotency(
                    f"idempotency_key={idempotency_key!r} 已被不同 context 使用"
                )

            # 同 context — 验证现存的 action_id 是否仍 alive（物理在 + 逻辑未过期）
            # review round 4 / P1：旧实现仅 HEXISTS，逻辑过期 action 会被当 alive
            # 复用 → 死 action 给用户。新实现同时检查 record.is_expired()。
            existing_aid = existing.get("action_id")
            is_alive = await self._is_pending_alive_not_expired(
                conversation_id=conversation_id,
                hub_user_id=hub_user_id,
                action_id=existing_aid,
            )
            if is_alive:
                # 真复用 — 返回现有 PendingAction
                return PendingAction(
                    action_id=existing_aid,
                    conversation_id=existing["conversation_id"],
                    hub_user_id=existing["hub_user_id"],
                    subgraph=existing["subgraph"],
                    summary=existing["summary"],
                    payload=existing["payload"],
                    created_at=datetime.fromisoformat(existing["created_at"]),
                    ttl_seconds=existing.get("ttl_seconds", 600),
                    token=existing.get("token"),
                    idempotency_key=existing.get("idempotency_key"),
                )

            # Stale：idempotency_key 还在但 action_id 已不在 pending hash
            # → CAS 清理（仅当当前值仍是我们读到的旧 stale record 才删）；
            # 下一轮 attempt 会用新 action_id 重新进入 WATCH/MULTI/EXEC。
            #
            # review round 2 / P1 修：旧实现盲目 DEL 在并发下会误删别人刚 SET 的新 reservation：
            #   T1 GET → V1 stale；T2 也 GET → V1 stale；T2 DEL+SET → V2 (alive)；
            #   T1 DEL 会把 V2 干掉，然后 T1 SET → V3 → 同 idempotency_key 双 reservation。
            # CAS 删除（WATCH+MULTI+EXEC）让"被改"的 DEL 失败，T1 的下一轮 attempt 通过
            # WATCH 也会看到 V2，走 reuse 路径，避免双创。
            await self._cas_delete_idempotency_key(idem_key, existing_raw_for_stale)
            # 继续下一次 attempt（CAS 成功 → 干净了；CAS 失败 → 别人写了新值，
            # 下一轮 WATCH+GET 会看到新值、转 reuse 路径，也是正确终态）

        # 不应到这里；safety
        raise RuntimeError(
            f"create_pending 重试耗尽 (idempotency_key={idempotency_key!r}) — "
            f"Redis 状态异常，请检查"
        )

    async def _create_pending_record(
        self, *,
        hub_user_id: int,
        conversation_id: str,
        subgraph: str,
        summary: str,
        payload: dict,
        action_prefix: str,
        ttl_seconds: int,
        idempotency_key: str | None,
        action_id: str | None,
    ) -> PendingAction:
        """无幂等路径 — 直接生成 + 写入。"""
        if action_id is None:
            action_id = f"{action_prefix}-{uuid4_hex()}"
        now = datetime.now(tz=timezone.utc)
        token = self.compute_token(
            conversation_id, hub_user_id, action_id,
            payload.get("tool_name", ""),
            self.canonicalize(payload.get("args", {})),
        )
        record = {
            "action_id": action_id,
            "conversation_id": conversation_id,
            "hub_user_id": hub_user_id,
            "subgraph": subgraph,
            "summary": summary,
            "payload": payload,
            "created_at": now.isoformat(),
            "ttl_seconds": ttl_seconds,
            "token": token,
            "idempotency_key": idempotency_key,
        }
        pending_key = self._pending_key(conversation_id, hub_user_id)
        await self.redis.hset(
            pending_key, action_id,
            json.dumps(record, ensure_ascii=False),
        )
        await self.redis.expire(pending_key, max(ttl_seconds, self.TTL))
        return PendingAction(
            action_id=action_id,
            conversation_id=conversation_id,
            hub_user_id=hub_user_id,
            subgraph=subgraph,
            summary=summary,
            payload=payload,
            created_at=now,
            ttl_seconds=ttl_seconds,
            token=token,
            idempotency_key=idempotency_key,
        )

    async def list_pending_for_context(
        self, *, conversation_id: str, hub_user_id: int
    ) -> list[PendingAction]:
        """返回该 (conv, user) 的 alive pending action，按 created_at asc 排序。

        review round 4 / P1：过滤逻辑过期 action（record.is_expired()）+ lazy GC：
        把过期 entry 从物理 hash 里 HDEL 掉。否则 confirm_node 会把过期 action
        列在"您有以下待确认操作"里，用户回 "1" 选中 → 死 action 误执行。
        """
        pending_key = self._pending_key(conversation_id, hub_user_id)
        raw = await self.redis.hgetall(pending_key)
        items: list[PendingAction] = []
        expired_aids: list[str] = []
        for aid_bytes, payload_bytes in (raw or {}).items():
            aid = aid_bytes.decode() if isinstance(aid_bytes, bytes) else aid_bytes
            try:
                data = json.loads(payload_bytes)
            except (json.JSONDecodeError, ValueError):
                continue
            # 只有 create_pending 写入的记录才有 created_at 字段
            if "created_at" not in data:
                continue
            if self._record_is_expired(data):
                expired_aids.append(aid)
                continue
            items.append(PendingAction(
                action_id=aid,
                conversation_id=data.get("conversation_id", conversation_id),
                hub_user_id=data.get("hub_user_id", hub_user_id),
                subgraph=data.get("subgraph", ""),
                summary=data.get("summary", ""),
                payload=data.get("payload", {}),
                created_at=datetime.fromisoformat(data["created_at"]),
                ttl_seconds=data.get("ttl_seconds", 600),
                token=data.get("token"),
                idempotency_key=data.get("idempotency_key"),
            ))
        # Lazy GC：清理过期 entry，避免 hash 长期累积垃圾
        if expired_aids:
            try:
                await self.redis.hdel(pending_key, *expired_aids)
            except Exception:
                pass  # GC 失败不影响返回结果
        items.sort(key=lambda p: (p.created_at, p.action_id))
        return items

    async def get_pending_by_id(self, action_id: str) -> PendingAction | None:
        """按 action_id 查 PendingAction（需要扫所有 key；测试/调试用）。

        注意：生产路径应优先用 list_pending_for_context + 已知 (conv, user) 定向查。
        """
        # action_id 格式 "prefix-32hex"，不含 conv:user 信息，只能扫 hash
        # 实际调用者应该传 conversation_id + hub_user_id 直接 HGET；此方法用于测试辅助
        pattern = f"{self.PENDING_KEY}*"
        async for key in self.redis.scan_iter(pattern):
            raw = await self.redis.hget(key, action_id)
            if raw:
                data = json.loads(raw)
                if "created_at" in data:
                    return PendingAction(
                        action_id=action_id,
                        conversation_id=data.get("conversation_id", ""),
                        hub_user_id=data.get("hub_user_id", 0),
                        subgraph=data.get("subgraph", ""),
                        summary=data.get("summary", ""),
                        payload=data.get("payload", {}),
                        created_at=datetime.fromisoformat(data["created_at"]),
                        ttl_seconds=data.get("ttl_seconds", 600),
                        token=data.get("token"),
                        idempotency_key=data.get("idempotency_key"),
                    )
        return None

    async def is_claimed(self, action_id: str) -> bool:
        """action 是否已被 claim（留 24h audit log）。"""
        return bool(await self.redis.exists(self._claimed_key(action_id)))

    async def claim(
        self, *, action_id: str, token: str,
        hub_user_id: int, conversation_id: str,
    ) -> bool:
        """消费一个 PendingAction token（单次；跨 context / 过期均抛 CrossContextClaim）。

        流程：
        1. HGET pending_key action_id → 校验存在性
        2. 校验 conversation_id 和 hub_user_id 一致 → 不一致 raise CrossContextClaim
        3. **校验 record 未过期**（review round 4 / P1）→ 过期 HDEL + raise
        4. 校验 token 匹配
        5. 原子 HDEL（单次消费）
        6. 写 claimed audit log（TTL 24h）
        """
        pending_key = self._pending_key(conversation_id, hub_user_id)
        raw = await self.redis.hget(pending_key, action_id)
        if raw is None:
            # 检查其他 context 是否有该 action_id（跨 context 检测）
            other = await self.get_pending_by_id(action_id)
            if other is not None:
                raise CrossContextClaim(
                    f"action_id={action_id!r} 属于 conv={other.conversation_id!r} "
                    f"user={other.hub_user_id}，拒绝 conv={conversation_id!r} user={hub_user_id} 的 claim"
                )
            # 已被消费或不存在 → 检查 claimed log
            if await self.is_claimed(action_id):
                raise CrossContextClaim(f"action_id={action_id!r} 已被消费（单次 token）")
            raise CrossContextClaim(f"action_id={action_id!r} 不存在（过期或已消费）")

        data = json.loads(raw)
        # 校验 context 一致性
        stored_conv = data.get("conversation_id", conversation_id)
        stored_user = data.get("hub_user_id", hub_user_id)
        if stored_conv != conversation_id or stored_user != hub_user_id:
            raise CrossContextClaim(
                f"action_id={action_id!r} 属于 conv={stored_conv!r} user={stored_user}，"
                f"拒绝 conv={conversation_id!r} user={hub_user_id} 的 claim"
            )
        # review round 4 / P1：record 逻辑过期必须拒 + 清理
        # 物理 hash TTL 比 record.ttl_seconds 长（max(ttl_seconds, self.TTL=1800)），
        # 仅 HEXISTS 不够；必须用 record-level TTL 把过期 action 拦在 claim 外，
        # 否则用户能确认一个早已逻辑过期的写操作。
        if self._record_is_expired(data):
            try:
                await self.redis.hdel(pending_key, action_id)
            except Exception:
                pass  # 清理失败不影响拒绝
            raise CrossContextClaim(
                f"action_id={action_id!r} 已过期（expired），不能确认"
            )
        # 校验 token
        stored_token = data.get("token")
        if stored_token != token:
            raise CrossContextClaim(f"action_id={action_id!r} token 不匹配")

        # 原子消费：HDEL
        deleted = await self.redis.hdel(pending_key, action_id)
        if not deleted:
            # 并发情况下可能被另一个 claim 抢先删除
            raise CrossContextClaim(f"action_id={action_id!r} 并发消费冲突")

        # 写 claimed audit log，TTL 24h
        await self.redis.set(
            self._claimed_key(action_id),
            json.dumps({"action_id": action_id, "conversation_id": conversation_id,
                        "hub_user_id": hub_user_id}, ensure_ascii=False),
            ex=86400,
        )
        return True

    # ====== 旧 API（保留给 ChainAgent；Task 7.3 删 ChainAgent 时一起清理）======

    async def add_pending(self, conversation_id: str, hub_user_id: int,
                          tool_name: str, args: dict) -> str:
        """[已废弃] ChainAgent 在 tool 被门禁拦截时调；返回 action_id。

        .. deprecated::
            请迁移到 create_pending()（Task 0.5 新 API）。
            此方法将在 Task 7.3 删 ChainAgent 时一并移除。
        """
        warnings.warn(
            "add_pending() 已废弃，请使用 create_pending()（Task 7.3 清理）",
            DeprecationWarning,
            stacklevel=2,
        )
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
        """[已废弃] 返回该 user 的所有 pending action（用户回'是'时由 ChainAgent 调）。

        .. deprecated::
            请迁移到 list_pending_for_context()（Task 0.5 新 API）。
        """
        warnings.warn(
            "list_pending() 已废弃，请使用 list_pending_for_context()（Task 7.3 清理）",
            DeprecationWarning,
            stacklevel=2,
        )
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
