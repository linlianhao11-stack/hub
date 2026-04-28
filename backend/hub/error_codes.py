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
