"""组装 LLM system prompt：业务词典 + 同义词 + few-shots + 当前 memory。"""
from __future__ import annotations
import json

from hub.agent.memory.types import Memory
from hub.agent.prompt.business_dict import DEFAULT_DICT, render_dict
from hub.agent.prompt.synonyms import DEFAULT_SYNONYMS, render_synonyms
from hub.agent.prompt.few_shots import DEFAULT_FEW_SHOTS, FewShot, render_few_shots


_SYSTEM_HEADER = """\
你是 HUB 业务 Agent，连接钉钉机器人和 ERP 数据中台。
任务：根据用户的中文自然语言请求，调用合适的 tool 查询数据 / 生成文件 / 提请审批。"""


_BEHAVIOR_RULES = """\
[行为准则]
1. 信息不足时**先反问澄清**，不要凭猜测调 tool。
2. 写类 tool（create_/generate_）**必须先用 text 预览给用户**，等用户回"是"后由系统注入
   confirmation_action_id + confirmation_token 再真调用。
3. tool 返回结果与预期不符时（如 0 命中 / 错 ID）**主动告知用户**，不要假装成功。
4. 涉及金额、日期、客户/商品 ID 等关键字段，**优先引用 tool 返回的真值**，不要自己生成或修改。
5. 数据范围超过工具上限（partial_result=True）时**透明说明数据不完整**。"""


class PromptBuilder:
    """LLM system prompt 组装器。

    组装顺序（spec §3.5）：
      header → 业务词典 → 同义词 → few-shots → 用户/客户/商品 memory → 行为准则。
    """

    def __init__(self,
                 business_dict: dict[str, str] | None = None,
                 synonyms: dict[str, list[str]] | None = None,
                 few_shots: list[FewShot] | None = None):
        # M1: 统一用 is not None，避免空 dict/list 被误作 falsy 丢弃
        self.business_dict = business_dict if business_dict is not None else DEFAULT_DICT
        self.synonyms = synonyms if synonyms is not None else DEFAULT_SYNONYMS
        self.few_shots = few_shots if few_shots is not None else DEFAULT_FEW_SHOTS

    def build(self, *, memory: Memory | None = None,
              tools_schema: list[dict] | None = None) -> str:
        """构造 system prompt 字符串。

        Args:
            memory: Task 4 MemoryLoader.load 的返回；None 表示无历史 memory（首次对话）
            tools_schema: ToolRegistry.schema_for_user 返的 list[dict]（OpenAI 格式）；
                          实际传给 LLM 的 tools 参数走 chat completions tools= 字段，
                          这里传入仅为给 LLM 显式提示可用 tool 名（可选）。

        Returns:
            纯文本 system prompt（一段长字符串）。
        """
        sections: list[str] = [_SYSTEM_HEADER]

        sections.append(f"[业务词典]\n{render_dict(self.business_dict)}")
        sections.append(f"[同义词]\n{render_synonyms(self.synonyms)}")
        # M4: builder 侧加 \n，render_few_shots 内部去掉前置 \n，格式统一
        sections.append(f"[Few-shot 例子]\n{render_few_shots(self.few_shots)}")

        # M2+M3: tools_schema 加 .get 防御 + 空 list 显式提示
        if tools_schema is not None:
            tool_names = [t.get("function", {}).get("name") for t in tools_schema]
            tool_names = [n for n in tool_names if n]
            if tool_names:
                sections.append(
                    f"[当前可用 tool ({len(tool_names)} 个)]\n"
                    + ", ".join(tool_names)
                )
            else:
                # M3: 空 list 或全无效 schema → 显式提示用户无 tool 权限
                sections.append(
                    "[当前可用 tool (0 个)]\n（用户无任何 tool 调用权限，请告知用户联系管理员）"
                )

        if memory is not None:
            mem_section = self._render_memory(memory)
            if mem_section:
                sections.append(mem_section)

        sections.append(_BEHAVIOR_RULES)
        return "\n\n".join(sections)

    def _render_memory(self, memory: Memory) -> str:
        """把 Memory dataclass 渲染成 prompt 段落。

        I3+I4: lazy header —— 只在第一条合法 fact 实际入列时才 append header，
        避免 facts 为空时残留孤立的 section header。
        """
        parts: list[str] = []

        # 用户层（lazy header）
        user_facts = memory.user.get("facts", []) if memory.user else []
        user_prefs = memory.user.get("preferences", {}) if memory.user else {}
        user_lines: list[str] = []
        for f in user_facts:
            if isinstance(f, dict) and f.get("fact"):
                user_lines.append(f"  - {f['fact']}")
        if user_prefs:
            user_lines.append(f"  偏好: {json.dumps(user_prefs, ensure_ascii=False)}")
        if user_lines:
            parts.append("[当前用户偏好]")
            parts.extend(user_lines)

        # 客户层（lazy header）
        customer_lines: list[str] = []
        if memory.customers:
            for cid, m in memory.customers.items():
                facts = m.get("facts", []) if m else []
                valid = [f for f in facts if isinstance(f, dict) and f.get("fact")]
                if valid:
                    customer_lines.append(f"  客户 {cid}:")
                    for f in valid:
                        customer_lines.append(f"    - {f['fact']}")
        if customer_lines:
            parts.append("[当前对话提及的客户]")
            parts.extend(customer_lines)

        # 商品层（lazy header）
        product_lines: list[str] = []
        if memory.products:
            for pid, m in memory.products.items():
                facts = m.get("facts", []) if m else []
                valid = [f for f in facts if isinstance(f, dict) and f.get("fact")]
                if valid:
                    product_lines.append(f"  商品 {pid}:")
                    for f in valid:
                        product_lines.append(f"    - {f['fact']}")
        if product_lines:
            parts.append("[当前对话提及的商品]")
            parts.extend(product_lines)

        if not parts:
            return ""
        return "\n".join(parts)
