"""Задания планировщика, которые пишут в Telegram."""

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.bot import texts
from app.bot.notifier import Notifier
from app.config import Settings
from app.core.clock import Clock, utc_now
from app.core.depends import DependsResolver
from app.core.enums import QuestionStatus
from app.dependencies import get_obj_digest_service, get_obj_notifier
from app.scheduler.schedule import WeeklySchedule
from app.scheduler.scheduler import Job, Scheduler
from app.services.digest_service import DigestService
from app.storage.redis.scheduler_work import RedisSchedulerWork

logger = logging.getLogger(__name__)

WEEKLY_DIGEST_JOB = "weekly_digest"


async def send_weekly_digest(due: datetime, service: DigestService, notifier: Notifier) -> None:
    for digest in await service.build_weekly(due):
        text = texts.weekly_digest(
            digest.project.name,
            digest.total,
            digest.pending,
            [(q.text, q.status == QuestionStatus.PENDING) for q in digest.latest],
        )
        report = await notifier.send_many(digest.admin_chat_ids, text)
        logger.info(
            "Weekly digest for project %s: sent %s, failed %s",
            digest.project.id,
            report.sent,
            report.failed,
        )


def build_scheduler(
    resolver: DependsResolver,
    settings: Settings,
    scheduler_work: RedisSchedulerWork,
    clock: Clock = utc_now,
) -> Scheduler:
    scheduler = Scheduler(scheduler_work, clock)

    async def weekly_digest(due: datetime) -> None:
        # Зависимости — на каждый запуск, как у хендлеров: Work-классы дешёвые
        cache: dict = {}
        service = await resolver.resolve(get_obj_digest_service, cache)
        notifier = await resolver.resolve(get_obj_notifier, cache)
        await send_weekly_digest(due, service, notifier)

    schedule = WeeklySchedule(
        settings.digest_weekday, time(settings.digest_hour), ZoneInfo(settings.scheduler_timezone)
    )
    scheduler.add(Job(WEEKLY_DIGEST_JOB, schedule, weekly_digest))
    return scheduler
