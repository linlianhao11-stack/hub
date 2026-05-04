"""Plan 6 迁移：9 张 Agent 新表。

包含：
  - conversation_log + tool_call_log（对话与工具调用日志）
  - user_memory / customer_memory / product_memory（三层 Memory）
  - contract_template / contract_draft（合同模板与草稿）
  - voucher_draft（凭证草稿，五状态机 + creating 租约）
  - price_adjustment_request / stock_adjustment_request（写操作申请）

注意事项：
  - tool_call_log.conversation_id 无 FK 约束（支持孤立写入）
  - 三张写草稿表均含 confirmation_action_id VARCHAR(64) + 部分唯一索引（WHERE NOT NULL）
  - voucher_draft 含 CHECK 约束（status 五值）
  - aerich 不能自动处理 GIN / partial index，故手写本迁移
"""
from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- ─── conversation_log ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS "conversation_log" (
            "id"               BIGSERIAL NOT NULL PRIMARY KEY,
            "conversation_id"  VARCHAR(200) NOT NULL UNIQUE,
            "hub_user_id"      INT,
            "channel_userid"   VARCHAR(200) NOT NULL,
            "started_at"       TIMESTAMPTZ NOT NULL,
            "ended_at"         TIMESTAMPTZ,
            "rounds_count"     INT NOT NULL DEFAULT 0,
            "tokens_used"      INT NOT NULL DEFAULT 0,
            "tokens_cost_yuan" DECIMAL(10,4),
            "final_status"     TEXT,
            "error_summary"    TEXT
        );
        COMMENT ON TABLE "conversation_log" IS 'Agent 单次对话元数据日志。';
        CREATE INDEX IF NOT EXISTS "idx_conv_log_user_started"
            ON "conversation_log" ("hub_user_id", "started_at");

        -- ─── tool_call_log ─────────────────────────────────────────────────
        -- conversation_id 无 FK 约束，支持孤立写入（观测不阻塞业务）
        CREATE TABLE IF NOT EXISTS "tool_call_log" (
            "id"              BIGSERIAL NOT NULL PRIMARY KEY,
            "conversation_id" VARCHAR(200) NOT NULL,
            "round_idx"       INT NOT NULL,
            "tool_name"       TEXT NOT NULL,
            "args_json"       JSONB,
            "result_json"     JSONB,
            "duration_ms"     INT,
            "error"           TEXT,
            "called_at"       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE "tool_call_log" IS 'Agent 对话内每次 tool 调用明细。';
        CREATE INDEX IF NOT EXISTS "idx_tool_call_log_conv"
            ON "tool_call_log" ("conversation_id", "round_idx");

        -- ─── user_memory ───────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS "user_memory" (
            "hub_user_id"  INT NOT NULL PRIMARY KEY,
            "facts"        JSONB NOT NULL DEFAULT '[]',
            "preferences"  JSONB NOT NULL DEFAULT '{}',
            "updated_at"   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE "user_memory" IS '用户级 Agent 记忆（偏好/历史偏好）。';

        -- ─── customer_memory ───────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS "customer_memory" (
            "erp_customer_id"   INT NOT NULL PRIMARY KEY,
            "facts"             JSONB NOT NULL DEFAULT '[]',
            "last_referenced_at" TIMESTAMPTZ,
            "updated_at"        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE "customer_memory" IS '客户级 Agent 记忆（议价习惯/付款摘要）。';

        -- ─── product_memory ────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS "product_memory" (
            "erp_product_id" INT NOT NULL PRIMARY KEY,
            "facts"          JSONB NOT NULL DEFAULT '[]',
            "updated_at"     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE "product_memory" IS '商品级 Agent 记忆（断货/停产/替代品）。';

        -- ─── contract_template ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS "contract_template" (
            "id"                    SERIAL NOT NULL PRIMARY KEY,
            "name"                  TEXT NOT NULL,
            "template_type"         TEXT NOT NULL,
            "file_storage_key"      TEXT NOT NULL,
            "placeholders"          JSONB NOT NULL,
            "description"           TEXT,
            "is_active"             BOOLEAN NOT NULL DEFAULT TRUE,
            "created_by_hub_user_id" INT,
            "created_at"            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE "contract_template" IS '合同模板（admin 管理）。';

        -- ─── contract_draft ────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS "contract_draft" (
            "id"                       SERIAL NOT NULL PRIMARY KEY,
            "template_id"              INT,
            "requester_hub_user_id"    INT NOT NULL,
            "customer_id"              INT NOT NULL,
            "items"                    JSONB NOT NULL,
            "rendered_file_storage_key" TEXT,
            "status"                   TEXT NOT NULL DEFAULT 'generated',
            "conversation_id"          VARCHAR(200),
            "created_at"               TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE "contract_draft" IS 'Agent 生成的合同草稿（不需审批流）。';

        -- ─── voucher_draft ─────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS "voucher_draft" (
            "id"                      SERIAL NOT NULL PRIMARY KEY,
            "requester_hub_user_id"   INT NOT NULL,
            "voucher_data"            JSONB NOT NULL,
            "rule_matched"            TEXT,
            "status"                  TEXT NOT NULL DEFAULT 'pending'
                CONSTRAINT "chk_voucher_draft_status"
                CHECK (status IN ('pending','creating','created','approved','rejected')),
            "creating_started_at"     TIMESTAMPTZ,
            "approved_by_hub_user_id" INT,
            "approved_at"             TIMESTAMPTZ,
            "rejection_reason"        TEXT,
            "erp_voucher_id"          INT,
            "conversation_id"         VARCHAR(200),
            "confirmation_action_id"  VARCHAR(64),
            "created_at"              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE "voucher_draft" IS '凭证草稿（五状态机 + creating 租约防重建）。';
        CREATE INDEX IF NOT EXISTS "idx_voucher_draft_pending"
            ON "voucher_draft" ("status", "created_at");
        CREATE INDEX IF NOT EXISTS "idx_voucher_draft_creating_lease"
            ON "voucher_draft" ("status", "creating_started_at");
        CREATE UNIQUE INDEX IF NOT EXISTS "idx_voucher_draft_action_id_unique"
            ON "voucher_draft" ("requester_hub_user_id", "confirmation_action_id")
            WHERE "confirmation_action_id" IS NOT NULL;

        -- ─── price_adjustment_request ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS "price_adjustment_request" (
            "id"                      SERIAL NOT NULL PRIMARY KEY,
            "requester_hub_user_id"   INT NOT NULL,
            "customer_id"             INT NOT NULL,
            "product_id"              INT NOT NULL,
            "current_price"           DECIMAL(12,2),
            "new_price"               DECIMAL(12,2),
            "discount_pct"            DECIMAL(5,4),
            "reason"                  TEXT,
            "status"                  TEXT NOT NULL DEFAULT 'pending',
            "approved_by_hub_user_id" INT,
            "approved_at"             TIMESTAMPTZ,
            "rejection_reason"        TEXT,
            "conversation_id"         VARCHAR(200),
            "confirmation_action_id"  VARCHAR(64),
            "created_at"              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE "price_adjustment_request" IS '调价申请（pending/approved/rejected 三值）。';
        CREATE INDEX IF NOT EXISTS "idx_price_adj_pending"
            ON "price_adjustment_request" ("status", "created_at");
        CREATE UNIQUE INDEX IF NOT EXISTS "idx_price_adj_action_id_unique"
            ON "price_adjustment_request" ("requester_hub_user_id", "confirmation_action_id")
            WHERE "confirmation_action_id" IS NOT NULL;

        -- ─── stock_adjustment_request ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS "stock_adjustment_request" (
            "id"                      SERIAL NOT NULL PRIMARY KEY,
            "requester_hub_user_id"   INT NOT NULL,
            "product_id"              INT NOT NULL,
            "warehouse_id"            INT,
            "current_stock"           DECIMAL(12,4),
            "new_stock"               DECIMAL(12,4),
            "adjustment_qty"          DECIMAL(12,4),
            "reason"                  TEXT,
            "status"                  TEXT NOT NULL DEFAULT 'pending',
            "approved_by_hub_user_id" INT,
            "approved_at"             TIMESTAMPTZ,
            "rejection_reason"        TEXT,
            "conversation_id"         VARCHAR(200),
            "confirmation_action_id"  VARCHAR(64),
            "created_at"              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE "stock_adjustment_request" IS '库存调整申请（pending/approved/rejected 三值）。';
        CREATE INDEX IF NOT EXISTS "idx_stock_adj_pending"
            ON "stock_adjustment_request" ("status", "created_at");
        CREATE UNIQUE INDEX IF NOT EXISTS "idx_stock_adj_action_id_unique"
            ON "stock_adjustment_request" ("requester_hub_user_id", "confirmation_action_id")
            WHERE "confirmation_action_id" IS NOT NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    """回滚 Plan 6 迁移，删除全部 9 张表。

    警告：不可恢复操作，仅供开发期向下回滚使用。执行后将永久丢失以下所有数据：
      - conversation_log：所有 Agent 对话元数据
      - tool_call_log：所有 tool 调用明细（含 args/result/耗时）
      - user_memory / customer_memory / product_memory：三层 Agent 记忆
      - contract_template：所有合同模板
      - contract_draft：所有合同草稿
      - voucher_draft：所有凭证草稿（含五状态机数据）
      - price_adjustment_request / stock_adjustment_request：所有调价/库存调整申请

    生产环境禁止执行。
    """
    return """
        DROP TABLE IF EXISTS "stock_adjustment_request";
        DROP TABLE IF EXISTS "price_adjustment_request";
        DROP TABLE IF EXISTS "voucher_draft";
        DROP TABLE IF EXISTS "contract_draft";
        DROP TABLE IF EXISTS "contract_template";
        DROP TABLE IF EXISTS "product_memory";
        DROP TABLE IF EXISTS "customer_memory";
        DROP TABLE IF EXISTS "user_memory";
        DROP TABLE IF EXISTS "tool_call_log";
        DROP TABLE IF EXISTS "conversation_log";
    """
