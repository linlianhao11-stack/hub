"""v8 staging review #17：ContractDraft 加 extras + fingerprint 字段。

背景：合同幂等查询过去只比对 (conversation_id, customer_id, template_id, hub_user_id, items)，
漏掉 extras（合同号 / 付款条款 / 交付地址等）。同会话改 extras 时新文件已发但 DB 仍复用
旧 draft 记录，admin 审计 metadata 与实际 docx 不一致。

修法：
- 加 extras JSONB 字段持久化渲染参数
- 加 fingerprint VARCHAR(64) 字段持久化稳定指纹（sha256(items + normalized extras + customer_id + template_id)）
- 加 fingerprint 部分索引加速幂等查询

字段都是 NULL allowable，旧数据不需要回填（旧 draft 直接走非 fingerprint 路径）。
"""
from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "contract_draft"
            ADD COLUMN IF NOT EXISTS "extras" JSONB NULL,
            ADD COLUMN IF NOT EXISTS "fingerprint" VARCHAR(64) NULL;

        CREATE INDEX IF NOT EXISTS "idx_contract_draft_fingerprint"
            ON "contract_draft" ("fingerprint")
            WHERE "fingerprint" IS NOT NULL;

        COMMENT ON COLUMN "contract_draft"."extras" IS
            '渲染参数（合同号/付款条款/交付地址等），与 items 一起决定 docx 内容（v8 review #17）';
        COMMENT ON COLUMN "contract_draft"."fingerprint" IS
            '幂等指纹 sha256(template_id|customer_id|items|extras_normalized)，重试时复用此 draft（v8 review #17）';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_contract_draft_fingerprint";
        ALTER TABLE "contract_draft"
            DROP COLUMN IF EXISTS "extras",
            DROP COLUMN IF EXISTS "fingerprint";
    """
