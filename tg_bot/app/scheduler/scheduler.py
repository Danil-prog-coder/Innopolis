"""Задания по расписанию внутри процесса бота.

Раз в ``tick_seconds`` планировщик берёт у каждого задания последний момент срабатывания
(``Schedule.latest_due``) и запускает задание, если этот момент ещё никто не отработал,
а опоздание не больше ``Job.grace``. Отметка «отработано» живёт в Redis
(``RedisSchedulerWork``): после перезапуска бота задание не повторится, а если бот лежал
в момент срабатывания — догонит его в пределах grace. Отметку ставит SET NX, поэтому при
нескольких экземплярах бота задание выполнит только один из них.

Работает на asyncio. Когда заданий станет много или они будут тяжёлыми, их стоит
перенести в очередь задач (taskiq/arq) с отдельным воркером.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.clock import Clock, utc_now
from app.scheduler.schedule import Schedule
from app.storage.redis.scheduler_work import RedisSchedulerWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    schedule: Schedule
    # Получает момент срабатывания (UTC): от него, а не от текущего времени, задание
    # отсчитывает свой период — так догнанный запуск обработает те же данные
    run: Callable[[datetime], Awaitable[None]]
    grace: timedelta = timedelta(hours=24)


class Scheduler:
    # Отметка живёт чуть дольше grace: пока срабатывание может быть запущено, она нужна
    _CLAIM_MARGIN = timedelta(hours=1)

    def __init__(
        self, scheduler_work: RedisSchedulerWork, clock: Clock = utc_now, tick_seconds: float = 60
    ) -> None:
        self._work = scheduler_work
        self._clock = clock
        self._tick = tick_seconds
        self._jobs: list[Job] = []
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def add(self, job: Job) -> None:
        if any(existing.name == job.name for existing in self._jobs):
            raise ValueError(f"Job {job.name!r} is already registered")
        self._jobs.append(job)

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Scheduler is already running")
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        """Остановка бота: дождаться текущего задания (не обрывать рассылку) и выйти."""
        if self._task is None:
            return
        self._stopping.set()
        await self._task
        self._task = None

    async def run_pending(self) -> list[str]:
        """Один проход: запустить созревшие задания. Вернуть имена запущенных."""
        now = self._clock()
        started: list[str] = []
        for job in self._jobs:
            try:
                if await self._run_if_due(job, now):
                    started.append(job.name)
            except Exception:
                logger.exception("Scheduler: job %s failed, will retry on next tick", job.name)
        return started

    async def _run_if_due(self, job: Job, now: datetime) -> bool:
        due = job.schedule.latest_due(now)
        if now - due > job.grace:
            return False
        ttl = int((job.grace + self._CLAIM_MARGIN).total_seconds())
        if not await self._work.claim(job.name, due, ttl):
            return False
        logger.info("Scheduler: running %s for %s", job.name, due.isoformat())
        try:
            await job.run(due)
        except BaseException:
            # Не доделано — снять отметку, чтобы повторить на следующем проходе
            await self._work.release(job.name, due)
            raise
        return True

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            await self.run_pending()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), self._tick)
