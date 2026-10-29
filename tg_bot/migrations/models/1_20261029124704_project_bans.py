from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "project_bans" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "last_invite_id" INT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "project_id" INT NOT NULL REFERENCES "projects" ("id") ON DELETE CASCADE,
    "user_id" INT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_project_ban_user_id_8958e8" UNIQUE ("user_id", "project_id")
);
COMMENT ON TABLE "project_bans" IS 'Пользователь удалён из проекта: старые приглашения и пароль ему больше не помогут.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "project_bans";"""


MODELS_STATE = (
    "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"
    "7fE/w5PsQw=="
)
