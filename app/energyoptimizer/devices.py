"""Geräte-Steuerung (shelly.cpp + extswitch.cpp der Firmware).

Shelly-Steckdosen (Gen2/Gen3-RPC) und externe Schalter (nur über MQTT) folgen
derselben Logik; wo die Firmware beide Varianten getrennt führt, steht hier eine
gemeinsame Implementierung mit `kind` = "shelly" | "ext". Unterschiede:

* Shelly wird per HTTP geschaltet und abgefragt, externe Schalter melden ihren
  Wunschzustand per MQTT (``<prefix>/ext/<i>/set``) und gelten sofort als geschaltet.
* Nur Shelly kennt Erreichbarkeit, Messwerte und die Eigenregelung (Laufschwelle).
* Shelly-Geräte haben Vorrang: externe Schalter bekommen den Überschuss, der nach
  den Shelly-Entscheidungen übrig bleibt.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import aiohttp

from . import sched
from .clock import CLOCK
from .const import (
    BATT_GUARD_RELEASE_FACTOR, ER_BATTERY, ER_CMD_FAIL, ER_DAY_CAP, ER_DEFICIT,
    ER_FAILSAFE, ER_FORCED, ER_FORCED_END, ER_MANUAL, ER_NO_DEMAND, ER_NONE,
    ER_SCHEDULE, ER_SURPLUS, ER_TIMER, ER_WINDOW, EV_AUTOMODE, EV_FAILSAFE,
    EV_IPMOVE, EV_REACH, EV_RUN, EV_SWITCH, MAX_EXT, MAX_SHELLY, ND_RETRY_S,
    RUN_START_GRACE_S, SCHED_RETRY_S, SHELLY_CMD_LOCK_S, SHELLY_FAIL_RECOVER,
    SHELLY_POLL_S, SHELLY_RECOVER_COOLDOWN_S,
)
from .settings import hyst_off_ticks, hyst_on_ticks

if TYPE_CHECKING:
    from .app import EnergyOptimizer

_LOGGER = logging.getLogger(__name__)

MAX_FOUND = 8
SHELLY_MAX_RESP = 8192


@dataclass
class DevState:
    on: bool = False
    reachable: bool = False
    auto_active: bool = False
    force_eval: bool = False
    on_ticks: int = 0
    off_ticks: int = 0
    last_on: float = 0.0          # CLOCK.mono() beim letzten Einschalten (0 = nie)
    last_off: float = 0.0
    apower_w: float = 0.0
    voltage_v: float = 0.0
    current_a: float = 0.0
    temp_c: float = 0.0
    name_from_device: str = ""
    last_poll: float = 0.0
    today_on_s: int = 0
    forced_on: bool = False
    fail_count: int = 0
    cmd_lock_until: float = 0.0
    offline_logged: bool = False
    override_until: float = 0.0   # 0 = unbefristet
    override_ret_auto: bool = False
    running: bool = False
    idle_since: float = 0.0
    nd_retry: float = 0.0
    sched_on: bool = False
    sched_skip: int = -1
    sched_retry: float = 0.0
    last_cmd: int = -1


@dataclass
class Found:
    ip: str
    name: str
    model: str
    id: str


@dataclass
class ScanState:
    running: bool = False
    done: bool = False
    recovery: bool = False
    found: list[Found] = field(default_factory=list)
    last_recover: float = 0.0


def local_ipv4(probe: str = "8.8.8.8") -> str:
    """Eigene LAN-Adresse (für den Subnetz-Scan; mit network_mode: host die des Pi)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((probe, 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Devices:
    def __init__(self, app: "EnergyOptimizer") -> None:
        self.app = app
        self.st = {"shelly": [DevState() for _ in range(MAX_SHELLY)],
                   "ext": [DevState() for _ in range(MAX_EXT)]}
        self.batt_block = False
        self.failsafe = {"shelly": False, "ext": False}
        self.last_surplus: dict[str, float | None] = {"shelly": None, "ext": None}
        self.auto_eval_req = False
        self.poll_req = False
        self.scan = ScanState()
        self._rt_last = {"shelly": 0.0, "ext": 0.0}
        self._cur_yday = {"shelly": -1, "ext": -1}
        self._tasks: set[asyncio.Task] = set()

    # ── Zugriffshilfen ──────────────────────────────────────────────────────
    @property
    def cfg(self) -> dict:
        return self.app.settings.cfg

    def entries(self, kind: str) -> list[dict]:
        return self.cfg["shelly" if kind == "shelly" else "ext"]

    def count(self, kind: str) -> int:
        return self.cfg["sh_count"] if kind == "shelly" else self.cfg["ex_count"]

    def slot_used(self, kind: str, i: int) -> bool:
        e = self.entries(kind)[i]
        return bool(e["ip"] if kind == "shelly" else e["name"])

    def display_name(self, kind: str, i: int) -> str:
        e = self.entries(kind)[i]
        if kind == "ext":
            return e["name"] or "Extern"
        return self.st["shelly"][i].name_from_device or e["name"] or e["ip"]

    def _ev_dev(self, kind: str, i: int) -> int:
        return i if kind == "shelly" else -1

    def _log_switch(self, kind: str, i: int, reason: int, on: bool) -> None:
        self.app.events.log(EV_SWITCH, self._ev_dev(kind, i), reason, on,
                            self.display_name(kind, i), self.last_surplus[kind])

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    @property
    def session(self) -> aiohttp.ClientSession:
        return self.app.session

    # ── Schalten ────────────────────────────────────────────────────────────
    async def set(self, kind: str, i: int, on: bool, reason: int = ER_NONE) -> bool:
        if not 0 <= i < self.count(kind):
            return False
        s = self.st[kind][i]
        if kind == "ext":
            changed = s.last_cmd != int(on)
            s.last_cmd = int(on)
            s.on = on
            if on:
                s.last_on = CLOCK.mono()
            else:
                s.last_off = CLOCK.mono()
            if changed:
                _LOGGER.info("[Ext] %s → %s (MQTT)", self.display_name(kind, i), "EIN" if on else "AUS")
                self._log_switch(kind, i, reason, on)
                self.app.mqtt.mark_ext_dirty(i)
            return True

        ip = self.entries(kind)[i]["ip"]
        if not ip:
            return False
        url = f"http://{ip}/rpc/Switch.Set?id=0&on={'true' if on else 'false'}"
        ok = False
        code: Any = "-"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as r:
                code = r.status
                ok = r.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            code = type(err).__name__
        if ok:
            changed = s.last_cmd != int(on)
            s.last_cmd = int(on)
            s.on = on
            if on:
                s.last_on = CLOCK.mono()
            else:
                s.last_off = CLOCK.mono()
            s.running = False
            s.idle_since = 0.0
            if changed:
                self.app.energy.count_switch(i)
            self.app.mqtt.mark_shelly_dirty(i)
            _LOGGER.info("[Shelly] %s → %s", self.display_name(kind, i), "EIN" if on else "AUS")
            self._log_switch(kind, i, reason, on)
        else:
            _LOGGER.warning("[Shelly] Fehler %s: %s", self.display_name(kind, i), code)
            self.app.events.log(EV_SWITCH, i, ER_CMD_FAIL, on, self.display_name(kind, i))
        return ok

    def queue_set(self, kind: str, i: int, on: bool, reason: int) -> None:
        """Handbefehl: sofort im UI zeigen, eigentlichen Aufruf im Hintergrund."""
        s = self.st[kind][i]
        s.on = on
        s.cmd_lock_until = CLOCK.mono() + SHELLY_CMD_LOCK_S
        self._spawn(self.set(kind, i, on, reason))

    def apply_command(self, kind: str, i: int, cmd: str, src: int = ER_MANUAL,
                      minutes: int = 0) -> bool:
        if not 0 <= i < self.count(kind):
            return False
        minutes = max(0, min(1440, minutes))
        e = self.entries(kind)[i]
        s = self.st[kind][i]
        name = self.display_name(kind, i)
        dev = self._ev_dev(kind, i)
        if cmd == "autoon":
            e["auto"] = True
            s.force_eval = True
            s.on_ticks = s.off_ticks = 0
            s.override_until = 0.0
            s.nd_retry = 0.0
            self.auto_eval_req = True
            self.app.events.log(EV_AUTOMODE, dev, src, True, name)
        elif cmd == "autooff":
            e["auto"] = False
            s.override_until = 0.0
            self.app.events.log(EV_AUTOMODE, dev, src, False, name)
        elif cmd in ("on", "off"):
            act_now = sched.active(e["sch"], CLOCK.now()) if cmd == "off" else -1
            was_auto = e["auto"]
            e["auto"] = False
            s.nd_retry = 0.0
            s.forced_on = False
            if cmd == "off":
                s.sched_skip = act_now if act_now >= 0 else -1
                s.sched_on = False
            if minutes > 0:
                s.override_until = CLOCK.mono() + minutes * 60
                s.override_ret_auto = was_auto
            else:
                s.override_until = 0.0
            if kind == "shelly":
                self.queue_set(kind, i, cmd == "on", src)
            else:
                self._spawn(self.set(kind, i, cmd == "on", src))
        else:
            return False
        self.app.settings.save()
        if kind == "shelly":
            self.app.mqtt.mark_shelly_dirty(i)
        else:
            self.app.mqtt.mark_ext_dirty(i)
        return True

    # ── Shelly-Abfrage ──────────────────────────────────────────────────────
    async def _get_json(self, url: str, timeout: float) -> dict | None:
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status != 200:
                    return None
                raw = await r.content.read(SHELLY_MAX_RESP + 1)
                if len(raw) > SHELLY_MAX_RESP:
                    return None
                data = json.loads(raw)
                return data if isinstance(data, dict) else None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None

    async def _fetch_info(self, i: int) -> None:
        e = self.entries("shelly")[i]
        doc = await self._get_json(f"http://{e['ip']}/rpc/Shelly.GetDeviceInfo", 3)
        if not doc:
            return
        dirty = False
        name = doc.get("name") or ""
        if isinstance(name, str) and name:
            self.st["shelly"][i].name_from_device = name[:31]
            if not e["name"]:
                e["name"] = name[:31]
                dirty = True
        dev_id = doc.get("id") or ""
        if isinstance(dev_id, str) and dev_id and not e["id"]:
            e["id"] = dev_id[:39]
            dirty = True
            _LOGGER.info("[Shelly] %s – ID gelernt: %s", e["ip"], dev_id)
        if dirty:
            self.app.settings.save()

    async def get_status(self, i: int) -> bool:
        if not 0 <= i < self.cfg["sh_count"]:
            return False
        e = self.entries("shelly")[i]
        s = self.st["shelly"][i]
        if not e["ip"]:
            s.reachable = False
            return False
        doc = await self._get_json(f"http://{e['ip']}/rpc/Switch.GetStatus?id=0", 1.5)
        if doc is None:
            s.reachable = False
            s.fail_count += 1
            return False
        aen = doc.get("aenergy")
        total = aen.get("total") if isinstance(aen, dict) else None
        total = float(total) if isinstance(total, (int, float)) else None
        apower = float(doc.get("apower") or 0.0)
        now = CLOCK.mono()
        if now >= s.cmd_lock_until:
            s.on = bool(doc.get("output", False))
        s.apower_w = apower
        s.voltage_v = float(doc.get("voltage") or 0.0)
        s.current_a = float(doc.get("current") or 0.0)
        temp = doc.get("temperature")
        s.temp_c = float(temp.get("tC") or 0.0) if isinstance(temp, dict) else 0.0
        s.reachable = True
        prev_poll = s.last_poll
        s.last_poll = now
        s.fail_count = 0
        thr = e["rw"]
        was_run = s.running
        is_run = thr > 0 and s.on and apower >= thr
        s.running = is_run
        if is_run:
            s.idle_since = 0.0
        if thr > 0 and is_run != was_run:
            self.app.events.log(EV_RUN, i, ER_NONE, is_run, self.display_name("shelly", i))
        dt = (now - prev_poll) if prev_poll else SHELLY_POLL_S
        self.app.energy.shelly_sample(i, total, apower, dt)
        self.app.mqtt.mark_shelly_dirty(i)
        return True

    async def poll_all(self) -> None:
        for i in range(self.cfg["sh_count"]):
            await self.get_status(i)
            e = self.entries("shelly")[i]
            s = self.st["shelly"][i]
            if s.reachable and (not s.name_from_device or not e["id"]):
                await self._fetch_info(i)
            if not e["ip"]:
                continue
            if not s.reachable and not s.offline_logged and s.fail_count >= SHELLY_FAIL_RECOVER:
                s.offline_logged = True
                self.app.events.log(EV_REACH, i, ER_NONE, False, self.display_name("shelly", i))
            elif s.reachable and s.offline_logged:
                s.offline_logged = False
                self.app.events.log(EV_REACH, i, ER_NONE, True, self.display_name("shelly", i))

    # ── Netzwerk-Suche ──────────────────────────────────────────────────────
    def _scan_hosts(self) -> list[str]:
        subnet = os.environ.get("EO_SCAN_SUBNET", "").strip()
        own = local_ipv4(self.cfg["sl_ip"] if self.cfg["sl_ip"][:1].isdigit() else "8.8.8.8")
        try:
            if subnet:
                if subnet.count(".") == 2:
                    subnet += ".0/24"
                net = ipaddress.ip_network(subnet, strict=False)
            else:
                net = ipaddress.ip_network(own + "/24", strict=False)
        except ValueError:
            return []
        return [str(h) for h in net.hosts() if str(h) != own][:1024]

    async def run_scan(self, recovery: bool) -> None:
        sc = self.scan
        sc.found = []
        sc.running = True
        sc.done = False
        sc.recovery = recovery
        hosts = self._scan_hosts()
        _LOGGER.info("[Scan] Suche Shelly-Geräte (%d Adressen) …", len(hosts))
        sem = asyncio.Semaphore(48)

        async def probe(ip: str) -> None:
            async with sem:
                if len(sc.found) >= MAX_FOUND:
                    return
                doc = await self._get_json(f"http://{ip}/rpc/Shelly.GetDeviceInfo", 1.0)
                if not doc or not isinstance(doc.get("gen"), int) or doc["gen"] < 2:
                    return
                if len(sc.found) >= MAX_FOUND:
                    return
                dev_id = str(doc.get("id") or ip)
                name = str(doc.get("name") or "") or dev_id
                sc.found.append(Found(ip=ip, name=name[:31], model=str(doc.get("model") or "Shelly")[:23],
                                      id=dev_id[:39]))
                _LOGGER.info("[Scan] Gefunden: %s @ %s", name, ip)

        try:
            await asyncio.gather(*(probe(ip) for ip in hosts))
            sc.found.sort(key=lambda f: tuple(int(x) for x in f.ip.split(".")))
        finally:
            sc.running = False
            sc.done = True
        _LOGGER.info("[Scan] Fertig – %d Gerät(e) gefunden", len(sc.found))
        if recovery:
            await self._recover_apply()

    async def _recover_apply(self) -> None:
        changed = False
        for f in self.scan.found:
            if not f.id:
                continue
            for i in range(self.cfg["sh_count"]):
                e = self.entries("shelly")[i]
                if not e["id"] or e["id"] != f.id:
                    continue
                if e["ip"] == f.ip:
                    self.st["shelly"][i].fail_count = 0
                    continue
                _LOGGER.info("[Recovery] %s: IP %s → %s", e["name"], e["ip"], f.ip)
                self.app.events.log(EV_IPMOVE, i, ER_NONE, True, f.ip)
                e["ip"] = f.ip
                self.st["shelly"][i].fail_count = 0
                changed = True
        if changed:
            self.app.settings.save()
            await self.poll_all()

    def recover_ips(self) -> None:
        if self.scan.running:
            return
        need = any(e["id"] and e["ip"] and self.st["shelly"][i].fail_count >= SHELLY_FAIL_RECOVER
                   for i, e in enumerate(self.entries("shelly")[: self.cfg["sh_count"]]))
        if not need:
            return
        now = CLOCK.mono()
        if self.scan.last_recover and now - self.scan.last_recover < SHELLY_RECOVER_COOLDOWN_S:
            return
        self.scan.last_recover = now
        _LOGGER.info("[Recovery] Gerät unerreichbar – starte Suchlauf nach neuer IP …")
        self._spawn(self.run_scan(True))

    def start_scan(self) -> None:
        if not self.scan.running:
            self._spawn(self.run_scan(False))

    # ── Regeln und Riegel ───────────────────────────────────────────────────
    def window_open(self, kind: str, i: int) -> bool:
        e = self.entries(kind)[i]
        days = e["wd"] or 0x7F
        anytime = e["ws"] == e["we"] or (e["ws"] == 0 and e["we"] == 24)
        if anytime and days == 0x7F:
            return True
        now = CLOCK.now()
        if not days & (1 << now.weekday()):
            return False
        if anytime:
            return True
        now_min = now.hour * 60 + now.minute
        s, en = e["ws"] * 60, e["we"] * 60
        if s < en:
            return s <= now_min < en
        return now_min >= s or now_min < en

    def daycap_reached(self, kind: str, i: int) -> bool:
        cap = self.entries(kind)[i]["mx"]
        return cap > 0 and self.st[kind][i].today_on_s >= cap * 60

    def quota_s(self, kind: str, i: int) -> int:
        e = self.entries(kind)[i]
        q = max(0, e["rt"])
        if e["mx"] > 0 and q > e["mx"]:
            q = e["mx"]
        return q * 60

    def self_regulated(self, i: int) -> bool:
        return 0 <= i < MAX_SHELLY and self.entries("shelly")[i]["rw"] > 0

    def is_running(self, kind: str, i: int) -> bool:
        s = self.st[kind][i]
        if kind == "ext" or not self.self_regulated(i):
            return s.on
        return s.on and s.reachable and s.running

    def failsafe_active(self) -> bool:
        return self.failsafe["shelly"] or self.failsafe["ext"]

    # ── Tageswechsel ────────────────────────────────────────────────────────
    def day_rollover(self, kind: str) -> None:
        yday = CLOCK.now().timetuple().tm_yday
        if yday == self._cur_yday[kind]:
            return
        if self._cur_yday[kind] == -1:
            # Container-Start: Shelly-Laufzeit aus den gespeicherten Tageszählern übernehmen,
            # damit Tageslimit und Mindestlaufzeit einen Neustart überstehen.
            self._cur_yday[kind] = yday
            if kind == "shelly":
                for i, s in enumerate(self.st[kind]):
                    s.today_on_s = int(self.app.energy.c["day_on_s"][i])
            return
        self._cur_yday[kind] = yday
        for s in self.st[kind]:
            s.today_on_s = 0
            s.forced_on = False
        self.auto_eval_req = True
        _LOGGER.info("[Laufzeit] Neuer Tag – Tageszähler (%s) zurückgesetzt", kind)

    # ── Zeitschaltuhr ───────────────────────────────────────────────────────
    async def sched_tick(self, kind: str) -> None:
        now = CLOCK.mono()
        dt = CLOCK.now()
        for i in range(self.count(kind)):
            if not self.slot_used(kind, i):
                continue
            e = self.entries(kind)[i]
            s = self.st[kind][i]
            act = sched.active(e["sch"], dt)
            blocked = s.sched_retry != 0 and now < s.sched_retry
            if s.sched_retry and not blocked:
                s.sched_retry = 0.0
            if s.sched_skip >= 0 and s.sched_skip != act:
                s.sched_skip = -1
            if act >= 0 and self.daycap_reached(kind, i):
                continue
            if act >= 0:
                if s.sched_skip == act:
                    continue
                if s.sched_on and s.on:
                    continue
                if not s.on:
                    if blocked:
                        continue
                    if not await self.set(kind, i, True, ER_SCHEDULE):
                        s.sched_retry = now + SCHED_RETRY_S
                        continue
                if not s.sched_on:
                    _LOGGER.info("[Uhr] %s – Programm %d gestartet", self.display_name(kind, i), act + 1)
                s.sched_on = True
                s.forced_on = False
                s.auto_active = False
                s.on_ticks = s.off_ticks = 0
                s.nd_retry = 0.0
            elif s.sched_on:
                ovr = s.override_until != 0
                autoc = e["auto"]
                if ovr or autoc or not s.on:
                    s.sched_on = False
                    if autoc and not ovr:
                        s.force_eval = True
                        s.auto_active = True
                        s.on_ticks = s.off_ticks = 0
                        self.auto_eval_req = True
                    _LOGGER.info("[Uhr] %s – Programm beendet", self.display_name(kind, i))
                    continue
                if blocked:
                    continue
                if not await self.set(kind, i, False, ER_SCHEDULE):
                    s.sched_retry = now + SCHED_RETRY_S
                    continue
                s.sched_on = False
                _LOGGER.info("[Uhr] %s – Programm beendet", self.display_name(kind, i))

    # ── Befristung, Tageslimit, Freigabefenster ─────────────────────────────
    async def schedule_tick(self, kind: str) -> None:
        now = CLOCK.mono()
        if not self._rt_last[kind]:
            self._rt_last[kind] = now
        delta = int(now - self._rt_last[kind])
        if delta > 0:
            self._rt_last[kind] += delta
            for i in range(self.count(kind)):
                if self.is_running(kind, i):
                    self.st[kind][i].today_on_s += delta
                    if kind == "shelly":
                        self.app.energy.add_runtime(i, delta)

        for i in range(self.count(kind)):
            e = self.entries(kind)[i]
            s = self.st[kind][i]
            if s.override_until and now >= s.override_until:
                s.override_until = 0.0
                ret_auto = s.override_ret_auto
                if ret_auto:
                    e["auto"] = True
                    s.force_eval = True
                    s.on_ticks = s.off_ticks = 0
                self.app.events.log(EV_SWITCH, self._ev_dev(kind, i), ER_TIMER, False,
                                    self.display_name(kind, i))
                _LOGGER.info("[Timer] %s – Befristung abgelaufen%s", self.display_name(kind, i),
                             ", zurück auf Automatik" if ret_auto else "")
                if ret_auto:
                    self.app.settings.save()
                    self.auto_eval_req = True

            if self.daycap_reached(kind, i):
                ovr = s.override_until != 0
                if (s.on and not ovr and (e["auto"] or s.forced_on or s.sched_on)
                        and await self.set(kind, i, False, ER_DAY_CAP)):
                    s.forced_on = s.sched_on = s.auto_active = False
                    s.on_ticks = s.off_ticks = 0
                    _LOGGER.info("[Tageslimit] %s – Höchst-Laufzeit von %d min erreicht, aus",
                                 self.display_name(kind, i), e["mx"])
                continue

            if self.window_open(kind, i):
                continue
            if not s.on or s.override_until or s.sched_on:
                continue
            if not e["auto"] and not s.forced_on:
                continue
            s.forced_on = s.auto_active = False
            s.on_ticks = s.off_ticks = 0
            await self.set(kind, i, False, ER_WINDOW)

    # ── Eigenregelung ───────────────────────────────────────────────────────
    async def demand_tick(self) -> None:
        now = CLOCK.mono()
        min_on = self.cfg["min_on_min"] * 60
        for i in range(self.cfg["sh_count"]):
            e = self.entries("shelly")[i]
            s = self.st["shelly"][i]
            if s.nd_retry and now >= s.nd_retry:
                s.nd_retry = 0.0
            thr, idle_min = e["rw"], e["io"]
            if thr <= 0:
                continue
            if not s.on or not s.reachable:
                s.idle_since = 0.0
                continue
            if s.running:
                continue
            if s.last_on and now - s.last_on < RUN_START_GRACE_S:
                continue
            if not s.idle_since:
                s.idle_since = now
                continue
            if idle_min <= 0 or now - s.idle_since < idle_min * 60:
                continue
            if s.override_until or s.sched_on or (not e["auto"] and not s.forced_on):
                continue
            if s.last_on and now - s.last_on < min_on:
                continue
            _LOGGER.info("[Eigenregelung] %s – kein Bedarf seit %d min, Steckdose aus",
                         self.display_name("shelly", i), int((now - s.idle_since) / 60))
            s.forced_on = s.auto_active = False
            s.on_ticks = s.off_ticks = 0
            s.idle_since = 0.0
            s.nd_retry = now + ND_RETRY_S
            await self.set("shelly", i, False, ER_NO_DEMAND)

    # ── Batterie-Vorrang ────────────────────────────────────────────────────
    def batt_update(self, has_battery: bool, battery_w: float) -> None:
        guard = self.cfg["batt_grd"]
        if not has_battery or guard <= 0:
            self.batt_block = False
            return
        discharge = -battery_w if battery_w < 0 else 0.0
        release = guard * BATT_GUARD_RELEASE_FACTOR
        block = discharge > release if self.batt_block else discharge >= guard
        if block != self.batt_block:
            self.batt_block = block
            _LOGGER.info("[Batterie] Entladung %.0f W → Automatik %s", discharge,
                         "gesperrt" if block else "wieder frei")

    # ── Überschuss-Automatik ────────────────────────────────────────────────
    async def auto_control(self, kind: str, surplus_w: float) -> float:
        c = self.cfg
        available = surplus_w
        now = CLOCK.mono()
        batt_block = self.batt_block
        self.last_surplus[kind] = surplus_w
        n = self.count(kind)
        maxpri = MAX_SHELLY if kind == "shelly" else MAX_EXT
        min_on, min_off = c["min_on_min"] * 60, c["min_off_min"] * 60
        need_off, need_on = hyst_off_ticks(c), hyst_on_ticks(c)

        # Ausschalten: niedrigste Priorität zuerst
        for pri in range(maxpri, 0, -1):
            for i in range(n):
                e = self.entries(kind)[i]
                s = self.st[kind][i]
                if e["pri"] != pri or not e["auto"]:
                    continue
                freed = float(e["pw"])
                if kind == "shelly" and e["rw"] > 0 and s.reachable:
                    freed = s.apower_w if s.apower_w > 0 else 0.0
                if s.forced_on or s.sched_on or not s.on:
                    continue
                force = s.force_eval
                if not force and now - s.last_on < min_on:
                    s.off_ticks = 0
                    continue
                deficit = surplus_w < -c["off_margin"] or batt_block
                if deficit:
                    s.off_ticks += 1
                    s.on_ticks = 0
                    _LOGGER.debug("%s off_ticks=%d/%d", self.display_name(kind, i), s.off_ticks, need_off)
                    if force or s.off_ticks >= need_off:
                        await self.set(kind, i, False, ER_BATTERY if batt_block else ER_DEFICIT)
                        s.auto_active = False
                        s.off_ticks = 0
                        s.force_eval = False
                        surplus_w += freed
                        available = surplus_w
                        if not batt_block and surplus_w >= 0:
                            return available
                else:
                    s.off_ticks = 0
                    s.force_eval = False

        # Einschalten: höchste Priorität zuerst
        for pri in range(1, maxpri + 1):
            for i in range(n):
                e = self.entries(kind)[i]
                s = self.st[kind][i]
                if e["pri"] != pri or not e["auto"]:
                    continue
                if not self.window_open(kind, i) or self.daycap_reached(kind, i):
                    continue
                if s.forced_on or s.on:
                    continue
                if s.nd_retry and now < s.nd_retry:
                    s.on_ticks = 0
                    s.force_eval = False
                    continue
                force = s.force_eval
                if not force and s.last_off and now - s.last_off < min_off:
                    s.on_ticks = 0
                    continue
                needed = e["pw"] + c["on_margin"]
                if not batt_block and available >= needed:
                    s.on_ticks += 1
                    s.off_ticks = 0
                    _LOGGER.debug("%s on_ticks=%d/%d", self.display_name(kind, i), s.on_ticks, need_on)
                    if force or s.on_ticks >= need_on:
                        await self.set(kind, i, True, ER_SURPLUS)
                        s.auto_active = True
                        s.on_ticks = 0
                        s.force_eval = False
                        available -= e["pw"]
                else:
                    s.on_ticks = 0
                    s.force_eval = False
        return available

    async def distribute(self, surplus_w: float) -> None:
        remain = await self.auto_control("shelly", surplus_w)
        await self.auto_control("ext", remain)

    # ── Fail-Safe ───────────────────────────────────────────────────────────
    async def failsafe_control(self, kind: str, sl_age_s: float) -> None:
        limit = self.cfg["sl_fsafe"] * 60
        if limit <= 0 or sl_age_s < limit:
            if self.failsafe[kind]:
                self.failsafe[kind] = False
                if kind == "shelly":
                    _LOGGER.info("[Fail-Safe] Aufgehoben – Automatik wieder scharf")
                    self.app.events.log(EV_FAILSAFE, -1, ER_NONE, False, None)
            return
        if self.failsafe[kind]:
            return
        self.failsafe[kind] = True
        if kind == "shelly":
            _LOGGER.warning("[Fail-Safe] SolarLog seit %ds nicht erreichbar – schalte Auto-Geräte ab",
                            int(sl_age_s))
            self.app.events.log(EV_FAILSAFE, -1, ER_NONE, True, f"seit {int(sl_age_s // 60)} min")
        for i in range(self.count(kind)):
            e = self.entries(kind)[i]
            s = self.st[kind][i]
            if not e["auto"] or s.forced_on or s.sched_on or not s.on:
                continue
            await self.set(kind, i, False, ER_FAILSAFE)
            s.auto_active = False

    # ── Schlechtwetter-Nachlauf ─────────────────────────────────────────────
    async def forced_runtime(self, kind: str) -> None:
        c = self.cfg
        now = CLOCK.mono()
        dt = CLOCK.now()
        now_min = dt.hour * 60 + dt.minute
        start, end = c["fw_start"] * 60, c["fw_end"] * 60
        if start == end:
            in_window = False
        elif start < end:
            in_window = start <= now_min < end
        else:
            in_window = now_min >= start or now_min < end

        n = self.count(kind)
        for i in range(n):
            s = self.st[kind][i]
            if not s.forced_on:
                continue
            quota = self.quota_s(kind, i)
            autoc = self.entries(kind)[i]["auto"]
            if not autoc or not in_window or s.today_on_s >= quota:
                s.forced_on = False
                await self.set(kind, i, False, ER_FORCED_END)
                _LOGGER.info("[Nachlauf] %s – beendet (%ds/%ds heute)%s", self.display_name(kind, i),
                             s.today_on_s, quota, "" if autoc else " – Automatik aus")
        if not in_window:
            return
        for i in range(n):
            e = self.entries(kind)[i]
            if e["rt"] <= 0 or not e["auto"]:
                continue
            if not self.is_running(kind, i):
                continue
            if self.st[kind][i].today_on_s < self.quota_s(kind, i):
                return  # es läuft bereits ein bedürftiges Gerät – immer nur eines
        pick, pick_pri = -1, 99
        for i in range(n):
            e = self.entries(kind)[i]
            s = self.st[kind][i]
            if e["rt"] <= 0 or not e["auto"] or not self.slot_used(kind, i):
                continue
            if not self.window_open(kind, i) or s.on:
                continue
            if s.nd_retry and now < s.nd_retry:
                continue
            if s.today_on_s >= self.quota_s(kind, i):
                continue
            if e["pri"] < pick_pri:
                pick, pick_pri = i, e["pri"]
        if pick >= 0 and await self.set(kind, pick, True, ER_FORCED):
            s = self.st[kind][pick]
            s.forced_on = True
            _LOGGER.info("[Nachlauf] %s – Schlechtwetter-Nachlauf EIN (noch %ds bis Tagessoll)",
                         self.display_name(kind, pick), self.quota_s(kind, pick) - s.today_on_s)

    # ── Einstellungen übernommen ────────────────────────────────────────────
    def settings_changed(self, orig: dict, new: dict) -> None:
        for i in range(MAX_SHELLY):
            o, n = orig["shelly"][i], new["shelly"][i]
            s = self.st["shelly"][i]
            if o["ip"] != n["ip"] or o["id"] != n["id"]:
                self.st["shelly"][i] = DevState()
                self.app.energy.reset_shelly(i)
                continue
            if o["sch"] != n["sch"]:
                s.sched_skip = -1
            if o["rw"] != n["rw"]:
                s.running = False
                s.idle_since = 0.0
                s.nd_retry = 0.0
        for i in range(MAX_EXT):
            o, n = orig["ext"][i], new["ext"][i]
            if o["name"] != n["name"]:
                self.st["ext"][i] = DevState()
            elif o["sch"] != n["sch"]:
                self.st["ext"][i].sched_skip = -1
