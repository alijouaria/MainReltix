from datetime import datetime, timedelta

from app import config


class VirtualClock:
    """Single source of time for the app; demo time starts at config.DEMO_START."""

    def __init__(self, start: str = config.DEMO_START):
        self._start = datetime.fromisoformat(start)
        self._now = self._start

    def now(self) -> datetime:
        return self._now

    def advance(self, td: timedelta) -> datetime:
        self._now += td
        return self._now

    def reset(self) -> None:
        self._now = self._start


clock = VirtualClock()
