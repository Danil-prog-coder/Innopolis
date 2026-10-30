from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.scheduler.schedule import WeeklySchedule

# Понедельник 10:00 по Москве = понедельник 07:00 UTC
MONDAY_10_MSK = WeeklySchedule(0, time(10), ZoneInfo("Europe/Moscow"))
DUE = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)  # понедельник
PREVIOUS_DUE = datetime(2026, 10, 19, 7, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (DUE, DUE),
        (datetime(2026, 10, 26, 6, 59, tzinfo=UTC), PREVIOUS_DUE),
        (datetime(2026, 10, 29, 12, 0, tzinfo=UTC), DUE),
        # Воскресенье 22:00 UTC — уже понедельник 01:00 по Москве, но до 10:00
        (datetime(2026, 11, 1, 22, 0, tzinfo=UTC), DUE),
        (datetime(2026, 11, 2, 7, 0, tzinfo=UTC), datetime(2026, 11, 2, 7, 0, tzinfo=UTC)),
    ],
)
def test_latest_due(now, expected):
    assert MONDAY_10_MSK.latest_due(now) == expected


def test_latest_due_is_utc():
    assert MONDAY_10_MSK.latest_due(DUE).tzinfo == UTC
