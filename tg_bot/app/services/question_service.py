from dataclasses import dataclass

from app.config import Settings
from app.core.clock import Clock, utc_now
from app.core.enums import MemberRole, QuestionStatus, RateLimitScope
from app.core.errors import (
    EmptyQuestion,
    ProjectNotFound,
    QuestionAlreadyAnswered,
    QuestionNotAnswered,
    QuestionNotFound,
    QuestionTooLong,
    RateLimited,
)
from app.llm.gateway import AskResult, LLMGateway
from app.services.access_service import AccessGuard
from app.storage.postgres.membership_work import PostgresMembershipWork
from app.storage.postgres.models import Project, Question, User
from app.storage.postgres.project_work import PostgresProjectWork
from app.storage.postgres.question_work import PostgresQuestionWork
from app.storage.redis.answer_cache_work import RedisAnswerCacheWork
from app.storage.redis.rate_limit_work import RedisRateLimitWork

SCREENSHOT_PLACEHOLDER = "[скриншот]"


@dataclass(frozen=True, slots=True)
class Asker:
    """Кто и где задал вопрос: туда же придёт ответ админа."""

    telegram_id: int
    chat_id: int
    message_id: int


@dataclass(frozen=True, slots=True)
class AskOutcome:
    """Либо ответ из документации, либо вопрос, переданный админам."""

    answer: str | None = None
    question: Question | None = None


class QuestionService:
    MAX_QUESTION_LEN = 4000
    MAX_ANSWER_LEN = 4000

    def __init__(
        self,
        llm_gateway: LLMGateway,
        answer_cache_work: RedisAnswerCacheWork,
        rate_limit_work: RedisRateLimitWork,
        question_work: PostgresQuestionWork,
        project_work: PostgresProjectWork,
        membership_work: PostgresMembershipWork,
        access_guard: AccessGuard,
        settings: Settings,
        clock: Clock = utc_now,
    ) -> None:
        self._llm = llm_gateway
        self._cache = answer_cache_work
        self._rate_limit_work = rate_limit_work
        self._question_work = question_work
        self._project_work = project_work
        self._membership_work = membership_work
        self._access = access_guard
        self._settings = settings
        self._clock = clock

    async def ask_text(
        self, project_id: int, text: str, asker: Asker, scope: RateLimitScope
    ) -> AskOutcome:
        text = self._validate(text)
        await self._check_rate(scope, asker)
        project = await self._get_project(project_id)

        cached = await self._cache.get(project.id, project.docs_version, text)
        if cached is not None:
            return AskOutcome(answer=cached)

        result = await self._llm.ask(project.id, text)
        if answer := self._answer_of(result):
            await self._cache.set(project.id, project.docs_version, text, answer)
            return AskOutcome(answer=answer)
        return AskOutcome(question=await self._save_question(project.id, text, asker, None))

    async def ask_image(
        self,
        project_id: int,
        image: bytes,
        image_file_id: str,
        caption: str | None,
        asker: Asker,
    ) -> AskOutcome:
        caption = (caption or "").strip() or None
        if caption is not None and len(caption) > self.MAX_QUESTION_LEN:
            raise QuestionTooLong
        await self._check_rate(RateLimitScope.QUESTION, asker)
        project = await self._get_project(project_id)

        result = await self._llm.ask_image(project.id, image, caption)
        if answer := self._answer_of(result):
            return AskOutcome(answer=answer)
        text = caption or SCREENSHOT_PLACEHOLDER
        return AskOutcome(
            question=await self._save_question(project.id, text, asker, image_file_id)
        )

    async def get_admin_chat_ids(self, project_id: int) -> list[int]:
        return await self._membership_work.get_member_telegram_ids(project_id, MemberRole.ADMIN)

    async def get_question_for_admin(self, admin: User, question_id: int) -> Question:
        question = await self._question_work.get_by_id(question_id)
        if question is None:
            raise QuestionNotFound
        await self._access.require_admin(admin.id, question.project_id)
        return question

    async def answer_question(self, admin: User, question_id: int, answer: str) -> Question:
        question = await self.get_question_for_admin(admin, question_id)
        answer = answer.strip()
        if not answer:
            raise EmptyQuestion
        if len(answer) > self.MAX_ANSWER_LEN:
            raise QuestionTooLong
        now = self._clock()
        if not await self._question_work.mark_answered(question.id, admin.id, answer, now):
            raise QuestionAlreadyAnswered
        question.status = QuestionStatus.ANSWERED
        question.answer = answer
        question.answered_by_id = admin.id
        question.answered_at = now
        return question

    async def save_to_knowledge(self, admin: User, question_id: int) -> None:
        question = await self.get_question_for_admin(admin, question_id)
        if question.status != QuestionStatus.ANSWERED or not question.answer:
            raise QuestionNotAnswered
        await self._llm.save_knowledge(question.project_id, question.text, question.answer)

    def _validate(self, text: str) -> str:
        text = text.strip()
        if not text:
            raise EmptyQuestion
        if len(text) > self.MAX_QUESTION_LEN:
            raise QuestionTooLong
        return text

    async def _check_rate(self, scope: RateLimitScope, asker: Asker) -> None:
        subject = asker.chat_id if scope == RateLimitScope.GROUP_QUESTION else asker.telegram_id
        allowed = await self._rate_limit_work.hit(
            scope,
            subject,
            self._settings.question_rate_limit,
            self._settings.question_rate_window_seconds,
        )
        if not allowed:
            raise RateLimited

    async def _get_project(self, project_id: int) -> Project:
        project = await self._project_work.get_by_id(project_id)
        if project is None:
            raise ProjectNotFound
        return project

    @staticmethod
    def _answer_of(result: AskResult) -> str | None:
        if result.found and result.answer and result.answer.strip():
            return result.answer.strip()
        return None

    async def _save_question(
        self, project_id: int, text: str, asker: Asker, image_file_id: str | None
    ) -> Question:
        return await self._question_work.create(
            project_id=project_id,
            asker_telegram_id=asker.telegram_id,
            chat_id=asker.chat_id,
            message_id=asker.message_id,
            text=text,
            image_file_id=image_file_id,
        )
