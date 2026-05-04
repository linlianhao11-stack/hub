"""v8 staging review #20：ConversationLog + ToolCallLog 按 (conv, user) per-user 归因。

问题：钉钉群聊里多人共享同一个 conversation_id，旧 ConversationLog.conversation_id
全局 unique 会让 B 用户复用 A 创建的 log → admin 会话历史 / task detail
决策链 / 成本统计把多人混到一条记录。
ToolCallLog 也同样按 conversation_id 查，混淆 user。

修：
- ConversationLog 单字段 unique 删除，改成 (conversation_id, hub_user_id) 复合 unique。
- ToolCallLog 加 hub_user_id 列 + (conversation_id, hub_user_id) 复合索引。
- 旧数据 hub_user_id NULL 的复合 unique 在 PostgreSQL 里允许多条（NULL 不算重复），
  与"NULL 表示未知 user"语义一致。
"""
from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- ConversationLog: drop 旧 unique，加复合 unique
        ALTER TABLE "conversation_log"
            DROP CONSTRAINT IF EXISTS "conversation_log_conversation_id_key";
        DROP INDEX IF EXISTS "conversation_log_conversation_id_key";

        ALTER TABLE "conversation_log"
            ADD CONSTRAINT "uniq_conv_log_per_user"
            UNIQUE ("conversation_id", "hub_user_id");

        -- ToolCallLog: 加 hub_user_id 列 + 复合索引
        ALTER TABLE "tool_call_log"
            ADD COLUMN IF NOT EXISTS "hub_user_id" INTEGER NULL;

        CREATE INDEX IF NOT EXISTS "idx_tool_call_log_conv_user"
            ON "tool_call_log" ("conversation_id", "hub_user_id");

        COMMENT ON COLUMN "tool_call_log"."hub_user_id" IS
            'v8 review #20：群聊里同 conv 不同用户的 tool 调用按 user 归因';
        COMMENT ON CONSTRAINT "uniq_conv_log_per_user" ON "conversation_log" IS
            'v8 review #20：复合 unique 防群聊串日志（旧单字段 unique 已删）';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_tool_call_log_conv_user";
        ALTER TABLE "tool_call_log" DROP COLUMN IF EXISTS "hub_user_id";

        ALTER TABLE "conversation_log"
            DROP CONSTRAINT IF EXISTS "uniq_conv_log_per_user";
        ALTER TABLE "conversation_log"
            ADD CONSTRAINT "conversation_log_conversation_id_key"
            UNIQUE ("conversation_id");
    """
