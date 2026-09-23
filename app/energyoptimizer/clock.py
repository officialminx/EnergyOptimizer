"""Zentrale Uhr – in den Tests austauschbar.

Dauern werden mit der monotonen Uhr gemessen (mono(), Sekunden seit Start),
Kalenderzeiten mit now() in der Zeitzone aus TZ (Standard Europe/Zurich).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _load_tz() -> tzinfo:
    name = os.environ.get("TZ") or "Europe/Zurich"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Zurich")


class Clock:
    def __init__(self) -> None:
        self.tz = _load_tz()
        self._start = time.monotonic()

    def mono(self) -> float:
        """Sekunden seit Programmstart (monoton, nie 0 nach dem Start)."""
        return time.monotonic() - self._start + 1.0

    def time(self) -> float:
        """Unix-Zeit in Sekunden."""
        return time.time()

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.time(), self.tz)

    def local(self, epoch: float) -> datetime:
        return datetime.fromtimestamp(epoch, self.tz)


CLOCK = Clock()
