from datetime import UTC, datetime, timedelta

from aiogram.methods import SendMessage

from app.bot import texts
from app.bot.jobs import WEEKLY_DIGEST_JOB, build_scheduler
from app.storage.postgres.models import Question
from app.storage.redis.scheduler_work import RedisSchedulerWork
from tests.bot.conftest import ADMIN_TG, SECOND_ADMIN_TG

# Понедельник 10:00 по Москве — расписание по умолчанию из Settings
DUE = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


async def test_weekly_digest_reaches_admins_only(
    resolver, settings, redis_client, session, question_work, project
):
    question = await question_work.create(project.id, 1002, 1002, 1, "как <настроить> VPN?", None)
    await Question.filter(id=question.id).update(created_at=DUE - timedelta(days=1))
    scheduler = build_scheduler(
        resolver, settings, RedisSchedulerWork(redis_client), clock=lambda: DUE
    )

    assert await scheduler.run_pending() == [WEEKLY_DIGEST_JOB]

    sent = session.all(SendMessage)
    assert sorted(m.chat_id for m in sent) == sorted([ADMIN_TG.id, SECOND_ADMIN_TG.id])
    assert sent[0].text == texts.weekly_digest("Альфа", 1, 1, [("как <настроить> VPN?", True)])
    assert "как &lt;настроить&gt; VPN?" in sent[0].text
    assert await scheduler.run_pending() == []


async def test_weekly_digest_silent_without_questions(resolver, settings, redis_client, session):
    scheduler = build_scheduler(
        resolver, settings, RedisSchedulerWork(redis_client), clock=lambda: DUE
    )
    assert await scheduler.run_pending() == [WEEKLY_DIGEST_JOB]
    assert session.all(SendMessage) == []
