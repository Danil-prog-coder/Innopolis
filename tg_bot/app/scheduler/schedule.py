from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Schedule(Protocol):
    def latest_due(self, now: datetime) -> datetime:
        """Последний момент срабатывания не позже ``now`` (в UTC)."""


@dataclass(frozen=True, slots=True)
class WeeklySchedule:
    """Раз в неделю: день недели (0 — понедельник) и время по часовому поясу ``tz``."""

    weekday: int
    at: time
    tz: ZoneInfo

    def latest_due(self, now: datetime) -> datetime:
        local = now.astimezone(self.tz)
        days_back = (local.weekday() - self.weekday) % 7
        due = datetime.combine(local.date() - timedelta(days=days_back), self.at, tzinfo=self.tz)
        if due > local:
            due -= timedelta(days=7)
        return due.astimezone(UTC)
