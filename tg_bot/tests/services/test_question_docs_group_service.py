import asyncio
from types import SimpleNamespace

import pytest

from app.core.enums import DocsMode, QuestionStatus, RateLimitScope
from app.core.errors import (
    DocUpdateNotFound,
    EmptyQuestion,
    FileTooLarge,
    GroupAnswersUnavailable,
    InvalidPassword,
    NoFilesToUpload,
    NotProjectAdmin,
    NotProjectMember,
    QuestionAlreadyAnswered,
    QuestionNotAnswered,
    QuestionTooLong,
    RateLimited,
    RemovedFromProject,
    TooManyAttempts,
)
from app.llm.gateway import (
    AskEventType,
    AskResult,
    DocFile,
    KnowledgeSnapshot,
    LLMUnavailableError,
)
from app.llm.published import PublishedSnapshotUnavailable
from app.services.docs_service import DocsService
from app.services.question_service import Asker, AskStreamState, QuestionService
from app.storage.postgres.models import Project

ASKER = Asker(telegram_id=1002, chat_id=1002, message_id=10)
FOUND = AskResult(found=True, answer="A", legacy_no_sources=True)


@pytest.fixture
async def pending_question(question_service, fake_llm, project):
    outcome = await question_service.ask_text(project.id, "Q?", ASKER, RateLimitScope.QUESTION)
    return outcome.question


async def test_ask_uses_cache_second_time(question_service, fake_llm, project):
    fake_llm.answer = FOUND
    for _ in range(2):
        outcome = await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)
        assert outcome.answer == "A" and outcome.question is None
    assert fake_llm.ask_calls == 1
    assert fake_llm.last_project_id == project.id


class NoPublishedSnapshot:
    async def get(self, project_id):
        return None


class FixedPublishedSnapshot:
    def __init__(self, snapshot: KnowledgeSnapshot) -> None:
        self.snapshot = snapshot

    async def get(self, project_id):
        assert project_id == self.snapshot.project_id
        return self.snapshot


def published_service(
    fake_llm,
    answer_cache_work,
    rate_limit_work,
    question_work,
    project_work,
    membership_work,
    access_guard,
    settings,
    snapshots,
) -> QuestionService:
    return QuestionService(
        fake_llm,
        answer_cache_work,
        rate_limit_work,
        question_work,
        project_work,
        membership_work,
        access_guard,
        settings,
        snapshots=snapshots,
    )


async def test_real_mode_without_published_snapshot_fails_closed(
    fake_llm,
    answer_cache_work,
    rate_limit_work,
    question_work,
    project_work,
    membership_work,
    access_guard,
    settings,
    project,
):
    service = published_service(
        fake_llm,
        answer_cache_work,
        rate_limit_work,
        question_work,
        project_work,
        membership_work,
        access_guard,
        settings,
        NoPublishedSnapshot(),
    )

    with pytest.raises(PublishedSnapshotUnavailable):
        await service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert fake_llm.ask_calls == 0


async def test_real_mode_passes_published_snapshot_to_gateway(
    fake_llm,
    answer_cache_work,
    rate_limit_work,
    question_work,
    project_work,
    membership_work,
    access_guard,
    settings,
    project,
):
    snapshot = KnowledgeSnapshot(project.id, "gen-published", 1)
    service = published_service(
        fake_llm,
        answer_cache_work,
        rate_limit_work,
        question_work,
        project_work,
        membership_work,
        access_guard,
        settings,
        FixedPublishedSnapshot(snapshot),
    )
    fake_llm.answer = FOUND

    outcome = await service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert outcome.answer == "A"
    assert outcome.snapshot == snapshot
    assert fake_llm.last_snapshot == snapshot


async def test_real_mode_group_answer_fails_closed_for_foreign_group(
    fake_llm,
    answer_cache_work,
    rate_limit_work,
    question_work,
    project_work,
    membership_work,
    access_guard,
    settings,
    project,
):
    real_settings = settings.model_copy(update={"rag_mode": "real"})
    service = QuestionService(
        fake_llm,
        answer_cache_work,
        rate_limit_work,
        question_work,
        project_work,
        membership_work,
        access_guard,
        real_settings,
        snapshots=NoPublishedSnapshot(),
    )
    foreign_group_asker = Asker(telegram_id=9999, chat_id=-1009999, message_id=1)

    with pytest.raises(GroupAnswersUnavailable):
        await service.ask_text(
            project.id,
            "закрытый вопрос",
            foreign_group_asker,
            RateLimitScope.GROUP_QUESTION,
        )

    assert fake_llm.ask_calls == 0


