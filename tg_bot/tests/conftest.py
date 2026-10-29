import os

import pytest
from fakeredis import FakeAsyncRedis
from tortoise import Tortoise

from app.config import Settings
from app.core.enums import DocsMode, MemberRole
from app.core.security import ProjectPasswordManager
from app.llm.gateway import AskResult, LLMGateway, LLMUnavailableError
from app.services.access_service import AccessGuard
from app.services.docs_service import DocsService
from app.services.group_chat_service import GroupChatService
from app.services.invite_service import InviteService
from app.services.membership_service import MembershipService
from app.services.project_service import ProjectService
from app.services.question_service import QuestionService
from app.storage.enums import TableName
from app.storage.postgres.client import PostgresClient
from app.storage.postgres.doc_update_work import PostgresDocUpdateWork
from app.storage.postgres.group_chat_work import PostgresGroupChatWork
from app.storage.postgres.invite_work import PostgresInviteWork
from app.storage.postgres.membership_work import PostgresMembershipWork
from app.storage.postgres.project_ban_work import PostgresProjectBanWork
from app.storage.postgres.project_work import PostgresProjectWork
from app.storage.postgres.question_work import PostgresQuestionWork
from app.storage.postgres.user_work import PostgresUserWork
from app.storage.redis.answer_cache_work import RedisAnswerCacheWork
from app.storage.redis.client import RedisClient
from app.storage.redis.docs_draft_work import RedisDocsDraftWork
from app.storage.redis.password_reveal_work import RedisPasswordRevealWork
from app.storage.redis.rate_limit_work import RedisRateLimitWork

TEST_MODULES = {"models": ["app.storage.postgres.models"]}


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        bot_token="1:test",
        postgres_db="test",
        postgres_user="test",
        postgres_password="test",
        redis_password="",
        password_attempts_limit=5,
        question_rate_limit=10,
        docs_max_files=10,
    )


# По умолчанию тесты идут на sqlite в памяти и fakeredis. Чтобы прогнать их на настоящих
# Postgres и Redis (docker compose), задайте TEST_POSTGRES_URL и TEST_REDIS_URL — ОТДЕЛЬНУЮ
# базу и номер БД Redis: после каждого теста они очищаются.
TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL")


@pytest.fixture
async def pg_client():
    client = PostgresClient(TEST_POSTGRES_URL or "sqlite://:memory:", TEST_MODULES)
    await client.connect()
    try:
        await client.generate_schemas()
        yield client
    finally:
        if TEST_POSTGRES_URL:
            tables = ", ".join(f'"{name}"' for name in TableName)
            await Tortoise.get_connection("default").execute_script(
                f"TRUNCATE {tables} RESTART IDENTITY CASCADE"
            )
        await client.close()


@pytest.fixture
async def redis_client():
    if TEST_REDIS_URL:
        client = RedisClient(TEST_REDIS_URL)
    else:
        client = RedisClient("redis://fake", redis=FakeAsyncRedis(decode_responses=True))
    await client.connect()
    try:
        yield client
    finally:
        if TEST_REDIS_URL:
            await client.redis.flushdb()
        await client.close()


# --- Postgres Work-классы ---


@pytest.fixture
def user_work(pg_client):
    return PostgresUserWork(pg_client)


@pytest.fixture
def project_work(pg_client):
    return PostgresProjectWork(pg_client)


@pytest.fixture
def membership_work(pg_client):
    return PostgresMembershipWork(pg_client)


@pytest.fixture
def invite_work(pg_client):
    return PostgresInviteWork(pg_client)


@pytest.fixture
def question_work(pg_client):
    return PostgresQuestionWork(pg_client)


@pytest.fixture
def project_ban_work(pg_client):
    return PostgresProjectBanWork(pg_client)


@pytest.fixture
def doc_update_work(pg_client):
    return PostgresDocUpdateWork(pg_client)


@pytest.fixture
def group_chat_work(pg_client):
    return PostgresGroupChatWork(pg_client)


# --- Типовые данные: проект, его создатель-админ, второй админ, пользователь, посторонний ---


@pytest.fixture
async def admin(user_work):
    return await user_work.upsert_from_telegram(1001, "admin", "Admin")


@pytest.fixture
async def user(user_work):
    return await user_work.upsert_from_telegram(1002, "user", "User")


@pytest.fixture
async def second_admin(user_work):
    return await user_work.upsert_from_telegram(1003, "admin2", "Second Admin")


@pytest.fixture
async def outsider(user_work):
    return await user_work.upsert_from_telegram(1004, "outsider", "Outsider")


@pytest.fixture
async def project(project_work, membership_work, admin, user, second_admin):
    project = await project_work.create("Альфа", "a1b2c3d4", "hash", admin.id)
    await membership_work.add_or_upgrade(admin.id, project.id, MemberRole.ADMIN)
    await membership_work.add_or_upgrade(second_admin.id, project.id, MemberRole.ADMIN)
    await membership_work.add_or_upgrade(user.id, project.id, MemberRole.USER)
    return project


