"""Heartbeat: regular ping to an external monitoring URL (e.g. Healthchecks, Uptime Kuma)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import aiohttp

from .clock import CLOCK

if TYPE_CHECKING:
    from .app import EnergyOptimizer

_LOGGER = logging.getLogger(__name__)


class Heartbeat:
    def __init__(self, app: "EnergyOptimizer") -> None:
        self.app = app
        self.hb_t = 0.0
        self.hb_ok = False
        self._hb_try = 0.0

    def state(self) -> dict:
        now = CLOCK.mono()
        return {"hb_age": int(now - self.hb_t) if self.hb_t else -1, "hb_ok": self.hb_ok}

    async def _ping(self, url: str) -> tuple[bool, str]:
        try:
            timeout = aiohttp.ClientTimeout(total=20, connect=8)
            async with self.app.session.get(url, timeout=timeout) as r:
                if 200 <= r.status < 300:
                    return True, ""
                return False, f"HTTP {r.status}"
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            return False, type(err).__name__

    async def tick(self) -> None:
        c = self.app.settings.cfg
        if not c["hb_en"] or not c["hb_url"]:
            return
        iv = (c["hb_min"] if c["hb_min"] > 0 else 15) * 60
        now = CLOCK.mono()
        if self._hb_try and now - self._hb_try < iv:
            return
        self._hb_try = now
        ok, err = await self._ping(c["hb_url"])
        self.hb_ok = ok
        if ok:
            self.hb_t = CLOCK.mono()
        else:
            _LOGGER.warning("[Heartbeat] Ping failed: %s", err)

    async def run(self) -> None:
        await asyncio.sleep(8)
        while True:
            await self.tick()
            await asyncio.sleep(5)
