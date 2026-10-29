"""Фабрики всех объектов бота. Хендлеры получают их через ``Depends(get_obj_...)``.

Фабрики без параметров под ``@cache`` — синглтоны на процесс (клиенты хранилищ, бот).
Остальные дешёвые: создают Work-класс или сервис поверх синглтонов на каждый апдейт.
"""

from functools import cache

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisStorage

from app.bot.notifier import Notifier
from app.config import Settings
from app.core.depends import Depends, DependsResolver
from app.core.security import ProjectPasswordManager
from app.llm.gateway import LLMGateway, StubLLMGateway
from app.services.access_service import AccessGuard
from app.services.docs_service import DocsService
from app.services.group_chat_service import GroupChatService
from app.services.invite_service import InviteService
from app.services.membership_service import MembershipService
from app.services.project_service import ProjectService
from app.services.question_service import QuestionService
from app.storage.enums import RedisKey
from app.storage.postgres.client import PostgresClient
from app.storage.postgres.config import MODELS_MODULES
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

# --- Синглтоны ---


@cache
def get_obj_settings() -> Settings:
    return Settings()


@cache
def get_obj_resolver() -> DependsResolver:
    return DependsResolver()


@cache
def get_obj_bot() -> Bot:
    return Bot(
        token=get_obj_settings().bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


@cache
def get_obj_postgres_client() -> PostgresClient:
    return PostgresClient(get_obj_settings().postgres_dsn, MODELS_MODULES)


@cache
def get_obj_redis_client() -> RedisClient:
    return RedisClient(get_obj_settings().redis_url)


@cache
def get_obj_llm_gateway() -> LLMGateway:
    # Здесь подключается реальная реализация ИИ-части, когда она появится
    return StubLLMGateway()


@cache
def get_obj_password_manager() -> ProjectPasswordManager:
    return ProjectPasswordManager()


@cache
def get_obj_notifier() -> Notifier:
    return Notifier(get_obj_bot())


@cache
def get_obj_fsm_storage() -> RedisStorage:
    """Требует подключённого RedisClient: вызывать после connect()."""
    return RedisStorage(
        redis=get_obj_redis_client().redis,
        key_builder=DefaultKeyBuilder(prefix=RedisKey.FSM_PREFIX),
    )


# --- Postgres Work-классы ---


def get_obj_user_work(
    client: PostgresClient = Depends(get_obj_postgres_client),
) -> PostgresUserWork:
    return PostgresUserWork(client)


def get_obj_project_work(
    client: PostgresClient = Depends(get_obj_postgres_client),
) -> PostgresProjectWork:
    return PostgresProjectWork(client)


def get_obj_membership_work(
    client: PostgresClient = Depends(get_obj_postgres_client),
) -> PostgresMembershipWork:
    return PostgresMembershipWork(client)


def get_obj_invite_work(
    client: PostgresClient = Depends(get_obj_postgres_client),
) -> PostgresInviteWork:
    return PostgresInviteWork(client)


def get_obj_question_work(
    client: PostgresClient = Depends(get_obj_postgres_client),
) -> PostgresQuestionWork:
    return PostgresQuestionWork(client)


def get_obj_doc_update_work(
    client: PostgresClient = Depends(get_obj_postgres_client),
) -> PostgresDocUpdateWork:
    return PostgresDocUpdateWork(client)


def get_obj_group_chat_work(
    client: PostgresClient = Depends(get_obj_postgres_client),
) -> PostgresGroupChatWork:
    return PostgresGroupChatWork(client)


def get_obj_project_ban_work(
    client: PostgresClient = Depends(get_obj_postgres_client),
) -> PostgresProjectBanWork:
    return PostgresProjectBanWork(client)


# --- Redis Work-классы ---


def get_obj_answer_cache_work(
    client: RedisClient = Depends(get_obj_redis_client),
    settings: Settings = Depends(get_obj_settings),
) -> RedisAnswerCacheWork:
    return RedisAnswerCacheWork(client, settings.answer_cache_ttl_seconds)


def get_obj_rate_limit_work(
    client: RedisClient = Depends(get_obj_redis_client),
) -> RedisRateLimitWork:
    return RedisRateLimitWork(client)


def get_obj_password_reveal_work(
    client: RedisClient = Depends(get_obj_redis_client),
    settings: Settings = Depends(get_obj_settings),
) -> RedisPasswordRevealWork:
    return RedisPasswordRevealWork(client, settings.password_reveal_ttl_seconds)


def get_obj_docs_draft_work(
    client: RedisClient = Depends(get_obj_redis_client),
) -> RedisDocsDraftWork:
    return RedisDocsDraftWork(client)


# --- Сервисы ---


def get_obj_access_guard(
    membership_work: PostgresMembershipWork = Depends(get_obj_membership_work),
) -> AccessGuard:
    return AccessGuard(membership_work)


def get_obj_project_service(
    project_work: PostgresProjectWork = Depends(get_obj_project_work),
    membership_work: PostgresMembershipWork = Depends(get_obj_membership_work),
    user_work: PostgresUserWork = Depends(get_obj_user_work),
    project_ban_work: PostgresProjectBanWork = Depends(get_obj_project_ban_work),
    password_manager: ProjectPasswordManager = Depends(get_obj_password_manager),
    password_reveal_work: RedisPasswordRevealWork = Depends(get_obj_password_reveal_work),
    rate_limit_work: RedisRateLimitWork = Depends(get_obj_rate_limit_work),
    access_guard: AccessGuard = Depends(get_obj_access_guard),
    settings: Settings = Depends(get_obj_settings),
) -> ProjectService:
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


def get_obj_invite_service(
    invite_work: PostgresInviteWork = Depends(get_obj_invite_work),
    membership_work: PostgresMembershipWork = Depends(get_obj_membership_work),
    user_work: PostgresUserWork = Depends(get_obj_user_work),
    project_work: PostgresProjectWork = Depends(get_obj_project_work),
    project_ban_work: PostgresProjectBanWork = Depends(get_obj_project_ban_work),
    access_guard: AccessGuard = Depends(get_obj_access_guard),
) -> InviteService:
    return InviteService(
        invite_work, membership_work, user_work, project_work, project_ban_work, access_guard
    )


def get_obj_membership_service(
    membership_work: PostgresMembershipWork = Depends(get_obj_membership_work),
    user_work: PostgresUserWork = Depends(get_obj_user_work),
    project_work: PostgresProjectWork = Depends(get_obj_project_work),
    project_ban_work: PostgresProjectBanWork = Depends(get_obj_project_ban_work),
    invite_work: PostgresInviteWork = Depends(get_obj_invite_work),
    access_guard: AccessGuard = Depends(get_obj_access_guard),
) -> MembershipService:
    return MembershipService(
        membership_work, user_work, project_work, project_ban_work, invite_work, access_guard
    )


def get_obj_question_service(
    llm_gateway: LLMGateway = Depends(get_obj_llm_gateway),
    answer_cache_work: RedisAnswerCacheWork = Depends(get_obj_answer_cache_work),
    rate_limit_work: RedisRateLimitWork = Depends(get_obj_rate_limit_work),
    question_work: PostgresQuestionWork = Depends(get_obj_question_work),
    project_work: PostgresProjectWork = Depends(get_obj_project_work),
    membership_work: PostgresMembershipWork = Depends(get_obj_membership_work),
    access_guard: AccessGuard = Depends(get_obj_access_guard),
    settings: Settings = Depends(get_obj_settings),
) -> QuestionService:
    return QuestionService(
        llm_gateway,
        answer_cache_work,
        rate_limit_work,
        question_work,
        project_work,
        membership_work,
        access_guard,
        settings,
    )


def get_obj_docs_service(
    llm_gateway: LLMGateway = Depends(get_obj_llm_gateway),
    project_work: PostgresProjectWork = Depends(get_obj_project_work),
    doc_update_work: PostgresDocUpdateWork = Depends(get_obj_doc_update_work),
    membership_work: PostgresMembershipWork = Depends(get_obj_membership_work),
    access_guard: AccessGuard = Depends(get_obj_access_guard),
) -> DocsService:
    return DocsService(llm_gateway, project_work, doc_update_work, membership_work, access_guard)


def get_obj_group_chat_service(
    group_chat_work: PostgresGroupChatWork = Depends(get_obj_group_chat_work),
    project_service: ProjectService = Depends(get_obj_project_service),
    project_work: PostgresProjectWork = Depends(get_obj_project_work),
    access_guard: AccessGuard = Depends(get_obj_access_guard),
) -> GroupChatService:
    return GroupChatService(group_chat_work, project_service, project_work, access_guard)
