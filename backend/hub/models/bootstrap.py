from __future__ import annotations
from tortoise import fields
from tortoise.models import Model


class BootstrapToken(Model):
    """初始化向导一次性 token。

    HUB 启动时若数据库为空（system_initialized=false）：
    - 自动生成 token（除非 .env 设置了 HUB_SETUP_TOKEN）
    - 哈希存数据库
    - 校验时按 hash 比对，验证通过后立即标记 used
    - 30 分钟 TTL（可配置）
    """
    id = fields.IntField(pk=True)
    token_hash = fields.CharField(max_length=255)
    expires_at = fields.DatetimeField()
    used_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "bootstrap_token"