@pytest.fixture
async def other_project(project_work, membership_work, outsider):
    project = await project_work.create("Бета", "deadbeef", "hash", outsider.id)
    await membership_work.add_or_upgrade(outsider.id, project.id, MemberRole.ADMIN)
    return project


# --- Redis Work-классы ---


@pytest.fixture
def answer_cache_work(redis_client):
    return RedisAnswerCacheWork(redis_client, ttl_seconds=60)


@pytest.fixture
def rate_limit_work(redis_client):
    return RedisRateLimitWork(redis_client)


@pytest.fixture
def password_reveal_work(redis_client):
    return RedisPasswordRevealWork(redis_client, ttl_seconds=600)


@pytest.fixture
def docs_draft_work(redis_client):
    return RedisDocsDraftWork(redis_client)


# --- Сервисы ---


@pytest.fixture
def password_manager():
    return ProjectPasswordManager()


@pytest.fixture
def access_guard(membership_work):
    return AccessGuard(membership_work)


@pytest.fixture
def project_service(
    project_work,
    membership_work,
    user_work,
    project_ban_work,
    password_manager,
    password_reveal_work,
    rate_limit_work,
    access_guard,
    settings,
):
    return ProjectService(
        project_work,
        membership_work,
        user_work,
        project_ban_work,
        password_manager,
        password_reveal_work,
        rate_limit_work,
        access_guard,
        settings,
    )


@pytest.fixture
def make_invite_service(
    invite_work, membership_work, user_work, project_work, project_ban_work, access_guard
):
    def factory(clock=None):
        kwargs = {"clock": clock} if clock else {}
        return InviteService(
            invite_work,
            membership_work,
            user_work,
            project_work,
            project_ban_work,
            access_guard,
            **kwargs,
        )

    return factory


@pytest.fixture
def invite_service(make_invite_service):
    return make_invite_service()


@pytest.fixture
def membership_service(
    membership_work, user_work, project_work, project_ban_work, invite_work, access_guard
):
    return MembershipService(
        membership_work, user_work, project_work, project_ban_work, invite_work, access_guard
    )


# --- Граница с ИИ ---


class FakeLLMGateway(LLMGateway):
    """Тестовая реализация: настраиваемый ответ, сбой и журнал вызовов."""

    def __init__(self) -> None:
        self.answer = AskResult(found=False, answer=None)
        self.summary = "Сводка"
        self.fail = False
        self.ask_calls = 0
        self.last_project_id: int | None = None
        self.last_question: str | None = None
        self.uploads: list[tuple[int, DocsMode, list[str]]] = []
        self.knowledge: list[tuple[int, str, str]] = []

    def _check(self) -> None:
        if self.fail:
            raise LLMUnavailableError

    async def ask(self, project_id, question):
        self._check()
        self.ask_calls += 1
        self.last_project_id, self.last_question = project_id, question
        return self.answer

    async def ask_image(self, project_id, image, caption):
        self._check()
        self.last_project_id, self.last_question = project_id, caption
        return self.answer

    async def upload_documents(self, project_id, mode, files):
        self._check()
        self.uploads.append((project_id, mode, [f.filename for f in files]))
        return self.summary

    async def save_knowledge(self, project_id, question, answer):
        self._check()
        self.knowledge.append((project_id, question, answer))


@pytest.fixture
def fake_llm():
    return FakeLLMGateway()


@pytest.fixture
def question_service(
    fake_llm,
    answer_cache_work,
    rate_limit_work,
    question_work,
    project_work,
    membership_work,
    access_guard,
    settings,
):
    return QuestionService(
        fake_llm,
        answer_cache_work,
        rate_limit_work,
        question_work,
        project_work,
        membership_work,
        access_guard,
        settings,
    )


@pytest.fixture
def docs_service(fake_llm, project_work, doc_update_work, membership_work, access_guard):
    return DocsService(fake_llm, project_work, doc_update_work, membership_work, access_guard)


@pytest.fixture
def group_chat_service(group_chat_work, project_service, project_work, access_guard):
    return GroupChatService(group_chat_work, project_service, project_work, access_guard)


# --- Настоящие пароли для проектов из фикстур (хеш argon2 считается только где нужно) ---

PROJECT_PASSWORD = "a1b2c3d4-" + "A" * 24
OTHER_PROJECT_PASSWORD = "deadbeef-" + "B" * 24


@pytest.fixture
async def project_password(project, password_manager):
    project.password_hash = await password_manager.hash(PROJECT_PASSWORD)
    await project.save(update_fields=["password_hash"])
    return PROJECT_PASSWORD


@pytest.fixture
async def other_project_password(other_project, password_manager):
    other_project.password_hash = await password_manager.hash(OTHER_PROJECT_PASSWORD)
    await other_project.save(update_fields=["password_hash"])
    return OTHER_PROJECT_PASSWORD
