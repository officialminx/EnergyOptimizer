"""Prüft auf GitHub, ob eine neuere Version veröffentlicht wurde.

Fragt alle 6 Stunden das neueste Release des Repositorys ab (ohne Token; das
Limit von 60 Anfragen pro Stunde reicht bei weitem). Installiert wird nichts:
die Oberfläche zeigt nur einen Hinweis mit dem Befehl für das Update.
"""

from __future__ import annotations

import logging
import os
import re

import aiohttp

from . import __version__
from .clock import CLOCK

_LOGGER = logging.getLogger(__name__)

CHECK_INTERVAL_S = 6 * 3600
RETRY_S = 1800
FIRST_CHECK_S = 60
DEFAULT_REPO = "officialminx/energyoptimizer"


def parse_version(tag: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(latest: str, current: str) -> bool:
    lv, cv = parse_version(latest), parse_version(current)
    return lv is not None and cv is not None and lv > cv


class UpdateCheck:
    def __init__(self, current: str = __version__) -> None:
        self.current = current
        self.repo = os.environ.get("EO_UPDATE_REPO", DEFAULT_REPO).strip()
        self.api = "https://api.github.com"
        self.enabled = (os.environ.get("EO_UPDATE_CHECK", "1").strip().lower()
                        not in ("0", "false", "no", "off")) and bool(self.repo)
        self.latest = ""
        self.url = ""
        self.checked_t = 0.0
        self.error = ""
        self._next = CLOCK.mono() + FIRST_CHECK_S

    @property
    def available(self) -> bool:
        return bool(self.latest) and is_newer(self.latest, self.current)

    def state(self) -> dict:
        return {
            "current": self.current, "latest": self.latest, "available": self.available,
            "url": self.url, "enabled": self.enabled, "error": self.error,
            "age": int(CLOCK.mono() - self.checked_t) if self.checked_t else -1,
        }

    def due(self) -> bool:
        return self.enabled and CLOCK.mono() >= self._next

    async def check(self, session: aiohttp.ClientSession) -> None:
        url = f"{self.api}/repos/{self.repo}/releases/latest"
        headers = {"Accept": "application/vnd.github+json",
                   "User-Agent": f"EnergyOptimizer/{self.current}"}
        try:
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 404:
                    # Noch kein Release veröffentlicht – kein Fehler.
                    self.latest, self.url, self.error = "", "", ""
                    self._done(CHECK_INTERVAL_S)
                    return
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                doc = await resp.json(content_type=None)
        except Exception as err:  # noqa: BLE001 - ein fehlender Internetzugang ist kein Drama
            self.error = str(err) or type(err).__name__
            _LOGGER.debug("[Update] Prüfung fehlgeschlagen: %s", self.error)
            self._done(RETRY_S)
            return
        tag = str(doc.get("tag_name") or "")
        if parse_version(tag) is None:
            self.error = f"Unbekanntes Versionsformat: {tag[:30]}"
            self._done(CHECK_INTERVAL_S)
            return
        self.latest = tag.lstrip("v")
        self.url = str(doc.get("html_url") or f"https://github.com/{self.repo}/releases")
        self.error = ""
        if self.available:
            _LOGGER.info("[Update] Version %s verfügbar (installiert: %s)", self.latest, self.current)
        self._done(CHECK_INTERVAL_S)

    def _done(self, next_in: float) -> None:
        self.checked_t = CLOCK.mono()
        self._next = self.checked_t + next_in
