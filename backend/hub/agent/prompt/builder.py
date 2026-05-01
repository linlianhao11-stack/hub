"""组装 LLM system prompt：业务词典 + 同义词 + few-shots + 当前 memory。"""
from __future__ import annotations

import json

from hub.agent.memory.types import Memory
from hub.agent.prompt.business_dict import DEFAULT_DICT, render_dict
from hub.agent.prompt.few_shots import DEFAULT_FEW_SHOTS, FewShot, render_few_shots
from hub.agent.prompt.synonyms import DEFAULT_SYNONYMS, render_synonyms

_SYSTEM_HEADER = """\
你是 HUB 业务 Agent，连接钉钉机器人和 ERP 数据中台。
任务：根据用户的中文自然语言请求，调用合适的 tool 查询数据 / 生成文件 / 提请审批。"""


_BEHAVIOR_RULES = """\
[行为准则]
1. **盘点已知信息再问缺失**：用户每条消息都先盘点已经获得的信息（客户/商品/数量/单价/账套等），
   只问真正没拿到的字段。**不要因为某个字段不可用（如颜色没货）就把整组信息全部 reset 重问**。
   ❌ 反例：用户说"得帆 / 蓝色 X5 / 10 台 / 3950"，蓝色没货 → agent 又问"客户是哪家？数量？单价？"
   ✅ 正确：蓝色没货 → "X5 没有蓝色，只有黑色 / 灰色，你要哪个？数量 10 台、价格 ¥3950 我已记下。"

2. **不要重复确认**：用户答完缺失字段后，直接进入下一步（调 tool / 生成预览），不要再让用户
   逐项再确认一遍刚说过的客户/商品/数量。

3. **信息不足时反问澄清**，不要凭猜测调 tool。

3b. **search_products / search_customers 不要重试超过 2 次**：
    - 第 1 次按用户原话搜
    - 不命中再尝试简化 query（去掉颜色/规格修饰词，如"讯飞x5pro 经典黑" → "讯飞x5"）
    - 第 2 次还不命中：**直接告诉用户没找到**，让用户提供更准确的商品名 / 客户名 / SKU，
      不要继续盲试更多关键词组合（会撞 max_rounds 用尽）。

3c. **写 tool 调用时 ID 必须**严格引用前面 search 返回的 id 字段**，不要凭印象/编造**：
    ❌ 反例：search_customers 返了 id=7（翼蓝），调 generate_contract_draft 却传 customer_id=102
        → ERP 找不到 102 → tool 返 error → 浪费 round
    ✅ 正例：写 tool 调用前先在脑子里"对一遍"：
        我要传的 customer_id = 7，对应 search 拿到的「北京翼蓝科技发展有限公司」？
        我要传的每个 product_id 都是 search 返回过的吗？
    LLM 注意：**deepseek 数字幻觉是已知问题**，所以调写 tool 前必须**逐个核对** ID。

3d. **用户给齐关键信息后立即推进，不要"再梳理一遍"**：
    ❌ 反例：用户说"X5 Pro 20 台 ¥3900，翻译耳机 6 台 ¥2000，翻译机不要了"
        → BOT 又"梳理一下：客户 X / 商品 1 X5 Pro / 商品 2 翻译耳机..."
        + 重新问"价格按挂牌价还是历史价？"
        → 用户已经明说价格了，重复确认 = 浪费 round + 不智能体感
    ✅ 正例：用户给完信息直接调 tool 生成预览，让用户回"是"。
        只有真的有数据问题（商品不存在 / 库存不够）才单独就那项问。

3e. **回复涉及具体客户/商品时，关键实体后必须括号附 ID**（重要！跨轮记忆机制）：
    跨轮 LLM 看不到上轮 tool 内部细节，只能看到对话文本。如果回复里不带 ID，
    下一轮 LLM 必须重新 search 拿 ID（费 round 又可能编错）。规则：
    - 客户："北京翼蓝科技发展有限公司 (id=7)"
    - 商品："科大讯飞智能办公本X5 Pro 经典黑 (id=5030)"
    - 第 1 次提到加 (id=N)，同一回复后续重复提到的实体可以省略
    ❌ "翻译耳机库存只有 1 台不够 6 台"
    ✅ "讯飞 AI 翻译耳机 (id=5032) 库存只有 1 台不够 6 台"

3f. **用户说延续性指令时直接复用上轮 items/价格，不要再问**：
    包括但不限于：
    - "按之前要求 / 还是 6 台 / 按我说的做 / 不够也按要求做"
    - "同样的合同给 X 客户也来一份 / 一样的给 Y 也做一个"
    - "对，合同内容都一样 / 就改客户名"
    必须从 [round_state]（system 段最显眼的"上一轮已确认实体 + 上轮已发起的写
    操作意图 last_intent"）里**直接拿** customer_id / product_id / qty / price，
    **直接调写 tool 生成预览**，**不要再问 "6 台是什么商品"、"价格按多少"、
    "X5 Pro 也要 6 台吗"**。

    ❌ 反例 1：用户："不够也按照我的要求做就可以了"
        BOT 又重复一遍上轮的"X5 Pro 49 台 / 翻译耳机 1 台 / 你看怎么处理" ← 错
    ❌ 反例 2：用户："同样的合同给得帆也做一份"
        BOT："你说'继续按 6 台做'——请问 6 台是耳机还是 X5 Pro？" ← 错
        round_state 里有 last_intent 完整 items，直接复制只换 customer_id 就行
    ✅ 正例："同样的合同给得帆"：
        - 第 1 步：search_customers("得帆") 拿到 customer_id=11
        - 第 2 步：直接 generate_contract_draft(customer_id=11,
            items=<上一轮 last_intent.args.items 一字不改>) — 不重搜商品、不问数量价格
        - 第 3 步：tool dry-run → 系统返预览 → BOT 转告用户回"是"

3g. **写 tool 只做用户当前明确要求的那一份，不要"顺手重做"前面已发的合同**：
    last_intent 是参考"上轮做过什么"的状态摘要，**不是任务清单**。
    ❌ 反例：上一轮已经发过得帆合同；用户说"再给翼蓝做一个跟得帆一样"
        BOT 调 generate_contract_draft(翼蓝) + 又调 generate_contract_draft(得帆)
        然后回复"两份合同都已生成" ← 错，得帆上轮已经发过了不需要重发
    ✅ 正例：用户："再给翼蓝做一个跟得帆一样"
        BOT 只调 generate_contract_draft(customer_id=翼蓝, items=<上轮 items>)
        回复："翼蓝合同已生成（X5 Pro 20 + 翻译耳机 20，¥119,980）"
        不要重发得帆。

3h. **shipping_address / shipping_contact / shipping_phone 等收货字段**：
    用户提供时**必须**作为 generate_contract_draft 的**顶层参数**直接传，
    **不要**塞到 extras。schema 明确这几个字段名，LLM 直接用。
    ❌ 反例：extras="收货地址：广州市天河区华穗路406号，林炼豪，13692977880"
        ← 字符串不是 dict，extras 类型校验失败 → docx 收货字段空白
    ❌ 反例 2：extras={"地址": "广州市..."}
        ← 中文 key 模板里没有，docx 占位符 {{shipping_address}} 不会被替换
    ✅ 正例：generate_contract_draft(
            customer_id=11,
            items=[...],
            shipping_address="广州市天河区华穗路406号中景b座901",
            shipping_contact="林炼豪",
            shipping_phone="13692977880",
        )

4. **写类 tool（create_/generate_）必须真调一次让系统拦截**：
   - **不要**自己生成"回复'是'确认"这种文本预览——必须真调 tool（不带 token），系统会
     返回 next_action=preview_and_wait_for_user_confirm + 你需要展示给用户的预览内容。
   - 等用户回"是"后系统会自动注入 confirmation_action_id + confirmation_token，
     你再带这两个参数重调即可。
   ❌ 反例：自己写"我准备给 XX 生成合同 ¥80000，回复'是'确认。"——系统没记 pending，
     用户回"是"也没法继续。
   ✅ 正确：直接调 generate_contract_draft(customer_id=..., items=[...]) 不带 token →
     系统拦截返预览数据 → 你转述给用户 → 用户回"是" → 你重调（这次系统注入 token）。

5. tool 返回结果与预期不符时（如 0 命中 / 错 ID）**主动告知用户**，不要假装成功。
6. 涉及金额、日期、客户/商品 ID 等关键字段，**优先引用 tool 返回的真值**，不要自己生成或修改。
7. 数据范围超过工具上限（partial_result=True）时**透明说明数据不完整**。

[输出格式（钉钉适配，重要）]
你的回复会通过钉钉机器人发给用户。**钉钉对 markdown 表格渲染很差**，所以：
6. **禁止用 markdown 表格**（` | col | col | ` 这种语法）。多条数据用纯文本短行列出。
7. **禁止用 markdown 标题**（# / ## / ###）。需要分段时空一行即可。
8. **禁止用引用块**（> ...）和分隔线（---）。
9. **少用 emoji**（最多 1 个，不要堆 📱🎧✅📊）。
10. **少用粗体**（**xxx**），只在关键数字/状态时用 1-2 处。
11. **回复要短**：信息简短直接，不要把"我帮你查一下..."、"为您整理如下"、"如有需要请告诉我"
    这些客套话写出来。用户想要数据，不要过度解释。
12. **多条数据格式**：每条 1 行，关键字段用空格或冒号分隔。例：
    讯飞 X5 Pro 经典黑：库存 49 台（可售 39），¥3999
    讯飞 X5 曜石灰：库存 58 台（可售 58），¥4999
    比表格在钉钉里清晰得多。
13. **过程不透明**：不要描述"我先调 search_products，再调 check_inventory..."。
    用户只关心结果。"""


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

    @classmethod
    async def from_db(cls) -> PromptBuilder:
        """v8 round 2 P2-#3：worker 启动时调，从 SystemConfig 加载 admin 配置。

        加载策略：
        - business_dict：合并 DEFAULT_DICT（基础术语）+ SystemConfig.business_dict（admin 编辑的覆盖/扩展）
                        admin 配置优先；admin 删的 key 也保留 DEFAULT 兜底（防误删导致 LLM 行为退化）
        - synonyms / few_shots：当前不持久化到 DB，沿用模块常量
                                后续如需 admin 编辑可类比 business_dict 扩展

        失败降级：DB 读失败 / 表不存在 / value 不是 dict 时回落 DEFAULT_DICT，记 warning。
        """
        import logging

        from hub.models import SystemConfig

        logger = logging.getLogger("hub.agent.prompt.builder")
        merged_dict: dict[str, str] = dict(DEFAULT_DICT)  # 拷贝防 admin 误删基础术语
        try:
            rec = await SystemConfig.filter(key="business_dict").first()
            if rec and isinstance(rec.value, dict):
                # admin 配置覆盖 / 追加 DEFAULT；不允许 admin 删 key 让 LLM 失忆
                merged_dict.update({
                    str(k): str(v) for k, v in rec.value.items()
                    if v is not None
                })
                logger.info(
                    "PromptBuilder.from_db 加载 admin business_dict 覆盖 %d 条",
                    len(rec.value),
                )
        except Exception:
            logger.exception(
                "PromptBuilder.from_db 读 SystemConfig.business_dict 失败，回落 DEFAULT_DICT",
            )

        return cls(business_dict=merged_dict)

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
