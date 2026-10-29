from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "users" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "telegram_id" BIGINT NOT NULL UNIQUE,
    "username" VARCHAR(64),
    "full_name" VARCHAR(256) NOT NULL,
    "current_project_id" INT,
    "created_at" TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS "projects" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "public_id" VARCHAR(8) NOT NULL UNIQUE,
    "name" VARCHAR(64) NOT NULL,
    "password_hash" VARCHAR(256) NOT NULL,
    "docs_version" INT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "creator_id" INT NOT NULL REFERENCES "users" ("id") ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS "doc_updates" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "mode" VARCHAR(16) NOT NULL,
    "summary" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "created_by_id" INT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    "project_id" INT NOT NULL REFERENCES "projects" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "doc_updates"."mode" IS 'APPEND: append\nREPLACE: replace';
CREATE TABLE IF NOT EXISTS "group_chats" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "chat_id" BIGINT NOT NULL UNIQUE,
    "bound_by_telegram_id" BIGINT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "project_id" INT NOT NULL REFERENCES "projects" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "invites" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "code" VARCHAR(64) NOT NULL UNIQUE,
    "expires_at" TIMESTAMPTZ NOT NULL,
    "revoked_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "created_by_id" INT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    "project_id" INT NOT NULL REFERENCES "projects" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_invites_project_e56e98" ON "invites" ("project_id", "expires_at");
CREATE TABLE IF NOT EXISTS "memberships" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "role" VARCHAR(16) NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "project_id" INT NOT NULL REFERENCES "projects" ("id") ON DELETE CASCADE,
    "user_id" INT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_memberships_user_id_24dee5" UNIQUE ("user_id", "project_id")
);
CREATE INDEX IF NOT EXISTS "idx_memberships_project_f4fae5" ON "memberships" ("project_id", "role");
COMMENT ON COLUMN "memberships"."role" IS 'ADMIN: admin\nUSER: user';
CREATE TABLE IF NOT EXISTS "questions" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "asker_telegram_id" BIGINT NOT NULL,
    "chat_id" BIGINT NOT NULL,
    "message_id" BIGINT NOT NULL,
    "text" TEXT NOT NULL,
    "image_file_id" VARCHAR(256),
    "status" VARCHAR(16) NOT NULL,
    "answer" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "answered_at" TIMESTAMPTZ,
    "answered_by_id" INT REFERENCES "users" ("id") ON DELETE SET NULL,
    "project_id" INT NOT NULL REFERENCES "projects" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_questions_project_0180e8" ON "questions" ("project_id", "status");
COMMENT ON COLUMN "questions"."status" IS 'PENDING: pending\nANSWERED: answered';
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSONB NOT NULL
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "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"
    "/Q+2tlxz"
)
