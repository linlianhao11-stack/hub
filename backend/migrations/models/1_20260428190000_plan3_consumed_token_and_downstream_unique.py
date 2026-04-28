"""Plan 3 迁移：新增 consumed_binding_token 表 + DownstreamIdentity 第二个唯一约束。

aerich 不能自动检测 unique_together 增删，本迁移手工编写。
"""
from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "consumed_binding_token" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "erp_token_id" INT NOT NULL UNIQUE,
            "hub_user_id" INT NOT NULL,
            "consumed_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS
            "uidx_downstream_identity_downstream_type_downstream_user_id"
            ON "downstream_identity" ("downstream_type", "downstream_user_id");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uidx_downstream_identity_downstream_type_downstream_user_id";
        DROP TABLE IF EXISTS "consumed_binding_token";
    """
