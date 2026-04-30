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


def render_synonyms(s: dict[str, list[str]] | None = None) -> str:
    """渲染同义词为 system prompt 文本片段。"""
    s = s or DEFAULT_SYNONYMS
    if not s:
        return ""
    lines = [f"- {canonical}: {', '.join(alts)}" for canonical, alts in s.items()]
    return "\n".join(lines)


def normalize(text: str, synonyms: dict[str, list[str]] | None = None) -> str:
    """把 text 中的同义词替换为 canonical 形式（输入预处理用）。

    简单正向替换，不做语义分词。仅用于 RuleParser 路径降低误判；
    LLM 路径不依赖此函数（LLM 自己理解同义词）。
    """
    s = synonyms or DEFAULT_SYNONYMS
    out = text
    for canonical, alts in s.items():
        for alt in alts:
            if alt in out:
                out = out.replace(alt, canonical)
    return out
