from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import QuestionStatus
from app.services.digest_service import DigestService
from app.storage.postgres.models import Question

UNTIL = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


@pytest.fixture
def digest_service(question_work, project_work, membership_work):
    return DigestService(question_work, project_work, membership_work)


@pytest.fixture
def add_question(question_work):
    async def factory(project, text, created_at, status=QuestionStatus.PENDING) -> Question:
        question = await question_work.create(project.id, 1002, 1002, 1, text, None)
        await Question.filter(id=question.id).update(created_at=created_at, status=status)
        return question

    return factory


async def test_weekly_digest_counts_and_latest(digest_service, add_question, project):
    for day in range(7):
        await add_question(project, f"q{day}", UNTIL - timedelta(days=7) + timedelta(days=day))
    await add_question(
        project, "answered", UNTIL - timedelta(minutes=1), status=QuestionStatus.ANSWERED
    )

    [digest] = await digest_service.build_weekly(UNTIL)

    assert digest.project.id == project.id
    assert sorted(digest.admin_chat_ids) == [1001, 1003]
    assert (digest.total, digest.pending) == (8, 7)
    assert [q.text for q in digest.latest] == ["answered", "q6", "q5", "q4", "q3"]


async def test_weekly_digest_window_bounds(digest_service, add_question, project):
    await add_question(project, "since", UNTIL - timedelta(days=7))
    await add_question(project, "too old", UNTIL - timedelta(days=7, seconds=1))
    await add_question(project, "too new", UNTIL)

    [digest] = await digest_service.build_weekly(UNTIL)
    assert [q.text for q in digest.latest] == ["since"]


async def test_weekly_digest_skips_quiet_projects(
    digest_service, add_question, project, other_project
):
    await add_question(project, "q", UNTIL - timedelta(hours=1))
    assert [d.project.id for d in await digest_service.build_weekly(UNTIL)] == [project.id]


async def test_weekly_digest_skips_project_without_admins(
    digest_service, add_question, membership_work, other_project, outsider
):
    await add_question(other_project, "q", UNTIL - timedelta(hours=1))
    await membership_work.remove(outsider.id, other_project.id)
    assert await digest_service.build_weekly(UNTIL) == []


async def test_weekly_digest_empty(digest_service):
    assert await digest_service.build_weekly(UNTIL) == []
