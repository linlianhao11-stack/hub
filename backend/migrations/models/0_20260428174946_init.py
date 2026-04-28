from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "ai_provider" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "provider_type" VARCHAR(30) NOT NULL,
    "name" VARCHAR(100) NOT NULL,
    "encrypted_api_key" BYTEA NOT NULL,
    "base_url" VARCHAR(500) NOT NULL,
    "model" VARCHAR(100) NOT NULL,
    "config" JSONB NOT NULL,
    "status" VARCHAR(20) NOT NULL  DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS "audit_log" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "who_hub_user_id" INT NOT NULL,
    "action" VARCHAR(80) NOT NULL,
    "target_type" VARCHAR(50),
    "target_id" VARCHAR(64),
    "detail" JSONB NOT NULL,
    "ip" VARCHAR(45),
    "user_agent" VARCHAR(500),
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON TABLE "audit_log" IS 'admin 操作审计（创建 ApiKey / 解绑 / 改角色等）。';
CREATE TABLE IF NOT EXISTS "bootstrap_token" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "token_hash" VARCHAR(255) NOT NULL,
    "expires_at" TIMESTAMPTZ NOT NULL,
    "used_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON TABLE "bootstrap_token" IS '初始化向导一次性 token。';
CREATE TABLE IF NOT EXISTS "channel_app" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "channel_type" VARCHAR(30) NOT NULL,
    "name" VARCHAR(100) NOT NULL,
    "encrypted_app_key" BYTEA NOT NULL,
    "encrypted_app_secret" BYTEA NOT NULL,
    "robot_id" VARCHAR(200),
    "status" VARCHAR(20) NOT NULL  DEFAULT 'active',
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "downstream_system" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "downstream_type" VARCHAR(30) NOT NULL,
    "name" VARCHAR(100) NOT NULL,
    "base_url" VARCHAR(500) NOT NULL,
    "encrypted_apikey" BYTEA NOT NULL,
    "apikey_scopes" JSONB NOT NULL,
    "status" VARCHAR(20) NOT NULL  DEFAULT 'active',
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "hub_permission" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "code" VARCHAR(120) NOT NULL UNIQUE,
    "resource" VARCHAR(40) NOT NULL,
    "sub_resource" VARCHAR(40) NOT NULL,
    "action" VARCHAR(20) NOT NULL,
    "name" VARCHAR(100) NOT NULL,
    "description" TEXT
);
CREATE TABLE IF NOT EXISTS "hub_role" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "code" VARCHAR(80) NOT NULL UNIQUE,
    "name" VARCHAR(100) NOT NULL,
    "description" TEXT,
    "is_builtin" BOOL NOT NULL  DEFAULT False,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "hub_user" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "display_name" VARCHAR(100) NOT NULL,
    "status" VARCHAR(20) NOT NULL  DEFAULT 'active',
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "channel_user_binding" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "channel_type" VARCHAR(30) NOT NULL,
    "channel_userid" VARCHAR(200) NOT NULL,
    "display_meta" JSONB NOT NULL,
    "status" VARCHAR(20) NOT NULL  DEFAULT 'active',
    "bound_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "revoked_at" TIMESTAMPTZ,
    "revoked_reason" VARCHAR(100),
    "hub_user_id" INT NOT NULL REFERENCES "hub_user" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_channel_use_channel_3e6b05" UNIQUE ("channel_type", "channel_userid")
);
CREATE TABLE IF NOT EXISTS "downstream_identity" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "downstream_type" VARCHAR(30) NOT NULL,
    "downstream_user_id" INT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "hub_user_id" INT NOT NULL REFERENCES "hub_user" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_downstream__hub_use_7b05cc" UNIQUE ("hub_user_id", "downstream_type")
);
CREATE TABLE IF NOT EXISTS "erp_user_state_cache" (
    "erp_active" BOOL NOT NULL,
    "checked_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "hub_user_id" INT NOT NULL  PRIMARY KEY REFERENCES "hub_user" ("id") ON DELETE CASCADE
);
COMMENT ON TABLE "erp_user_state_cache" IS '缓存 hub_user 对应 ERP 是否启用（10 分钟 TTL）。';
CREATE TABLE IF NOT EXISTS "hub_user_role" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "assigned_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "assigned_by_hub_user_id" INT,
    "hub_user_id" INT NOT NULL REFERENCES "hub_user" ("id") ON DELETE CASCADE,
    "role_id" INT NOT NULL REFERENCES "hub_role" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_hub_user_ro_hub_use_833fb5" UNIQUE ("hub_user_id", "role_id")
);
COMMENT ON TABLE "hub_user_role" IS '中间表显式建模便于带审计字段（assigned_by / assigned_at）。';
CREATE TABLE IF NOT EXISTS "meta_audit_log" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "who_hub_user_id" INT NOT NULL,
    "viewed_task_id" VARCHAR(64) NOT NULL,
    "viewed_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "ip" VARCHAR(45)
);
COMMENT ON TABLE "meta_audit_log" IS '看 payload 留痕（\"谁在监控监控员\"）。';
CREATE TABLE IF NOT EXISTS "system_config" (
    "key" VARCHAR(100) NOT NULL  PRIMARY KEY,
    "value" JSONB NOT NULL,
    "description" TEXT,
    "updated_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "updated_by_hub_user_id" INT
);
COMMENT ON TABLE "system_config" IS 'key-value 配置表（告警接收人、TTL、运行时常量等）。';
CREATE TABLE IF NOT EXISTS "task_log" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "task_id" VARCHAR(64) NOT NULL UNIQUE,
    "task_type" VARCHAR(80) NOT NULL,
    "channel_type" VARCHAR(30) NOT NULL,
    "channel_userid" VARCHAR(200) NOT NULL,
    "hub_user_id" INT,
    "status" VARCHAR(40) NOT NULL,
    "intent_parser" VARCHAR(20),
    "intent_confidence" DOUBLE PRECISION,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "finished_at" TIMESTAMPTZ,
    "duration_ms" INT,
    "error_classification" VARCHAR(50),
    "error_summary" VARCHAR(500),
    "retry_count" INT NOT NULL  DEFAULT 0
);
COMMENT ON TABLE "task_log" IS '元数据，长保留 365 天。';
CREATE TABLE IF NOT EXISTS "task_payload" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "encrypted_request" BYTEA NOT NULL,
    "encrypted_erp_calls" BYTEA,
    "encrypted_response" BYTEA NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "expires_at" TIMESTAMPTZ NOT NULL,
    "task_log_id" INT NOT NULL UNIQUE REFERENCES "task_log" ("id") ON DELETE CASCADE
);
COMMENT ON TABLE "task_payload" IS '敏感数据，加密 + 短保留 30 天。';
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS "hub_role_permission" (
    "hub_role_id" INT NOT NULL REFERENCES "hub_role" ("id") ON DELETE CASCADE,
    "hubpermission_id" INT NOT NULL REFERENCES "hub_permission" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_hub_role_pe_hub_rol_958eda" ON "hub_role_permission" ("hub_role_id", "hubpermission_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """
