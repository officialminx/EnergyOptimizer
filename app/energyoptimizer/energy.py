"""Energiezähler (energy.cpp).

Produktion und Verbrauch kommen als native Zählerstände vom Solar-Log; Netzbezug
und Einspeisung werden aus der Netzleistung integriert (Zeitschritt gedeckelt auf
3 × Abfrageintervall). Pro Steckdose zählt der aenergy-Zähler des Shelly, dazu
der PV-Anteil, Laufzeit und Schaltspiele.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from .clock import CLOCK
from .const import MAX_SHELLY, SHELLY_POLL_S
from .storage import load_json, save_json

_LOGGER = logging.getLogger(__name__)

_FIELDS_DAY = ("day_prod_wh", "day_cons_wh", "day_grid_in_wh", "day_grid_out_wh")
_FIELDS_TOT = ("total_prod_wh", "total_cons_wh", "total_grid_in_wh", "total_grid_out_wh")
_ARR = ("day_shelly_wh", "total_shelly_wh", "day_shelly_pv_wh", "total_shelly_pv_wh",
        "day_on_s", "total_on_s", "switch_cnt")


def date_of(epoch: float) -> int:
    d = CLOCK.local(epoch)
    return d.year * 10000 + d.month * 100 + d.day


class Energy:
    def __init__(self, data_dir: str, archive_day: Callable[[int, float, float, float, float], None]) -> None:
        self.path = os.path.join(data_dir, "energy.json")
        self.archive_day = archive_day
        self.c: dict = {k: 0.0 for k in _FIELDS_DAY + _FIELDS_TOT}
        for k in _ARR:
            self.c[k] = [0 for _ in range(MAX_SHELLY)]
        self.cur_yday = -1
        self.dirty = False
        self.last_save = 0.0
        self.archived_date = 0
        self.pv_share = 0.0
        self._last_total = [None] * MAX_SHELLY
        self._prev_grid_w = 0.0
        self._prev_t: float | None = None

    def load(self) -> None:
        data = load_json(self.path, None)
        if not isinstance(data, dict):
            self.cur_yday = CLOCK.now().timetuple().tm_yday  # Erststart: heute ist Tag 1
            return
        for k in _FIELDS_DAY + _FIELDS_TOT:
            if isinstance(data.get(k), (int, float)):
                self.c[k] = float(data[k])
        for k in _ARR:
            v = data.get(k)
            if isinstance(v, list) and len(v) == MAX_SHELLY:
                self.c[k] = list(v)
        self.cur_yday = int(data.get("yday", -1))
        self.archived_date = int(data.get("archived", 0))
        today = CLOCK.now().timetuple().tm_yday
        if self.cur_yday != today:
            # Seit dem letzten Speichern ist ein Tag vergangen: Tageszähler verwerfen.
            for k in _FIELDS_DAY:
                self.c[k] = 0.0
            for k in ("day_shelly_wh", "day_shelly_pv_wh", "day_on_s"):
                self.c[k] = [0 for _ in range(MAX_SHELLY)]
            self.cur_yday = today

    def save(self) -> None:
        data = dict(self.c)
        data["yday"] = self.cur_yday
        data["archived"] = self.archived_date
        save_json(self.path, data)
        self.dirty = False
        self.last_save = CLOCK.mono()

    def add_runtime(self, idx: int, seconds: int) -> None:
        if 0 <= idx < MAX_SHELLY and seconds > 0:
            self.c["day_on_s"][idx] += seconds
            self.c["total_on_s"][idx] += seconds
            self.dirty = True

    def count_switch(self, idx: int) -> None:
        if 0 <= idx < MAX_SHELLY:
            self.c["switch_cnt"][idx] += 1
            self.dirty = True

    def _close_solar_day(self, date: int) -> None:
        if date and date != self.archived_date:
            self.archive_day(date, self.c["day_prod_wh"], self.c["day_cons_wh"],
                             self.c["day_grid_in_wh"], self.c["day_grid_out_wh"])
            self.archived_date = date
        for k in _FIELDS_DAY:
            self.c[k] = 0.0
        self.dirty = True

    def add_solar(self, sd, poll_s: float) -> None:
        now = CLOCK.mono()
        c = self.c
        # Der Solar-Log nullt seine Tageszähler selbst (oft kurz nach Mitternacht):
        # fällt ein Tageswert deutlich, ist der Tag beim Solar-Log abgeschlossen.
        sl_reset = ((c["day_prod_wh"] > 200 and sd.prod_today_wh < c["day_prod_wh"] * 0.5)
                    or (c["day_cons_wh"] > 200 and sd.cons_today_wh < c["day_cons_wh"] * 0.5))
        if sl_reset:
            _LOGGER.info("[Energy] Solar-Log-Tageszähler zurückgesetzt – Tag abgeschlossen")
            self._close_solar_day(date_of(CLOCK.time() - 3600))
        d_in = d_out = 0.0
        if self._prev_t is not None:
            dt = min(now - self._prev_t, poll_s * 3)
            dt_h = dt / 3600.0
            if self._prev_grid_w > 0:
                d_in = self._prev_grid_w * dt_h
            elif self._prev_grid_w < 0:
                d_out = -self._prev_grid_w * dt_h
        c["day_prod_wh"] = sd.prod_today_wh
        c["day_cons_wh"] = sd.cons_today_wh
        c["total_prod_wh"] = sd.prod_total_wh
        c["total_cons_wh"] = sd.cons_total_wh
        c["day_grid_in_wh"] += d_in
        c["total_grid_in_wh"] += d_in
        c["day_grid_out_wh"] += d_out
        c["total_grid_out_wh"] += d_out
        self.dirty = True
        if sd.grid_w <= 0:
            self.pv_share = 1.0
        elif sd.consumption_w > 1:
            self.pv_share = max(0.0, min(1.0, sd.production_w / sd.consumption_w))
        else:
            self.pv_share = 0.0
        self._prev_grid_w = sd.grid_w
        self._prev_t = now

    def shelly_sample(self, idx: int, total_wh: float | None, apower_w: float, dt_s: float) -> None:
        if not 0 <= idx < MAX_SHELLY:
            return
        if total_wh is not None:
            last = self._last_total[idx]
            delta = 0.0 if last is None else max(0.0, total_wh - last)
            self._last_total[idx] = total_wh
        else:
            delta = apower_w * min(dt_s, SHELLY_POLL_S * 3) / 3600.0
        if delta <= 0:
            return
        pv = delta * self.pv_share
        c = self.c
        c["day_shelly_wh"][idx] += delta
        c["total_shelly_wh"][idx] += delta
        c["day_shelly_pv_wh"][idx] += pv
        c["total_shelly_pv_wh"][idx] += pv
        self.dirty = True

    def reset_shelly(self, idx: int) -> None:
        if not 0 <= idx < MAX_SHELLY:
            return
        for k in _ARR:
            self.c[k][idx] = 0
        self._last_total[idx] = None
        self.dirty = True
        _LOGGER.info("[Energy] Slot %d – Zähler zurückgesetzt (anderes Gerät)", idx)

    def tick(self) -> None:
        today = CLOCK.now().timetuple().tm_yday
        if self.cur_yday != today:
            self._close_solar_day(date_of(CLOCK.time() - 86400) if self.cur_yday != -1 else 0)
            self.cur_yday = today
            for k in ("day_shelly_wh", "day_shelly_pv_wh", "day_on_s"):
                self.c[k] = [0 for _ in range(MAX_SHELLY)]
            _LOGGER.info("[Energy] Neuer Tag – Tageszähler zurückgesetzt")
            self.save()
            return
        # Im Container ist Schreiben billig: alle 5 Minuten statt stündlich (NVS-Schonung).
        if self.dirty and CLOCK.mono() - self.last_save >= 300:
            self.save()
