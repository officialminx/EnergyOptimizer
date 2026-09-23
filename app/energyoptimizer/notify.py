"""Benachrichtigungen über ntfy und Heartbeat."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import aiohttp

from .clock import CLOCK
from .const import ER_NONE, EV_ERROR, NOTIFY_MAX_CRIT_PER_HOUR, NOTIFY_MAX_PER_HOUR, NS_CRIT, NS_WARN

if TYPE_CHECKING:
    from .app import EnergyOptimizer

_LOGGER = logging.getLogger(__name__)

NOTIFY_QUEUE_LEN = 16
NOTIFY_MAX_TRIES = 5
NOTIFY_GIVEUP_S = 1800


@dataclass
class Msg:
    sev: int
    title: str
    body: str
    tag: str
    tries: int = 0
    first: float = field(default_factory=CLOCK.mono)
    next: float = field(default_factory=CLOCK.mono)


class Notifier:
    def __init__(self, app: "EnergyOptimizer") -> None:
        self.app = app
        self.q: list[Msg] = []
        self.sent_ok = 0
        self.failed = 0
        self.dropped = 0
        self.last_t = 0.0
        self.last_ok = False
        self.last_err = ""
        self.hb_t = 0.0
        self.hb_ok = False
        self._hb_try = 0.0
        self._hour_start = 0.0
        self._hour_count = 0
        self._hour_crit = 0
        self._wake = asyncio.Event()

    def send(self, sev: int, title: str, body: str, tag: str) -> None:
        m = Msg(sev, (title or "EnergyOptimizer")[:71], (body or "")[:191], (tag or "")[:19])
        if len(self.q) >= NOTIFY_QUEUE_LEN:
            if sev >= NS_CRIT:
                victim = next((x for x in self.q if x.sev < NS_CRIT), None)
                if victim is not None:
                    self.q.remove(victim)
                    self.dropped += 1
                else:
                    self.dropped += 1
                    return
            else:
                self.dropped += 1
                _LOGGER.warning("[Notify] Queue voll – Meldung verworfen: %s", title)
                return
        self.q.append(m)
        self._wake.set()

    def state(self) -> dict:
        c = self.app.settings.cfg
        now = CLOCK.mono()
        return {
            "cfg": bool(c["nt_en"] and c["nt_top"]),
            "ok": self.sent_ok, "fail": self.failed, "drop": self.dropped,
            "age": int(now - self.last_t) if self.last_t else -1,
            "last": self.last_ok, "err": self.last_err,
            "hb_age": int(now - self.hb_t) if self.hb_t else -1,
            "hb_ok": self.hb_ok,
        }

    async def _http(self, url: str, json_body: dict | None) -> tuple[bool, str]:
        try:
            timeout = aiohttp.ClientTimeout(total=20, connect=8)
            if json_body is not None:
                req = self.app.session.post(url, json=json_body, timeout=timeout)
            else:
                req = self.app.session.get(url, timeout=timeout)
            async with req as r:
                if 200 <= r.status < 300:
                    return True, ""
                return False, f"HTTP {r.status}"
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            return False, f"Verbindungsfehler {type(err).__name__}"

    def _quiet(self) -> bool:
        c = self.app.settings.cfg
        qs, qe = c["nt_qs"], c["nt_qe"]
        if qs == qe:
            return False
        h = CLOCK.now().hour
        return qs <= h < qe if qs < qe else (h >= qs or h < qe)

    async def _heartbeat(self) -> None:
        c = self.app.settings.cfg
        if not c["hb_en"] or not c["hb_url"]:
            return
        iv = (c["hb_min"] if c["hb_min"] > 0 else 15) * 60
        now = CLOCK.mono()
        if self._hb_try and now - self._hb_try < iv:
            return
        self._hb_try = now
        ok, err = await self._http(c["hb_url"], None)
        self.hb_ok = ok
        if ok:
            self.hb_t = CLOCK.mono()
        else:
            _LOGGER.warning("[Heartbeat] Fehlgeschlagen: %s", err)

    async def run(self) -> None:
        await asyncio.sleep(8)
        while True:
            await self._heartbeat()
            try:
                await asyncio.wait_for(self._wake.wait(), 1.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            await self._process()

    async def _process(self) -> None:
        c = self.app.settings.cfg
        pending, self.q = self.q, []
        for m in pending:
            if not c["nt_en"] or not c["nt_top"]:
                continue
            if m.sev < c["nt_sev"]:
                continue
            if m.sev < NS_CRIT and self._quiet():
                continue
            now = CLOCK.mono()
            if not self._hour_start or now - self._hour_start > 3600:
                self._hour_start, self._hour_count, self._hour_crit = now, 0, 0
            crit = m.sev >= NS_CRIT
            used = self._hour_crit if crit else self._hour_count
            if used >= (NOTIFY_MAX_CRIT_PER_HOUR if crit else NOTIFY_MAX_PER_HOUR):
                self.dropped += 1
                continue
            if now < m.next:
                self.q.append(m)
                continue
            prio = 5 if m.sev == NS_CRIT else (4 if m.sev == NS_WARN else 3)
            body = {"topic": c["nt_top"], "title": m.title, "message": m.body, "priority": prio}
            if m.tag:
                body["tags"] = [m.tag]
            ok, err = await self._http(c["nt_srv"].rstrip("/"), body)
            self.last_t = CLOCK.mono()
            self.last_ok = ok
            self.last_err = "" if ok else err[:63]
            if ok:
                self.sent_ok += 1
                if crit:
                    self._hour_crit += 1
                else:
                    self._hour_count += 1
                _LOGGER.info("[Notify] Gesendet: %s", m.title)
                continue
            m.tries += 1
            if m.tries >= NOTIFY_MAX_TRIES or CLOCK.mono() - m.first > NOTIFY_GIVEUP_S:
                self.failed += 1
                self.app.events.log(EV_ERROR, -1, ER_NONE, False, "Meldung nicht zustellbar")
                continue
            wait = min(15 << (2 * (m.tries - 1)), 960)
            m.next = CLOCK.mono() + wait
            _LOGGER.warning("[Notify] Fehlgeschlagen (%s) – Wiederholung in %ds", err, wait)
            self.q.append(m)
