"""中文同义词归一映射（输入预处理 + system prompt 注入两路用）。"""
from __future__ import annotations

DEFAULT_SYNONYMS: dict[str, list[str]] = {
    # 业务实体
    "客户": ["顾客", "甲方", "客方"],
    "商品": ["产品", "货品", "SKU", "品类"],
    "订单": ["销售单", "销单", "出货单"],
    "库存": ["现货", "现存", "存货"],
    "供应商": ["供方", "卖家", "厂商"],

    # 财务术语
    "销售额": ["营业额", "总销售", "销售总额", "成交额"],
    "回款": ["收款", "款项", "应收"],
    "成本": ["进价", "采购价", "成本价"],
    "毛利": ["净利", "利润"],
    "凭证": ["传票", "记账凭证"],

    # 操作动词
    "查": ["搜", "看", "找", "查询", "搜索"],
    "改": ["调整", "更新", "修改"],
    "生成": ["创建", "新建", "做"],
    "审核": ["审批", "通过", "批"],
    "拒绝": ["驳回", "退回"],

    # 数量/时间
    "近期": ["最近", "近", "新近"],
    "今日": ["今天", "本日"],
    "上次": ["最近一次", "前次", "上回"],

    # 状态
    "已": ["已经", "完成"],
    "未": ["还没", "尚未", "暂未"],

    # 额外补充（凑足 ≥20 组）
    "压货": ["积压货", "滞销货", "呆货"],
    "合同": ["协议", "合约"],
}


# 白名单：仅这些 canonical 参与 normalize（实体名词；动词同义不归一以免破坏 business_dict 复合词）
# 例："做" 是 "生成" 的 alt，但 "做凭证"/"做账" 是 business_dict 关键词，不可被打散
_NORMALIZE_WHITELIST = {
    "客户", "商品", "订单", "库存", "供应商",
    "销售额", "回款", "成本", "毛利", "凭证",
    "近期", "今日", "上次",
}


def render_synonyms(s: dict[str, list[str]] | None = None) -> str:
    """渲染同义词为 system prompt 文本片段。"""
    s = s or DEFAULT_SYNONYMS
    if not s:
        return ""
    lines = [f"- {canonical}: {', '.join(alts)}" for canonical, alts in s.items()]
    return "\n".join(lines)


def normalize(text: str, synonyms: dict[str, list[str]] | None = None) -> str:
    """归一同义词到 canonical 形式（输入预处理用）。

    实现要点（v2 修复链式替换 bug）：
    1. 默认 synonyms 仅对**实体名词**做归一（白名单），动词同义词剔除以免破坏 business_dict 复合词
       （如 "做凭证" 不应被打成 "生成凭证"）；自定义 synonyms 不受白名单限制。
    2. 用 sentinel 一次过避免 A→B→C 链式塌陷
    3. alt 按长度降序遍历，避免短 alt 截断长 alt（"已" 是 "已经" 的子串）
    """
    use_default = synonyms is None
    s = DEFAULT_SYNONYMS if use_default else synonyms
    if not text or not s:
        return text

    # 收集所有 (alt, canonical)；默认 synonyms 限白名单，自定义 synonyms 全量处理
    pairs = []
    for canonical, alts in s.items():
        if use_default and canonical not in _NORMALIZE_WHITELIST:
            continue
        for alt in alts:
            pairs.append((alt, canonical))
    # alt 长度降序：先匹配长串避免 "已" 截断 "已经"
    pairs.sort(key=lambda p: -len(p[0]))

    out = text
    slots: list[str] = []
    for alt, canonical in pairs:
        if alt in out:
            slot = f"\x00{len(slots)}\x00"
            slots.append(canonical)
            out = out.replace(alt, slot)
    # 还原 sentinel 为 canonical（不会再次 replace）
    for i, canonical in enumerate(slots):
        out = out.replace(f"\x00{i}\x00", canonical)
    return out
