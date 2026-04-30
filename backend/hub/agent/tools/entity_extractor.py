# hub/agent/tools/entity_extractor.py
# I1: EntityRefs 已移到 hub.agent.memory.types，这里重新导出保持兼容。
from hub.agent.memory.types import EntityRefs  # noqa: F401 — 重新导出


class EntityExtractor:
    """从 tool result（任意 nested dict/list）提取 customer_id / product_id。"""

    def extract(self, result) -> EntityRefs:
        refs = EntityRefs()
        self._walk(result, refs)
        return refs

    def _walk(self, node, refs: EntityRefs):
        if isinstance(node, dict):
            for key, val in node.items():
                if key == "customer_id" and isinstance(val, int):
                    refs.customer_ids.add(val)
                elif key == "product_id" and isinstance(val, int):
                    refs.product_ids.add(val)
                elif key == "id" and "customer" in str(node.get("type", "")).lower():
                    if isinstance(val, int):
                        refs.customer_ids.add(val)
                elif key == "id" and "product" in str(node.get("type", "")).lower():
                    if isinstance(val, int):
                        refs.product_ids.add(val)
                else:
                    self._walk(val, refs)
        elif isinstance(node, list):
            for item in node:
                self._walk(item, refs)
