"""Load control.

Shelly plugs (Gen2/Gen3 RPC) and external switches (MQTT only) follow the same
rules and share this implementation, with `kind` = "shelly" | "ext". Differences:

* Shelly plugs are switched and read over HTTP. External switches get their
  wanted state over MQTT (``<prefix>/ext/<i>/set``) and count as switched at
  once; if a state topic is configured, their real state is checked against it.
* Only Shelly plugs know reachability, readings and self-regulation (run threshold).
* Shelly plugs come first: external switches get the surplus that is left after
  the Shelly decisions.

The surplus decisions themselves are made by `control.plan_group`; this module
builds the snapshots, carries out the actions and runs everything that depends
on time (programs, windows, caps, catch-up, fail-safe).

Dry run (``dry_run``): the automatic decides as usual but sends no command.
Its decisions only change the state shown here (``virt``); the surplus it works
with is corrected by what the virtually switched loads would draw. Manual
commands still switch for real.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import aiohttp

from . import control, sched
from .clock import CLOCK
from .const import (
    BATT_GUARD_RELEASE_FACTOR,
    ER_CMD_FAIL,
    ER_DAY_CAP,
    ER_FAILSAFE,
    ER_FORCED,
    ER_FORCED_END,
    ER_MANUAL,
    ER_MQTT,
    ER_NO_DEMAND,
    ER_NONE,
    ER_SCHEDULE,
    ER_TIMER,
    ER_WINDOW,
    EV_AUTOMODE,
    EV_FAILSAFE,
    EV_IPMOVE,
    EV_REACH,
    EV_RUN,
    EV_SWITCH,
    MAX_EXT,
    MAX_SHELLY,
    ND_RETRY_S,
    RUN_START_GRACE_S,
    SCHED_RETRY_S,
    SHELLY_CMD_LOCK_S,
    SHELLY_FAIL_RECOVER,
    SHELLY_RECOVER_COOLDOWN_S,
)
from .settings import hyst_off_ticks, hyst_on_ticks

if TYPE_CHECKING:
    from .app import EnergyOptimizer

_LOGGER = logging.getLogger(__name__)

MAX_FOUND = 32
SHELLY_MAX_RESP = 8192
# Learned power: median of the readings while the device really draws power.
LEARN_SAMPLES = 120
LEARN_MIN_SAMPLES = 10
LEARN_MIN_W = 20.0
# External switch with a state topic: time it may take to confirm a command.
EXT_FB_GRACE_S = 60


@dataclass
class DevState:
    on: bool = False              # state the control works with (virtual in a dry run)
    real_on: bool = False         # state of the output (Shelly: read back; ext: last command)
    virt: bool = False            # `on` comes from a dry-run decision, not from the device
    reachable: bool = False
    auto_active: bool = False
    force_eval: bool = False
    on_ticks: int = 0
    off_ticks: int = 0
    last_on: float = 0.0          # CLOCK.mono() at the last switch-on (0 = never)
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
    override_until: float = 0.0   # 0 = no time limit
    override_ret_auto: bool = False
    running: bool = False
    idle_since: float = 0.0
    nd_retry: float = 0.0
    sched_on: bool = False
    sched_skip: int = -1
    sched_retry: float = 0.0
    last_cmd: int = -1
    cmd_t: float = 0.0            # last real command (external feedback check)
    fb: bool | None = None        # state reported on the state topic (external switches)
    fb_t: float = 0.0
    samples: deque = field(default_factory=lambda: deque(maxlen=LEARN_SAMPLES))


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
    """Own LAN address (for the subnet scan; with network_mode: host the Pi's)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((probe, 80))
        return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Devices:
    def __init__(self, app: EnergyOptimizer) -> None:
        self.app = app
        self.st = {"shelly": [DevState() for _ in range(MAX_SHELLY)],
                   "ext": [DevState() for _ in range(MAX_EXT)]}
        self.batt_block = False
        self.failsafe = {"shelly": False, "ext": False}
        self.last_surplus: dict[str, float | None] = {"shelly": None, "ext": None}
        self.last_available: dict[str, float] = {"shelly": 0.0, "ext": 0.0}
        self.auto_eval_req = False
        self.poll_req = False
        self.scan = ScanState()
        self._rt_last = {"shelly": 0.0, "ext": 0.0}
        self._cur_yday = {"shelly": -1, "ext": -1}
        self._tasks: set[asyncio.Task] = set()

    # ── Access helpers ──────────────────────────────────────────────────────
    @property
    def cfg(self) -> dict:
        return self.app.settings.cfg

    @property
    def dry(self) -> bool:
        return bool(self.cfg.get("dry_run"))

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

    def _log_switch(self, kind: str, i: int, reason: int, on: bool, dry: bool = False) -> None:
        self.app.events.log(EV_SWITCH, self._ev_dev(kind, i), reason, on,
                            self.display_name(kind, i), self.last_surplus[kind], dry=dry)

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    @property
    def session(self) -> aiohttp.ClientSession:
        return self.app.session

    def learned_w(self, i: int) -> int:
        """Typical power of plug i while its device runs (0 = not learned yet)."""
        return int(self.app.energy.c["learned_w"][i]) if 0 <= i < MAX_SHELLY else 0

    # ── Switching ───────────────────────────────────────────────────────────
    async def set(self, kind: str, i: int, on: bool, reason: int = ER_NONE,
                  force: bool = False) -> bool:
        if not 0 <= i < self.count(kind):
            return False
        s = self.st[kind][i]
        if self.dry and reason not in (ER_MANUAL, ER_MQTT):
            # Dry run: remember the decision, switch nothing.
            changed = s.on != on or not s.virt
            s.on = on
            s.virt = True
            if on:
                s.last_on = CLOCK.mono()
            else:
                s.last_off = CLOCK.mono()
            if changed:
                _LOGGER.info("[Dry run] %s → %s (not switched)", self.display_name(kind, i),
                             "ON" if on else "OFF")
                self._log_switch(kind, i, reason, on, dry=True)
                self._mark_dirty(kind, i)
            return True
        s.virt = False
        if kind == "ext":
            changed = s.last_cmd != int(on)
            s.last_cmd = int(on)
            s.on = s.real_on = on
            s.cmd_t = CLOCK.mono()
            if on:
                s.last_on = CLOCK.mono()
            else:
                s.last_off = CLOCK.mono()
            if changed:
                _LOGGER.info("[Ext] %s → %s (MQTT)", self.display_name(kind, i), "ON" if on else "OFF")
                self._log_switch(kind, i, reason, on)
                self.app.mqtt.mark_ext_dirty(i)
            return True

        ip = self.entries(kind)[i]["ip"]
        if not ip:
            return False
        if not force and s.reachable and s.last_poll and s.real_on == on:
            # The plug already reports this state: no command, no traffic.
            s.on = on
            s.last_cmd = int(on)
            return True
        url = f"http://{ip}/rpc/Switch.Set?id=0&on={'true' if on else 'false'}"
        ok = False
        code: Any = "-"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as r:
                code = r.status
                ok = r.status == 200
        except (TimeoutError, aiohttp.ClientError) as err:
            code = type(err).__name__
        if ok:
            changed = s.last_cmd != int(on)
            s.last_cmd = int(on)
            s.on = s.real_on = on
            if on:
                s.last_on = CLOCK.mono()
            else:
                s.last_off = CLOCK.mono()
            s.running = False
            s.idle_since = 0.0
            if changed:
                self.app.energy.count_switch(i)
            self.app.mqtt.mark_shelly_dirty(i)
            _LOGGER.info("[Shelly] %s → %s", self.display_name(kind, i), "ON" if on else "OFF")
            self._log_switch(kind, i, reason, on)
        else:
            _LOGGER.warning("[Shelly] %s: command failed (%s)", self.display_name(kind, i), code)
            self.app.events.log(EV_SWITCH, i, ER_CMD_FAIL, on, self.display_name(kind, i))
        return ok

    def _mark_dirty(self, kind: str, i: int) -> None:
        if kind == "shelly":
            self.app.mqtt.mark_shelly_dirty(i)
        else:
            self.app.mqtt.mark_ext_dirty(i)

    def queue_set(self, kind: str, i: int, on: bool, reason: int) -> None:
        """Manual command: show it in the interface at once, send it in the background."""
        s = self.st[kind][i]
        s.on = on
        s.virt = False
        s.cmd_lock_until = CLOCK.mono() + SHELLY_CMD_LOCK_S
        self._spawn(self.set(kind, i, on, reason, force=True))

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
            if s.virt:
                # A dry-run decision is no longer anybody's: back to the real state.
                s.on, s.virt, s.auto_active = s.real_on, False, False
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
        self._mark_dirty(kind, i)
        return True

    # ── Shelly readings ─────────────────────────────────────────────────────
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
        except (TimeoutError, aiohttp.ClientError, ValueError):
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
            _LOGGER.info("[Shelly] %s – learned id %s", e["ip"], dev_id)
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
            s.real_on = bool(doc.get("output", False))
            if not s.virt:
                s.on = s.real_on
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
        is_run = thr > 0 and s.real_on and apower >= thr
        s.running = is_run
        if is_run:
            s.idle_since = 0.0
        if thr > 0 and is_run != was_run:
            self.app.events.log(EV_RUN, i, ER_NONE, is_run, self.display_name("shelly", i))
        self._learn(i, s, apower, thr)
        dt = (now - prev_poll) if prev_poll else self.cfg["sh_poll_s"]
        self.app.energy.shelly_sample(i, total, apower, dt, self.cfg["sh_poll_s"])
        self.app.mqtt.mark_shelly_dirty(i)
        return True

    def _learn(self, i: int, s: DevState, apower: float, thr: int) -> None:
        if not s.real_on or apower < max(LEARN_MIN_W, float(thr)):
            return
        s.samples.append(apower)
        if len(s.samples) >= LEARN_MIN_SAMPLES:
            self.app.energy.set_learned(i, statistics.median(s.samples))

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

    # ── External switches: reported state ───────────────────────────────────
    def ext_feedback(self, i: int, on: bool) -> None:
        if not 0 <= i < self.cfg["ex_count"]:
            return
        s = self.st["ext"][i]
        s.fb = on
        s.fb_t = CLOCK.mono()
        self.app.mqtt.mark_ext_dirty(i)

    def ext_mismatch(self, i: int) -> bool:
        """The switch has a state topic and has not confirmed the last command."""
        if not 0 <= i < self.cfg["ex_count"] or not self.entries("ext")[i]["st"]:
            return False
        s = self.st["ext"][i]
        if not s.cmd_t or CLOCK.mono() - s.cmd_t < EXT_FB_GRACE_S:
            return False
        return s.fb is None or s.fb != s.real_on

    # ── Network scan ────────────────────────────────────────────────────────
    def _scan_hosts(self) -> list[str]:
        subnet = os.environ.get("EO_SCAN_SUBNET", "").strip()
        src_host = self.cfg["sl_ip"] if self.cfg["src"] == "solarlog" else self.cfg["src_host"]
        own = local_ipv4(src_host if src_host[:1].isdigit() else "8.8.8.8")
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
        _LOGGER.info("[Scan] Looking for Shelly devices (%d addresses) …", len(hosts))
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
                _LOGGER.info("[Scan] Found %s at %s", name, ip)

        try:
            await asyncio.gather(*(probe(ip) for ip in hosts))
            sc.found.sort(key=lambda f: tuple(int(x) for x in f.ip.split(".")))
        finally:
            sc.running = False
            sc.done = True
        _LOGGER.info("[Scan] Done – %d device(s) found", len(sc.found))
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
                _LOGGER.info("[Recovery] %s: address %s → %s", e["name"], e["ip"], f.ip)
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
        _LOGGER.info("[Recovery] Plug unreachable – scanning for its new address …")
        self._spawn(self.run_scan(True))

    def start_scan(self) -> None:
        if not self.scan.running:
            self._spawn(self.run_scan(False))

    # ── Rules and locks ─────────────────────────────────────────────────────
    def window_open(self, kind: str, i: int) -> bool:
        return control.window_open_at(self.entries(kind)[i], CLOCK.now())

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

    # ── Master switch and state for Home Assistant ──────────────────────────
    @property
    def enabled(self) -> bool:
        """Master switch: off leaves every load where it is and makes no decisions."""
        return bool(self.cfg["auto_en"])

    def set_enabled(self, on: bool, src: int = ER_MANUAL) -> None:
        if self.enabled == on:
            return
        self.cfg["auto_en"] = on
        self.enabled_changed(on, src)
        self.app.settings.save()

    def enabled_changed(self, on: bool, src: int = ER_MANUAL) -> None:
        for kind in ("shelly", "ext"):
            for i in range(self.count(kind)):
                s = self.st[kind][i]
                s.on_ticks = s.off_ticks = 0
                if on:
                    s.force_eval = self.entries(kind)[i]["auto"]
                else:
                    # Loads stay as they are, but the optimizer stops claiming them.
                    s.auto_active = s.forced_on = False
                    if s.virt:
                        s.on, s.virt = s.real_on, False
        if not on:
            self.failsafe = {"shelly": False, "ext": False}
        self.auto_eval_req = True
        self.app.events.log(EV_AUTOMODE, -1, src, on, "EnergyOptimizer")
        _LOGGER.info("[Automatic] Master switch %s", "ON" if on else "OFF")

    def dry_run_changed(self, on: bool) -> None:
        """Entering or leaving the dry run: start again from the real states."""
        for kind in ("shelly", "ext"):
            for i in range(self.count(kind)):
                s = self.st[kind][i]
                s.on_ticks = s.off_ticks = 0
                if s.virt:
                    s.on, s.virt = s.real_on, False
                    s.auto_active = s.forced_on = s.sched_on = False
                s.force_eval = self.entries(kind)[i]["auto"]
        self.auto_eval_req = True
        _LOGGER.info("[Dry run] %s", "started – no commands are sent" if on else "ended")

    def managed(self, kind: str, i: int) -> bool:
        """On because the optimizer switched it on (surplus or catch-up)."""
        s = self.st[kind][i]
        return s.on and (s.auto_active or s.forced_on)

    def managed_power(self) -> float:
        total = 0.0
        for kind in ("shelly", "ext"):
            for i in range(self.count(kind)):
                if not self.managed(kind, i):
                    continue
                s, e = self.st[kind][i], self.entries(kind)[i]
                if kind == "shelly" and s.reachable and s.apower_w > 0 and not s.virt:
                    total += s.apower_w
                else:
                    total += float(e["pw"])
        return total

    def load_status(self, kind: str, i: int) -> str:
        """Why a load is in its state, with the values of ha-energyoptimizer."""
        e, s = self.entries(kind)[i], self.st[kind][i]
        now = CLOCK.mono()
        if s.override_until:
            return "manual"
        if s.sched_on:
            return "schedule"
        if not e["auto"] or not self.enabled:
            return "disabled"
        if s.on:
            if s.forced_on:
                return "catchup"
            return "surplus" if s.auto_active else "external"
        min_off = self.cfg["min_off_min"] * 60
        if (self.failsafe_active() or self.batt_block or not self.window_open(kind, i)
                or self.daycap_reached(kind, i) or (s.nd_retry and now < s.nd_retry)
                or (s.last_off and now - s.last_off < min_off)
                or (kind == "shelly" and not s.reachable)):
            return "blocked"
        return "waiting"

    def explain(self, kind: str, i: int) -> dict[str, Any]:
        """What the optimizer does with this load right now and why.

        Returns {"c": code, …values}; the interface turns it into a sentence."""
        e, s, c = self.entries(kind)[i], self.st[kind][i], self.cfg
        now = CLOCK.mono()
        if kind == "shelly" and not s.reachable and e["ip"]:
            return {"c": "offline"}
        if s.override_until:
            return {"c": "manual", "on": s.on, "s": max(0, int(s.override_until - now)),
                    "ret": s.override_ret_auto}
        if s.sched_on:
            act = sched.active(e["sch"], CLOCK.now())
            return {"c": "schedule", "m": sched.ends_in_min(e["sch"][act], CLOCK.now()) if act >= 0 else 0}
        if not self.enabled:
            return {"c": "master_off", "on": s.on}
        if not e["auto"]:
            return {"c": "auto_off", "on": s.on}
        if s.forced_on:
            return {"c": "catchup", "m": s.today_on_s // 60, "q": self.quota_s(kind, i) // 60}
        if self.failsafe_active():
            return {"c": "failsafe"}
        min_on, min_off = c["min_on_min"] * 60, c["min_off_min"] * 60
        need_on, need_off = hyst_on_ticks(c), hyst_off_ticks(c)
        if s.on:
            if not s.auto_active:
                return {"c": "external"}
            if self.batt_block:
                return {"c": "battery_off", "n": max(1, need_off - s.off_ticks)}
            if s.off_ticks > 0:
                return {"c": "switching_off", "n": max(1, need_off - s.off_ticks)}
            if kind == "shelly" and e["rw"] > 0 and not s.running:
                return {"c": "no_demand", "s": int(now - s.idle_since) if s.idle_since else 0}
            if s.last_on and now - s.last_on < min_on:
                return {"c": "running_min_on", "s": int(min_on - (now - s.last_on))}
            return {"c": "running", "s": int(now - s.last_on) if s.last_on else 0}
        if not self.window_open(kind, i):
            return {"c": "window", "h": e["ws"]}
        if self.daycap_reached(kind, i):
            return {"c": "daycap", "m": e["mx"]}
        if s.nd_retry and now < s.nd_retry:
            return {"c": "nd_pause", "s": int(s.nd_retry - now)}
        if self.batt_block:
            return {"c": "battery"}
        if s.last_off and now - s.last_off < min_off:
            return {"c": "min_off", "s": int(min_off - (now - s.last_off))}
        need = float(e["pw"]) + c["on_margin"]
        avail = self.last_available.get(kind, 0.0) if self.app.sl_ok else 0.0
        if s.on_ticks > 0 or avail >= need:
            return {"c": "switching_on", "n": max(1, need_on - s.on_ticks)}
        return {"c": "waiting", "need": round(need), "miss": round(max(0.0, need - avail))}

    # ── Day change ──────────────────────────────────────────────────────────
    def day_rollover(self, kind: str) -> None:
        yday = CLOCK.now().timetuple().tm_yday
        if yday == self._cur_yday[kind]:
            return
        if self._cur_yday[kind] == -1:
            # Container start: take the Shelly runtime from the stored day counters,
            # so the daily cap and the minimum runtime survive a restart.
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
        _LOGGER.info("[Runtime] New day – day counters (%s) reset", kind)

    # ── Weekly programs ─────────────────────────────────────────────────────
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
                    _LOGGER.info("[Program] %s – program %d started", self.display_name(kind, i), act + 1)
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
                    _LOGGER.info("[Program] %s – program ended", self.display_name(kind, i))
                    continue
                if blocked:
                    continue
                if not await self.set(kind, i, False, ER_SCHEDULE):
                    s.sched_retry = now + SCHED_RETRY_S
                    continue
                s.sched_on = False
                _LOGGER.info("[Program] %s – program ended", self.display_name(kind, i))

    # ── Timed overrides, daily cap, release window ──────────────────────────
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
                    if kind == "shelly" and not self.st[kind][i].virt:
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
                _LOGGER.info("[Timer] %s – timed override ended%s", self.display_name(kind, i),
                             ", back to automatic" if ret_auto else "")
                if ret_auto:
                    self.app.settings.save()
                    self.auto_eval_req = True

            if self.daycap_reached(kind, i):
                ovr = s.override_until != 0
                auto = self.enabled and (e["auto"] or s.forced_on)
                if (s.on and not ovr and (auto or s.sched_on)
                        and await self.set(kind, i, False, ER_DAY_CAP)):
                    s.forced_on = s.sched_on = s.auto_active = False
                    s.on_ticks = s.off_ticks = 0
                    _LOGGER.info("[Daily cap] %s – maximum runtime of %d min reached, off",
                                 self.display_name(kind, i), e["mx"])
                continue

            if self.window_open(kind, i):
                continue
            if not s.on or s.override_until or s.sched_on:
                continue
            if not self.enabled or (not e["auto"] and not s.forced_on):
                continue
            s.forced_on = s.auto_active = False
            s.on_ticks = s.off_ticks = 0
            await self.set(kind, i, False, ER_WINDOW)

    # ── Self-regulation ─────────────────────────────────────────────────────
    async def demand_tick(self) -> None:
        if not self.enabled:
            return
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
            if not s.on or not s.reachable or s.virt:
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
            _LOGGER.info("[Self-regulation] %s – no demand for %d min, plug off",
                         self.display_name("shelly", i), int((now - s.idle_since) / 60))
            s.forced_on = s.auto_active = False
            s.on_ticks = s.off_ticks = 0
            s.idle_since = 0.0
            s.nd_retry = now + ND_RETRY_S
            await self.set("shelly", i, False, ER_NO_DEMAND)

    # ── Battery guard ───────────────────────────────────────────────────────
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
            _LOGGER.info("[Battery] Discharging %.0f W → automatic %s", discharge,
                         "held back" if block else "released")

    # ── Surplus control ─────────────────────────────────────────────────────
    def _snapshot(self, kind: str) -> list[control.Load]:
        out = []
        now = CLOCK.mono()
        for i in range(self.count(kind)):
            e, s = self.entries(kind)[i], self.st[kind][i]
            freed = float(e["pw"])
            if kind == "shelly" and e["rw"] > 0 and s.reachable and not s.virt:
                freed = s.apower_w if s.apower_w > 0 else 0.0
            out.append(control.Load(
                kind=kind, idx=i, pri=e["pri"], auto=e["auto"], pw=float(e["pw"]), freed=freed,
                on=s.on, forced_on=s.forced_on, sched_on=s.sched_on, force_eval=s.force_eval,
                last_on=s.last_on, last_off=s.last_off, on_ticks=s.on_ticks, off_ticks=s.off_ticks,
                window_open=self.window_open(kind, i), daycap=self.daycap_reached(kind, i),
                nd_block=bool(s.nd_retry and now < s.nd_retry), auto_active=s.auto_active))
        return out

    async def auto_control(self, kind: str, surplus_w: float) -> float:
        c = self.cfg
        self.last_surplus[kind] = surplus_w
        p = control.params_from_cfg(c, hyst_on_ticks(c), hyst_off_ticks(c))
        loads = self._snapshot(kind)
        actions, available = control.plan_group(loads, surplus_w, self.batt_block, p, CLOCK.mono())
        for ld in loads:
            s = self.st[kind][ld.idx]
            s.on_ticks, s.off_ticks, s.force_eval = ld.on_ticks, ld.off_ticks, ld.force_eval
        for a in actions:
            await self.set(kind, a.idx, a.on, a.reason)
            self.st[kind][a.idx].auto_active = a.on
        self.last_available[kind] = available
        return available

    def dry_correction(self) -> float:
        """Dry run: how much more the house would draw if the virtual decisions were real."""
        delta = 0.0
        for kind in ("shelly", "ext"):
            for i in range(self.count(kind)):
                s = self.st[kind][i]
                if not s.virt or s.on == s.real_on:
                    continue
                e = self.entries(kind)[i]
                if s.on:
                    delta += float(e["pw"])
                elif kind == "shelly" and s.reachable:
                    delta -= s.apower_w
                else:
                    delta -= float(e["pw"])
        return delta

    async def distribute(self, surplus_w: float) -> None:
        if not self.enabled:
            return
        if self.dry:
            surplus_w -= self.dry_correction()
        remain = await self.auto_control("shelly", surplus_w)
        await self.auto_control("ext", remain)

    # ── Fail-safe ───────────────────────────────────────────────────────────
    async def failsafe_control(self, kind: str, src_age_s: float) -> None:
        if not self.enabled:
            return
        limit = self.cfg["sl_fsafe"] * 60
        if limit <= 0 or src_age_s < limit:
            if self.failsafe[kind]:
                self.failsafe[kind] = False
                if kind == "shelly":
                    _LOGGER.info("[Fail-safe] Lifted – automatic active again")
                    self.app.events.log(EV_FAILSAFE, -1, ER_NONE, False, None)
            return
        if self.failsafe[kind]:
            return
        self.failsafe[kind] = True
        if kind == "shelly":
            _LOGGER.warning("[Fail-safe] No readings for %ds – switching automatic loads off",
                            int(src_age_s))
            self.app.events.log(EV_FAILSAFE, -1, ER_NONE, True, f"seit {int(src_age_s // 60)} min")
        for i in range(self.count(kind)):
            e = self.entries(kind)[i]
            s = self.st[kind][i]
            if not e["auto"] or s.forced_on or s.sched_on or not s.on:
                continue
            await self.set(kind, i, False, ER_FAILSAFE)
            s.auto_active = False

    # ── Bad-weather catch-up ────────────────────────────────────────────────
    async def forced_runtime(self, kind: str) -> None:
        if not self.enabled:
            return
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
                _LOGGER.info("[Catch-up] %s – ended (%ds/%ds today)%s", self.display_name(kind, i),
                             s.today_on_s, quota, "" if autoc else " – automatic off")
        if not in_window:
            return
        for i in range(n):
            e = self.entries(kind)[i]
            if e["rt"] <= 0 or not e["auto"]:
                continue
            if not self.is_running(kind, i):
                continue
            if self.st[kind][i].today_on_s < self.quota_s(kind, i):
                return  # a load that still needs runtime is already running – one at a time
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
            _LOGGER.info("[Catch-up] %s – bad-weather catch-up ON (%ds left to the daily target)",
                         self.display_name(kind, pick), self.quota_s(kind, pick) - s.today_on_s)

    # ── Settings applied ────────────────────────────────────────────────────
    def settings_changed(self, orig: dict, new: dict) -> None:
        if orig.get("auto_en", True) != new.get("auto_en", True):
            self.enabled_changed(bool(new["auto_en"]))
        if bool(orig.get("dry_run")) != bool(new.get("dry_run")):
            self.dry_run_changed(bool(new.get("dry_run")))
        # Slots are compacted on save: follow each plug by its address and device id,
        # so a plug keeps its state and counters when one before it is removed.
        old_sh = [(e["ip"], e["id"]) for e in orig["shelly"]]
        moved: dict[int, int] = {}
        for i in range(MAX_SHELLY):
            n = new["shelly"][i]
            if not n["ip"]:
                continue
            for j, key in enumerate(old_sh):
                if key == (n["ip"], n["id"]) and j not in moved.values():
                    moved[i] = j
                    break
        old_states = list(self.st["shelly"])
        self.app.energy.remap_shelly(moved)
        for i in range(MAX_SHELLY):
            n = new["shelly"][i]
            if i in moved:
                j = moved[i]
                s = old_states[j]
                self.st["shelly"][i] = s
                o = orig["shelly"][j]
                if o["sch"] != n["sch"]:
                    s.sched_skip = -1
                if o["rw"] != n["rw"]:
                    s.running = False
                    s.idle_since = 0.0
                    s.nd_retry = 0.0
            else:
                self.st["shelly"][i] = DevState()
        for i in range(MAX_EXT):
            o, n = orig["ext"][i], new["ext"][i]
            if o["name"] != n["name"]:
                self.st["ext"][i] = DevState()
            elif o["sch"] != n["sch"]:
                self.st["ext"][i].sched_skip = -1