async def test_stream_answer_and_cache_hit(question_service, fake_llm, project):
    fake_llm.answer = FOUND
    first_state = AskStreamState()
    first_events = [
        event
        async for event in question_service.ask_text_stream(
            project.id,
            "Q",
            ASKER,
            RateLimitScope.QUESTION,
            request_id="req-1",
            state=first_state,
        )
    ]
    assert first_events[-1].type is AskEventType.FINAL
    assert first_state.outcome is not None
    assert first_state.outcome.answer == "A"

    second_state = AskStreamState()
    second_events = [
        event
        async for event in question_service.ask_text_stream(
            project.id,
            "Q",
            ASKER,
            RateLimitScope.QUESTION,
            request_id="req-2",
            state=second_state,
        )
    ]
    assert second_events[-1].type is AskEventType.FINAL
    assert second_state.outcome is not None
    assert second_state.outcome.answer == "A"
    assert fake_llm.ask_calls == 1


async def test_not_found_creates_question(question_service, fake_llm, project):
    outcome = await question_service.ask_text(project.id, " Q? ", ASKER, RateLimitScope.QUESTION)
    assert outcome.answer is None
    assert outcome.question.status == QuestionStatus.PENDING
    assert outcome.question.text == "Q?"
    assert (outcome.question.chat_id, outcome.question.message_id) == (1002, 10)


def test_found_with_blank_answer_is_rejected_by_contract():
    with pytest.raises(ValueError):
        AskResult(found=True, answer="   ")


async def test_not_found_is_not_cached(question_service, fake_llm, project):
    await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)
    fake_llm.answer = FOUND
    outcome = await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)
    assert outcome.answer == "A"


async def test_cache_invalidated_by_docs_update(
    question_service, docs_service, fake_llm, project, admin
):
    fake_llm.answer = FOUND
    await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)
    await docs_service.apply_update(admin, project.id, DocsMode.APPEND, [DocFile("a.md", b"x")])
    await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)
    assert fake_llm.ask_calls == 2


@pytest.mark.parametrize(("text", "error"), [("x" * 4001, QuestionTooLong), ("   ", EmptyQuestion)])
async def test_question_validation(question_service, fake_llm, project, text, error):
    with pytest.raises(error):
        await question_service.ask_text(project.id, text, ASKER, RateLimitScope.QUESTION)
    assert fake_llm.ask_calls == 0


async def test_question_rate_limited(question_service, settings, project):
    for _ in range(settings.question_rate_limit):
        await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)
    with pytest.raises(RateLimited):
        await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)


async def test_group_rate_limit_is_per_chat(question_service, settings, project):
    for i in range(settings.question_rate_limit):
        asker = Asker(telegram_id=i, chat_id=-100, message_id=i)
        await question_service.ask_text(project.id, "Q", asker, RateLimitScope.GROUP_QUESTION)
    with pytest.raises(RateLimited):
        await question_service.ask_text(
            project.id, "Q", Asker(999, -100, 1), RateLimitScope.GROUP_QUESTION
        )


async def test_llm_unavailable_propagates_and_saves_nothing(question_service, fake_llm, project):
    fake_llm.fail = True
    with pytest.raises(LLMUnavailableError):
        await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)


async def test_ask_image_not_found_saves_placeholder(question_service, project):
    outcome = await question_service.ask_image(project.id, b"img", "file-1", None, ASKER)
    assert outcome.question.text == "[скриншот]"
    assert outcome.question.image_file_id == "file-1"


async def test_ask_image_found(question_service, fake_llm, project):
    fake_llm.answer = FOUND
    outcome = await question_service.ask_image(project.id, b"img", "f", " подпись ", ASKER)
    assert outcome.answer == "A"
    assert fake_llm.last_question == "подпись"


async def test_admin_chat_ids(question_service, project, admin, second_admin):
    ids = await question_service.get_admin_chat_ids(project.id)
    assert sorted(ids) == sorted([admin.telegram_id, second_admin.telegram_id])


async def test_answer_question_race_only_one_wins(
    question_service, pending_question, admin, second_admin
):
    results = await asyncio.gather(
        question_service.answer_question(admin, pending_question.id, "a1"),
        question_service.answer_question(second_admin, pending_question.id, "a2"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, QuestionAlreadyAnswered) for r in results) == 1
    assert sum(not isinstance(r, Exception) for r in results) == 1


async def test_non_admin_cannot_answer(question_service, pending_question, user):
    with pytest.raises(NotProjectAdmin):
        await question_service.answer_question(user, pending_question.id, "a")


async def test_admin_of_other_project_cannot_answer(
    question_service, pending_question, outsider, other_project
):
    with pytest.raises(NotProjectAdmin):
        await question_service.answer_question(outsider, pending_question.id, "a")


