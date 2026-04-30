"""Plan 6 Task 4：持久化 Memory（用户/客户/商品三层）。

每层都是 Postgres 表，facts 列是 JSONB 数组（list of fact dict）。
service class 提供 load / upsert_facts 接口；不直接暴露 ORM model。
"""
from __future__ import annotations
from datetime import datetime, UTC
from typing import Iterable

from hub.models.memory import (
    UserMemory as UserMemoryModel,
    CustomerMemory as CustomerMemoryModel,
    ProductMemory as ProductMemoryModel,
)


class UserMemoryService:
    """用户级长期记忆（偏好 + 历史 facts）。"""

    async def load(self, hub_user_id: int) -> dict:
        """返 {facts: [...], preferences: {...}}；空记录返 {}+{}."""
        rec = await UserMemoryModel.filter(hub_user_id=hub_user_id).first()
        if not rec:
            return {"facts": [], "preferences": {}}
        return {"facts": rec.facts or [], "preferences": rec.preferences or {}}

    async def upsert_facts(self, hub_user_id: int, *,
                           new_facts: list[dict],
                           preferences: dict | None = None) -> None:
        """追加 facts（去重）+ 可选 merge preferences。"""
        rec, _ = await UserMemoryModel.get_or_create(hub_user_id=hub_user_id)
        existing = rec.facts or []
        # 简单去重：fact 文本相同视为重复
        seen = {f.get("fact") for f in existing if isinstance(f, dict)}
        for f in new_facts:
            if isinstance(f, dict) and f.get("fact") not in seen:
                existing.append({**f, "created_at": datetime.now(UTC).isoformat()})
                seen.add(f.get("fact"))
        rec.facts = existing
        if preferences is not None:
            merged = (rec.preferences or {}).copy()
            merged.update(preferences)
            rec.preferences = merged
        rec.updated_at = datetime.now(UTC)
        await rec.save()


class CustomerMemoryService:
    """客户级长期记忆（议价习惯 / 付款记录摘要等）。"""

    async def load_referenced(self, customer_ids: Iterable[int]) -> dict[int, dict]:
        """批量加载多个客户的 memory；空返 {}.

        Returns:
            {customer_id: {"facts": [...]}}
        """
        cids = list(customer_ids)
        if not cids:
            return {}
        recs = await CustomerMemoryModel.filter(erp_customer_id__in=cids).all()
        return {r.erp_customer_id: {"facts": r.facts or []} for r in recs}

    async def upsert_facts(self, customer_id: int, *,
                           new_facts: list[dict]) -> None:
        rec, _ = await CustomerMemoryModel.get_or_create(erp_customer_id=customer_id)
        existing = rec.facts or []
        seen = {f.get("fact") for f in existing if isinstance(f, dict)}
        for f in new_facts:
            if isinstance(f, dict) and f.get("fact") not in seen:
                existing.append({**f, "created_at": datetime.now(UTC).isoformat()})
                seen.add(f.get("fact"))
        rec.facts = existing
        rec.last_referenced_at = datetime.now(UTC)
        rec.updated_at = datetime.now(UTC)
        await rec.save()


class ProductMemoryService:
    """商品级长期记忆（断货 / 停产 / 替代品等）。"""

    async def load_referenced(self, product_ids: Iterable[int]) -> dict[int, dict]:
        pids = list(product_ids)
        if not pids:
            return {}
        recs = await ProductMemoryModel.filter(erp_product_id__in=pids).all()
        return {r.erp_product_id: {"facts": r.facts or []} for r in recs}

    async def upsert_facts(self, product_id: int, *,
                           new_facts: list[dict]) -> None:
        rec, _ = await ProductMemoryModel.get_or_create(erp_product_id=product_id)
        existing = rec.facts or []
        seen = {f.get("fact") for f in existing if isinstance(f, dict)}
        for f in new_facts:
            if isinstance(f, dict) and f.get("fact") not in seen:
                existing.append({**f, "created_at": datetime.now(UTC).isoformat()})
                seen.add(f.get("fact"))
        rec.facts = existing
        rec.updated_at = datetime.now(UTC)
        await rec.save()
