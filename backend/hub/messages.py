"""钉钉回复文案模板（中文大白话原则）。"""
from __future__ import annotations


def binding_code_reply(code: str, ttl_minutes: int = 5) -> str:
    return (
        f"绑定码已生成：{code}\n\n"
        f"请在 {ttl_minutes} 分钟内登录 ERP，进入「设置 → 钉钉绑定」"
        f"输入此码完成确认。"
    )


def binding_user_not_found(erp_username: str) -> str:
    return f"未找到 ERP 用户「{erp_username}」，请检查用户名是否正确。"


def binding_already_bound(erp_username: str | None = None) -> str:
    suffix = f"到 ERP 用户「{erp_username}」" if erp_username else ""
    return f"该钉钉账号已经绑定{suffix}。如需换绑请先发送 /解绑。"


def binding_success(erp_display_name: str) -> str:
    return (
        f"绑定成功，欢迎 {erp_display_name}！\n"
        "发送「帮助」查看可用功能。"
    )


def privacy_notice() -> str:
    return (
        "为了功能改进和问题排查，你跟我的对话内容会被记录 30 天后自动删除，"
        "仅授权管理员可查看。如有疑问请联系管理员。"
    )


def unbind_success() -> str:
    return "已解绑。下次发送消息会重新触发绑定流程。"


def unbind_not_bound() -> str:
    return "你的钉钉账号尚未绑定 ERP 账号。请发送 /绑定 你的ERP用户名 开始绑定。"


def system_error(detail: str | None = None) -> str:
    base = "系统暂时出错了，请稍后重试。"
    return f"{base}（{detail}）" if detail else base


def help_message(available_commands: list[str]) -> str:
    cmds = "\n".join(f"• {c}" for c in available_commands)
    return f"我能帮你做这些：\n\n{cmds}\n\n输入「帮助」可再次查看此信息。"
