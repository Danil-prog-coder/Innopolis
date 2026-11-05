import logging
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
from app.llm.gateway import (
    AskResult,
    KnowledgeSnapshot,
    LLMContractError,
    LLMGateway,
    NoAnswerReason,
    Source,
)
from app.services.access_service import AccessGuard
from app.storage.postgres.membership_work import PostgresMembershipWork
from app.storage.postgres.models import Project, Question, User
from app.storage.postgres.project_work import PostgresProjectWork
from app.storage.postgres.question_work import PostgresQuestionWork
from app.storage.redis.answer_cache_work import RedisAnswerCacheWork
from app.storage.redis.rate_limit_work import RedisRateLimitWork

SCREENSHOT_PLACEHOLDER = "[скриншот]"

logger = logging.getLogger(__name__)


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
    sources: tuple[Source, ...] = ()
    snapshot: KnowledgeSnapshot | None = None
    no_answer_reason: NoAnswerReason | None = None


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
        # Snapshot фиксируется до retrieval и не перечитывается после ответа.
        snapshot = KnowledgeSnapshot.compat(project.id, project.docs_version)

        cached = await self._cache.get(project.id, snapshot.knowledge_version, text)
        if cached is not None:
            cached_result = self._cached_result(cached, snapshot)
            if cached_result is not None:
                return AskOutcome(
                    answer=cached_result.answer,
                    sources=cached_result.sources,
                    snapshot=snapshot,
                )

        result = await self._llm.ask(project.id, text, snapshot=snapshot)
        result = self._checked_result(result, snapshot)
        if result.found:
            await self._cache.set(
                project.id, snapshot.knowledge_version, text, result.to_cache_json()
            )
            return AskOutcome(
                answer=result.answer,
                sources=result.sources,
                snapshot=snapshot,
            )
        return AskOutcome(
            question=await self._save_question(project.id, text, asker, None),
            snapshot=snapshot,
            no_answer_reason=result.no_answer_reason,
        )

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
        snapshot = KnowledgeSnapshot.compat(project.id, project.docs_version)

        result = await self._llm.ask_image(project.id, image, caption, snapshot=snapshot)
        result = self._checked_result(result, snapshot)
        if result.found:
            return AskOutcome(
                answer=result.answer,
                sources=result.sources,
                snapshot=snapshot,
            )
        text = caption or SCREENSHOT_PLACEHOLDER
        return AskOutcome(
            question=await self._save_question(project.id, text, asker, image_file_id),
            snapshot=snapshot,
            no_answer_reason=result.no_answer_reason,
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
    def _cached_result(raw: str, snapshot: KnowledgeSnapshot) -> AskResult | None:
        """Принять только полный ответ той же версии; иначе — miss."""
        try:
            cached = AskResult.from_json(raw)
        except (TypeError, ValueError, KeyError) as error:
            logger.warning("Ignoring invalid answer cache: %s", error)
            return None
        if not cached.found or not cached.sources:
            return None
        if cached.usage is not None or cached.request_id is not None:
            # Cache не должен нести историческую попытку/стоимость.
            return None
        if cached.snapshot != snapshot:
            return None
        for source in cached.sources:
            if (
                source.project_id != snapshot.project_id
                or source.generation_id != snapshot.generation_id
                or source.knowledge_version != snapshot.knowledge_version
            ):
                return None
        return cached

    @staticmethod
    def _checked_result(result: AskResult, snapshot: KnowledgeSnapshot) -> AskResult:
        """Проверить scope. Ошибка provenance не превращается в no-answer."""
        if not isinstance(result, AskResult):
            raise LLMContractError("gateway returned invalid AskResult")
        if result.snapshot is None:
            raise LLMContractError("gateway result has no snapshot")
        if result.snapshot != snapshot:
            raise LLMContractError("gateway returned result for a different snapshot")
        if result.found:
            if not result.sources:
                raise LLMContractError("found=True requires verified sources")
            for source in result.sources:
                if (
                    source.project_id != snapshot.project_id
                    or source.generation_id != snapshot.generation_id
                    or source.knowledge_version != snapshot.knowledge_version
                ):
                    raise LLMContractError("source does not belong to snapshot")
        return result

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
