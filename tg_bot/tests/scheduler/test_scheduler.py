import asyncio
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.scheduler.schedule import WeeklySchedule
from app.scheduler.scheduler import Job, Scheduler
from app.storage.redis.scheduler_work import RedisSchedulerWork

SCHEDULE = WeeklySchedule(0, time(10), ZoneInfo("Europe/Moscow"))
DUE = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Recorder:
    """Задание, которое запоминает, за какие срабатывания его запускали."""

    def __init__(self, failures: int = 0) -> None:
        self.calls: list[datetime] = []
        self._failures = failures

    async def __call__(self, due: datetime) -> None:
        if self._failures:
            self._failures -= 1
            raise RuntimeError("boom")
        self.calls.append(due)


@pytest.fixture
def scheduler_work(redis_client):
    return RedisSchedulerWork(redis_client)


@pytest.fixture
def clock():
    return FakeClock(DUE + timedelta(minutes=1))


def make(scheduler_work, clock, job, **kwargs) -> Scheduler:
    scheduler = Scheduler(scheduler_work, clock, **kwargs)
    scheduler.add(Job("digest", SCHEDULE, job))
    return scheduler


async def test_runs_once_per_occurrence(scheduler_work, clock):
    job = Recorder()
    scheduler = make(scheduler_work, clock, job)
    assert await scheduler.run_pending() == ["digest"]
    assert await scheduler.run_pending() == []
    assert job.calls == [DUE]

    clock.now += timedelta(days=7)
    assert await scheduler.run_pending() == ["digest"]
    assert job.calls == [DUE, DUE + timedelta(days=7)]


async def test_not_repeated_after_restart(scheduler_work, clock):
    first, second = Recorder(), Recorder()
    await make(scheduler_work, clock, first).run_pending()
    # Новый экземпляр — как после перезапуска бота: отметка осталась в Redis
    assert await make(scheduler_work, clock, second).run_pending() == []
    assert second.calls == []


async def test_two_instances_run_job_once(scheduler_work, clock):
    first, second = Recorder(), Recorder()
    await asyncio.gather(
        make(scheduler_work, clock, first).run_pending(),
        make(scheduler_work, clock, second).run_pending(),
    )
    assert len(first.calls) + len(second.calls) == 1


async def test_catches_up_missed_run_within_grace(scheduler_work, clock):
    clock.now = DUE + timedelta(hours=5)
    job = Recorder()
    assert await make(scheduler_work, clock, job).run_pending() == ["digest"]
    assert job.calls == [DUE]


async def test_skips_run_older_than_grace(scheduler_work, clock):
    # Бот впервые запущен в среду: понедельничную сводку не шлём, ждём следующую
    clock.now = DUE + timedelta(days=2)
    job = Recorder()
    assert await make(scheduler_work, clock, job).run_pending() == []
    assert job.calls == []


async def test_failed_job_is_retried(scheduler_work, clock):
    job = Recorder(failures=1)
    scheduler = make(scheduler_work, clock, job)
    assert await scheduler.run_pending() == []
    assert await scheduler.run_pending() == ["digest"]
    assert job.calls == [DUE]


async def test_failing_job_does_not_block_others(scheduler_work, clock):
    scheduler = Scheduler(scheduler_work, clock)
    broken, healthy = Recorder(failures=1), Recorder()
    scheduler.add(Job("broken", SCHEDULE, broken))
    scheduler.add(Job("healthy", SCHEDULE, healthy))
    assert await scheduler.run_pending() == ["healthy"]


def test_duplicate_job_name_rejected(scheduler_work, clock):
    scheduler = make(scheduler_work, clock, Recorder())
    with pytest.raises(ValueError):
        scheduler.add(Job("digest", SCHEDULE, Recorder()))


async def test_loop_runs_and_stop_waits_for_running_job(scheduler_work, clock):
    started, finished = asyncio.Event(), asyncio.Event()

    async def slow_job(due: datetime) -> None:
        started.set()
        await asyncio.sleep(0.05)
        finished.set()

    scheduler = make(scheduler_work, clock, slow_job, tick_seconds=0.01)
    scheduler.start()
    with pytest.raises(RuntimeError):
        scheduler.start()
    await asyncio.wait_for(started.wait(), 1)
    await scheduler.stop()
    assert finished.is_set()
    await scheduler.stop()  # повторная остановка ничего не ломает