async def test_save_to_knowledge(question_service, fake_llm, pending_question, project, admin):
    with pytest.raises(QuestionNotAnswered):
        await question_service.save_to_knowledge(admin, pending_question.id)
    await question_service.answer_question(admin, pending_question.id, " ответ ")
    await question_service.save_to_knowledge(admin, pending_question.id)
    assert fake_llm.knowledge == [(project.id, "Q?", "ответ")]


async def test_apply_update_bumps_version_and_stores_summary(
    docs_service, fake_llm, project, admin
):
    fake_llm.summary = "Добавлен раздел"
    update = await docs_service.apply_update(
        admin, project.id, DocsMode.REPLACE, [DocFile("a.md", b"x")]
    )
    assert update.summary == "Добавлен раздел" and update.mode == DocsMode.REPLACE
    assert (await Project.get(id=project.id)).docs_version == 1
    assert fake_llm.uploads == [(project.id, DocsMode.REPLACE, ["a.md"])]


async def test_real_docs_path_uses_publisher_not_upload_documents(
    fake_llm,
    project_work,
    doc_update_work,
    membership_work,
    access_guard,
    project,
    admin,
):
    update = await doc_update_work.create(
        project.id, admin.id, DocsMode.REPLACE, "Опубликована версия 1"
    )

    class FakePublisher:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        async def ingest_and_publish(self, project_id, mode, files, *, created_by_id):
            self.calls.append((project_id, mode, [file.filename for file in files], created_by_id))
            return SimpleNamespace(doc_update=update)

    publisher = FakePublisher()
    service = DocsService(
        fake_llm,
        project_work,
        doc_update_work,
        membership_work,
        access_guard,
        publisher,
    )

    result = await service.apply_update(
        admin, project.id, DocsMode.REPLACE, [DocFile("a.md", b"x")]
    )

    assert result is update
    assert fake_llm.uploads == []
    assert publisher.calls == [(project.id, DocsMode.REPLACE, ["a.md"], admin.id)]
    # The publisher owns version bump; DocsService must not bump a second time.
    assert (await Project.get(id=project.id)).docs_version == 0


async def test_llm_failure_keeps_docs_version(docs_service, fake_llm, project, admin):
    fake_llm.fail = True
    with pytest.raises(LLMUnavailableError):
        await docs_service.apply_update(admin, project.id, DocsMode.APPEND, [DocFile("a.md", b"x")])
    assert (await Project.get(id=project.id)).docs_version == 0


async def test_apply_update_requires_admin_and_files(docs_service, project, admin, user):
    with pytest.raises(NotProjectAdmin):
        await docs_service.apply_update(user, project.id, DocsMode.APPEND, [DocFile("a", b"")])
    with pytest.raises(NoFilesToUpload):
        await docs_service.apply_update(admin, project.id, DocsMode.APPEND, [])


@pytest.mark.parametrize("size", [None, 20 * 1024 * 1024 + 1])
def test_file_too_large(size):
    with pytest.raises(FileTooLarge):
        DocsService.check_file_size(size)


def test_file_at_limit_ok():
    DocsService.check_file_size(20 * 1024 * 1024)


async def test_summary_access(docs_service, project, admin, user, outsider):
    update = await docs_service.apply_update(
        admin, project.id, DocsMode.APPEND, [DocFile("a", b"")]
    )
    assert await docs_service.get_summary(user, update.id) == "Сводка"
    with pytest.raises(NotProjectMember):
        await docs_service.get_summary(outsider, update.id)
    with pytest.raises(DocUpdateNotFound):
        await docs_service.get_summary(user, 9999)


async def test_group_bind_and_lookup(group_chat_service, project, project_password, outsider):
    bound = await group_chat_service.bind(-100, f"  {project_password} ", outsider)
    assert bound.id == project.id
    assert (await group_chat_service.get_bound_project(-100)).id == project.id
    assert await group_chat_service.get_bound_project(-200) is None


async def test_group_bind_wrong_password_and_bruteforce(
    group_chat_service, project_password, outsider
):
    for _ in range(5):
        with pytest.raises(InvalidPassword):
            await group_chat_service.bind(-100, "a1b2c3d4-wrong", outsider)
    with pytest.raises(TooManyAttempts):
        await group_chat_service.bind(-100, project_password, outsider)
    assert await group_chat_service.get_bound_project(-100) is None


async def test_group_bind_by_removed_member_rejected(
    group_chat_service, membership_service, project, project_password, admin, user
):
    await membership_service.remove_member(admin, project.id, user.id)
    with pytest.raises(RemovedFromProject):
        await group_chat_service.bind(-100, project_password, user)
