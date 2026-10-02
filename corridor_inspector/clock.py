"""时间源，测试可注入固定时钟。"""
from __future__ import annotations

from datetime import datetime, timedelta


class Clock:
    def now(self) -> datetime:
        return datetime.now()

    def in_days(self, days: float) -> datetime:
        return self.now() + timedelta(days=days)


class FixedClock(Clock):
    def __init__(self, moment: datetime):
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def advance(self, **kw) -> None:
        self._moment += timedelta(**kw)
