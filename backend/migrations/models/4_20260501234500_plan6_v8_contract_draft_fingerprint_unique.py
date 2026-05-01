"""v8 staging review #18：合同 draft fingerprint 部分**唯一**索引。

review #17 加了普通 partial index 但不是 UNIQUE，filter→create 仍是 read-then-insert
有并发 race window：两个同 fingerprint 的请求同时查不到 → 各自 create → DB 双
插入。

修法：把普通 index 升级为 partial UNIQUE index，覆盖
(conversation_id, requester_hub_user_id, fingerprint) WHERE fingerprint IS NOT NULL。
应用层 generate_contract_draft 在 create 抛 IntegrityError 时回查 first() 复用。

为什么 conv + user + fingerprint 三元组：
- 同 conv 同 user 同 fingerprint = 重试 / 真重复 → 应复用
- 同 conv 不同 user = 群聊里两人同时发同样的请求是合法的不同业务，不该 unique 拦
- 跨 conv 同 fingerprint = 完全不同对话上下文，也不能跨 conv 复用
"""
from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_contract_draft_fingerprint";

        CREATE UNIQUE INDEX IF NOT EXISTS "uniq_contract_draft_fingerprint"
            ON "contract_draft" ("conversation_id", "requester_hub_user_id", "fingerprint")
            WHERE "fingerprint" IS NOT NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uniq_contract_draft_fingerprint";

        CREATE INDEX IF NOT EXISTS "idx_contract_draft_fingerprint"
            ON "contract_draft" ("fingerprint")
            WHERE "fingerprint" IS NOT NULL;
    """
