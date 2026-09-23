"""Alarmzentrale (alarms.cpp + sun.h).

Jede Störung wird erst nach ihrer Entprellzeit aktiv, dann per ntfy gemeldet,
bei Kritisch alle 6 h wiederholt (bis quittiert) und beim Beheben entwarnt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .clock import CLOCK
from .const import (
    AL_COUNT, AL_FAILSAFE, AL_INVERTER, AL_NOPROD, AL_NVS, AL_SHELLY0, AL_SOLARLOG,
    EV_ALARM, MAX_SHELLY, NOTIFY_REPEAT_H, NS_CRIT, NS_INFO, NS_WARN,
    SHELLY_FAIL_RECOVER,
)

if TYPE_CHECKING:
    from .app import EnergyOptimizer

SUN_MIN_ELEV_DEG = 10.0
INV_PLANT_MIN_W = 500.0
INV_SUN_MIN_ELEV_DEG = 20.0
INV_DEAD_W = 20.0
INV_ALIVE_W = 200.0


def sun_elevation_deg(utc: float, lat_deg: float, lon_deg: float) -> float:
    d2r, r2d = math.pi / 180.0, 180.0 / math.pi
    n = utc / 86400.0 + 2440587.5 - 2451545.0
    L = math.fmod(280.460 + 0.9856474 * n, 360.0)
    g = math.fmod(357.528 + 0.9856003 * n, 360.0) * d2r
    lam = (L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) * d2r
    eps = (23.439 - 0.0000004 * n) * d2r
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    gmst = math.fmod(18.697374558 + 24.06570982441908 * n, 24.0)
    if gmst < 0:
        gmst += 24.0
    lmst = gmst * 15.0 + lon_deg
    ha = (lmst - ra * r2d) * d2r
    lat = lat_deg * d2r
    sin_el = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(ha)
    return math.asin(max(-1.0, min(1.0, sin_el))) * r2d


def alarm_name(i: int) -> str:
    return {
        AL_SOLARLOG: "SolarLog antwortet nicht",
        AL_FAILSAFE: "Fail-Safe aktiv",
        AL_NOPROD: "Keine Produktion trotz Sonne",
        AL_INVERTER: "Wechselrichter ohne Leistung",
        AL_NVS: "Speicherfehler (Datenverzeichnis)",
    }.get(i, f"Steckdose {i - AL_SHELLY0 + 1} nicht erreichbar")


@dataclass
class AlarmState:
    sev: int = NS_WARN
    cond: bool = False
    cond_since: float = 0.0
    active: bool = False
    active_since: float = 0.0
    active_epoch: int = 0
    last_notify: float = 0.0
    acked: bool = False
    detail: str = ""


class Alarms:
    def __init__(self, app: "EnergyOptimizer") -> None:
        self.app = app
        self.al = [AlarmState() for _ in range(AL_COUNT)]
        self.al[AL_FAILSAFE].sev = NS_CRIT
        self.al[AL_INVERTER].sev = NS_CRIT

    def _feed(self, i: int, cond: bool, delay_min: int, detail: str | None) -> None:
        a = self.al[i]
        now = CLOCK.mono()
        if delay_min <= 0:
            cond = False
        if cond:
            if not a.cond:
                a.cond = True
                a.cond_since = now
            if detail:
                a.detail = detail[:55]
        else:
            a.cond = False
        dev = i - AL_SHELLY0 if i >= AL_SHELLY0 else -1
        if cond and not a.active and now - a.cond_since >= delay_min * 60:
            a.active = True
            a.acked = False
            a.active_since = now
            a.active_epoch = int(CLOCK.time())
            a.last_notify = now
            self.app.events.log(EV_ALARM, dev, i, True, alarm_name(i))
            self.app.notify.send(a.sev, alarm_name(i), a.detail or "Störung erkannt",
                                 "rotating_light" if a.sev == NS_CRIT else "warning")
            return
        if not cond and a.active:
            a.active = False
            mins = int((now - a.active_since) / 60)
            self.app.events.log(EV_ALARM, dev, i, False, alarm_name(i))
            self.app.notify.send(NS_INFO, alarm_name(i), f"Behoben nach {mins} min.", "white_check_mark")
            a.detail = ""
            return
        if (a.active and not a.acked and a.sev == NS_CRIT
                and now - a.last_notify >= NOTIFY_REPEAT_H * 3600):
            a.last_notify = now
            self.app.notify.send(NS_CRIT, alarm_name(i),
                                 f"Weiterhin aktiv seit {int((now - a.active_since) / 60)} min. {a.detail}",
                                 "rotating_light")

    def tick(self, sl_age_s: float, sd, sd_valid: bool, devs) -> None:
        c = self.app.settings.cfg
        mo_sl = c["mo_sl"] if c["mo_sl"] > 0 else 1
        self._feed(AL_SOLARLOG, sl_age_s >= mo_sl * 60, c["mo_sl"],
                   f"{c['sl_ip']} seit {int(sl_age_s // 60)} min ohne Antwort")
        self._feed(AL_FAILSAFE, self.app.devices.failsafe["shelly"], 1,
                   "Automatik-Geräte wurden abgeschaltet")
        elev = sun_elevation_deg(CLOCK.time(), c["lat"], c["lon"])
        noprod = sd_valid and elev >= SUN_MIN_ELEV_DEG and sd.production_w < 50.0
        self._feed(AL_NOPROD, noprod, c["mo_np"],
                   f"Sonnenhöhe {elev:.0f}°, Produktion {sd.production_w if sd_valid else 0:.0f} W")
        inv_bad = False
        det = ""
        if (c["sl_dev"] and devs.valid and sd_valid and sd.production_w >= INV_PLANT_MIN_W
                and elev >= INV_SUN_MIN_ELEV_DEG):
            producers = 0
            best = 0.0
            for d in devs.d[: devs.count]:
                if not d.present or d.seen_max_w < INV_ALIVE_W:
                    continue
                producers += 1
                if d.has_power and d.power_w > best:
                    best = d.power_w
            if producers >= 2 and best >= INV_ALIVE_W:
                for d in devs.d[: devs.count]:
                    if not d.present or not d.has_power or d.seen_max_w < INV_ALIVE_W:
                        continue
                    if d.power_w > INV_DEAD_W:
                        continue
                    inv_bad = True
                    det = f"{d.name or 'Wechselrichter'}: 0 W (andere liefern {best:.0f} W)"
                    break
        self._feed(AL_INVERTER, inv_bad, c["mo_inv"] if c["sl_dev"] else 0, det)
        self._feed(AL_NVS, self.app.settings.save_error, 1,
                   "Einstellungen konnten nicht gespeichert werden")
        dv = self.app.devices
        for i in range(MAX_SHELLY):
            e = c["shelly"][i]
            configured = i < c["sh_count"] and bool(e["ip"])
            st = dv.st["shelly"][i]
            bad = configured and not st.reachable and st.fail_count >= SHELLY_FAIL_RECOVER
            nm = st.name_from_device or e["name"] or e["ip"]
            self._feed(AL_SHELLY0 + i, bad, c["mo_dev"] if configured else 0,
                       f"{nm} ({e['ip']}) antwortet nicht")

    def active_count(self) -> int:
        return sum(1 for a in self.al if a.active and not a.acked)

    def max_severity(self) -> int:
        return max((a.sev for a in self.al if a.active), default=-1)

    def build(self) -> dict:
        now = CLOCK.mono()
        out = []
        n, mx = 0, -1
        for i, a in enumerate(self.al):
            if not a.active:
                continue
            out.append({"id": i, "name": alarm_name(i), "sev": a.sev, "since": a.active_epoch,
                        "mins": int((now - a.active_since) / 60), "acked": a.acked,
                        "detail": a.detail})
            if not a.acked:
                n += 1
            mx = max(mx, a.sev)
        return {"al": out, "n": n, "max": mx}

    def ack(self, i: int) -> bool:
        if i >= AL_COUNT:
            return False
        if i < 0:
            for a in self.al:
                a.acked = True
        else:
            self.al[i].acked = True
        return True
