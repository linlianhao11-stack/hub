"""M4：HubPermission 加 (resource, sub_resource, action) 复合唯一索引。

HubPermission 已有 code UNIQUE，但缺少 (resource, sub_resource, action) 语义三元组唯一约束。
不同 code 但相同三元组的权限记录可以插入，违反业务不变量。

加 UNIQUE INDEX 防止语义重复权限。
"""
from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE UNIQUE INDEX IF NOT EXISTS "hub_perm_res_sub_act_uniq"
            ON "hub_permission" ("resource", "sub_resource", "action");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "hub_perm_res_sub_act_uniq";
    """
