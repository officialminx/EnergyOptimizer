"""Hauptschleife und Zusammenbau aller Teile."""

from __future__ import annotations

import asyncio
import logging
import os

import aiohttp

from .alarms import Alarms
from .clock import CLOCK
from .const import (
    EV_BOOT, EV_CONFIG, EV_SOLARLOG, ER_MANUAL, ER_NONE, RST_CONTAINER, SHELLY_POLL_S,
    SL_DEV_POLL_S, SL_MAX_DEV, SL_RETRY_S, HISTORY_SAMPLE_S,
)
from .devices import Devices
from .energy import Energy
from .events import EventLog
from .history import History
from .ideas import Ideas
from .mdns import Mdns
from .mqtt import Mqtt
from .heartbeat import Heartbeat
from .settings import SettingsStore
from .solarlog import SolarData, SolarDevices, SolarLogReader
from .storage import load_json, save_json
from .sysinfo import SysInfo
from .updates import UpdateCheck

_LOGGER = logging.getLogger(__name__)

ST_OK, ST_WARN, ST_FAIL, ST_SKIP = 0, 1, 2, 3

# Liegt diese Datei im Datenordner, wird das Web-Passwort gelöscht und beim nächsten
# Aufruf der Oberfläche neu festgelegt (python -m energyoptimizer reset-password).
RESET_FLAG = "RESET_PASSWORD"
MDNS_CHECK_S = 60


class EnergyOptimizer:
    def __init__(self, data_dir: str, port: int = 80) -> None:
        self.data_dir = data_dir
        self.port = port
        os.makedirs(data_dir, exist_ok=True)
        self.settings = SettingsStore(data_dir)
        self.events = EventLog(data_dir)
        self.history = History(data_dir)
        self.energy = Energy(data_dir, self.history.daily_append)
        self.ideas = Ideas(data_dir)
        self.devices = Devices(self)
        self.alarms = Alarms(self)
        self.heartbeat = Heartbeat(self)
        self.mqtt = Mqtt(self)
        self.sysinfo = SysInfo(data_dir)
        self.mdns = Mdns(port)
        self.updates = UpdateCheck()
        self.session: aiohttp.ClientSession = None  # type: ignore[assignment]
        self.reader: SolarLogReader = None  # type: ignore[assignment]
        self.solar = SolarData()
        self.sl_ok = False
        self.last_sl_ok = 0.0
        self.task_start = CLOCK.mono()
        self.next_solar = 0.0
        self.solar_refresh_req = False
        self.devs = SolarDevices()
        self.devs_t = 0.0
        self.boots = 0
        self.selftest = {"running": False, "done_t": 0.0, "items": []}
        self._tasks: list[asyncio.Task] = []
        self._selftest_req = False
        self.mdns_req = True

    # ── Start / Stopp ───────────────────────────────────────────────────────
    async def start(self) -> None:
        self.settings.load()
        self.check_password_reset()
        self.events.load()
        self.energy.load()
        self.history.load()
        self.ideas.load()
        meta_path = os.path.join(self.data_dir, "meta.json")
        meta = load_json(meta_path, {})
        self.boots = int(meta.get("boots", 0)) + 1
        save_json(meta_path, {"boots": self.boots})
        # Eigener Cookie-Jar aus: das Solar-Log-Cookie wird ausdrücklich als Header
        # gesetzt (wie in ha-advanced-solarlog), sonst nichts.
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
        self.reader = SolarLogReader(self.session)
        self.events.log(EV_BOOT, -1, RST_CONTAINER, False, f"Start #{self.boots}")
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self.network_task(), name="network"),
            loop.create_task(self.heartbeat.run(), name="heartbeat"),
            loop.create_task(self.mqtt.run(), name="mqtt"),
            loop.create_task(self.update_task(), name="updates"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.flush()
        if self.session:
            await self.session.close()
        await self.mdns.close()

    def flush(self) -> None:
        self.energy.save()
        self.events.flush()

    async def update_task(self) -> None:
        # Eigene Aufgabe: eine langsame GitHub-Antwort darf die Regelung nie aufhalten.
        while True:
            if self.updates.due():
                await self.updates.check(self.session)
            await asyncio.sleep(30)

    # ── Web-Passwort ────────────────────────────────────────────────────────
    def set_web_password(self, pw: str) -> None:
        self.settings.cfg["web_pass"] = pw
        self.settings.save()
        self.events.log(EV_CONFIG, -1, ER_MANUAL, True, "Web-Passwort festgelegt")
        _LOGGER.info("[Auth] Web-Passwort festgelegt")

    def check_password_reset(self) -> None:
        path = os.path.join(self.data_dir, RESET_FLAG)
        if not os.path.exists(path):
            return
        try:
            os.remove(path)
        except OSError as err:
            _LOGGER.error("[Auth] %s konnte nicht gelöscht werden: %s", path, err)
        if not self.settings.cfg["web_pass"]:
            return
        self.settings.cfg["web_pass"] = ""
        self.settings.save()
        self.events.log(EV_CONFIG, -1, ER_MANUAL, True, "Web-Passwort zurückgesetzt")
        _LOGGER.warning("[Auth] Web-Passwort zurückgesetzt – beim nächsten Aufruf neu festlegen")

    # ── Einstellungen übernehmen ────────────────────────────────────────────
    def apply_settings(self, doc: dict, text: str | None = None) -> str:
        orig, new, warn = self.settings.apply(doc)
        self.devices.settings_changed(orig, new)
        self.mqtt.apply_settings()
        self.events.log(EV_CONFIG, -1, ER_MANUAL, True, text)
        self.solar_refresh_req = True
        self.devices.poll_req = True
        self.devices.auto_eval_req = True
        self.mdns_req = True
        return warn

    # ── Hauptschleife ───────────────────────────────────────────────────────
    async def network_task(self) -> None:
        last_shelly = 0.0
        last_tick = 0.0
        last_hist_slot = -1
        last_devs = 0.0
        last_mdns = 0.0
        sl_was_ok = True
        sl_ever_ok = False
        while True:
            try:
                now = CLOCK.mono()
                cfg = self.settings.cfg
                dv = self.devices

                if dv.auto_eval_req:
                    dv.auto_eval_req = False
                    if self.sl_ok:
                        dv.batt_update(self.solar.has_battery, self.solar.battery_w)
                        await dv.distribute(self.solar.surplus_w)

                force_solar = self.solar_refresh_req
                self.solar_refresh_req = False
                if force_solar or now >= self.next_solar:
                    sd = await self.reader.fetch(cfg)
                    ok = sd is not None
                    self.next_solar = CLOCK.mono() + (cfg["sl_poll_min"] * 60 if ok else SL_RETRY_S)
                    self.sl_ok = ok
                    if ok:
                        self.solar = sd
                        sl_ever_ok = True
                    if sl_ever_ok and ok != sl_was_ok:
                        sl_was_ok = ok
                        self.events.log(EV_SOLARLOG, -1, ER_NONE, ok, cfg["sl_ip"])
                    if ok:
                        self.last_sl_ok = now
                        self.history.set_solar(sd)
                        self.energy.add_solar(sd, cfg["sl_poll_min"] * 60)
                        self.mqtt.solar_dirty = True
                        dv.batt_update(sd.has_battery, sd.battery_w)
                        await dv.distribute(sd.surplus_w)

                slot = int(CLOCK.time() // HISTORY_SAMPLE_S)
                if slot != last_hist_slot:
                    if self.history.record(dv.st["shelly"], dv.self_regulated,
                                           lambda i: dv.is_running("shelly", i)):
                        last_hist_slot = slot

                if dv.poll_req or not last_shelly or now - last_shelly >= SHELLY_POLL_S:
                    dv.poll_req = False
                    last_shelly = now
                    await dv.poll_all()
                    dv.recover_ips()
                    self.history.accum_shelly(dv.st["shelly"])

                if cfg["sl_dev"] and (not last_devs or now - last_devs >= SL_DEV_POLL_S):
                    last_devs = now
                    res = await self.reader.fetch_devices(cfg, self.devs)
                    if res is not None:
                        self.devs = res
                        for _ in range(SL_MAX_DEV):
                            if not await self.reader.fetch_one_name(cfg, self.devs):
                                break
                    self.devs_t = CLOCK.mono()

                if now - last_tick >= 5:
                    last_tick = now
                    for kind in ("shelly", "ext"):
                        dv.day_rollover(kind)
                    await dv.sched_tick("shelly")
                    await dv.schedule_tick("shelly")
                    await dv.demand_tick()
                    await dv.forced_runtime("shelly")
                    await dv.sched_tick("ext")
                    await dv.schedule_tick("ext")
                    await dv.forced_runtime("ext")
                    sl_age = (now - self.last_sl_ok) if self.last_sl_ok else (now - self.task_start)
                    await dv.failsafe_control("shelly", sl_age)
                    await dv.failsafe_control("ext", sl_age)
                    self.energy.tick()
                    self.alarms.tick(sl_age, self.solar, self.sl_ok, self.devs)
                    self.sysinfo.sample()
                    self.check_password_reset()
                    self.events.flush()

                if self.mdns_req or now - last_mdns >= MDNS_CHECK_S:
                    self.mdns_req = False
                    last_mdns = now
                    await self.mdns.update(cfg["hostname"])

                if self._selftest_req:
                    self._selftest_req = False
                    await self.run_selftest()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 – die Schleife darf nie stehen bleiben
                _LOGGER.exception("Fehler in der Hauptschleife")
            await asyncio.sleep(0.5)

    # ── Selbsttest ──────────────────────────────────────────────────────────
    def request_selftest(self) -> None:
        self._selftest_req = True

    async def run_selftest(self) -> None:
        st = self.selftest
        st["running"] = True
        items: list[dict] = []

        def add(name: str, state: int, detail: str) -> None:
            items.append({"n": name[:33], "s": state, "d": detail[:79]})

        cfg = self.settings.cfg
        add("Netzwerk", ST_OK, "Container läuft")
        add("Uhrzeit", ST_OK, CLOCK.now().strftime("%d.%m.%Y %H:%M"))
        sd = await self.reader.fetch(cfg)
        if sd is None:
            add("SolarLog", ST_FAIL, f"{cfg['sl_ip']}:{cfg['sl_port']} antwortet nicht"
                + (f" ({self.reader.last_error[:40]})" if self.reader.last_error else ""))
        elif sd.production_w == 0 and sd.consumption_w == 0:
            add("SolarLog", ST_WARN, "Erreichbar, aber Produktion und Verbrauch sind 0 – Feldnummern prüfen")
        else:
            add("SolarLog", ST_OK, f"{cfg['sl_ip']}: {sd.production_w:.0f} W Produktion, "
                f"{sd.consumption_w:.0f} W Verbrauch")
        dv = self.devices
        for i in range(cfg["sh_count"]):
            e = cfg["shelly"][i]
            if not e["ip"]:
                continue
            nm = f"Steckdose {i + 1}: {e['name'] or e['ip']}"
            if not await dv.get_status(i):
                add(nm, ST_FAIL, f"{e['ip']} antwortet nicht")
                continue
            s = dv.st["shelly"][i]
            onoff = "EIN" if s.on else "AUS"
            if e["rw"] > 0:
                add(nm, ST_OK, f"Erreichbar, {onoff}, {s.apower_w:.0f} W ("
                    + ("Gerät läuft" if s.on and s.apower_w >= e["rw"] else "Eigenregelung: kein Bedarf") + ")")
            elif s.on and s.apower_w < 1:
                add(nm, ST_WARN, "Erreichbar und EIN, zieht aber 0 W – Verbraucher oder Sicherung prüfen")
            else:
                add(nm, ST_OK, f"Erreichbar, {onoff}, {s.apower_w:.0f} W")
        stats = self.history.stats()
        add("Datenverzeichnis", ST_OK, f"{stats['fs_used'] // 1048576} von {stats['fs_total'] // 1048576} MB belegt")
        if self.settings.save_error:
            add("Einstellungsspeicher", ST_FAIL, "Ein Schreibvorgang ist fehlgeschlagen")
        else:
            add("Einstellungsspeicher", ST_OK, "In Ordnung")
        if not cfg["mq_en"]:
            add("MQTT", ST_SKIP, "Nicht aktiviert")
        elif self.mqtt.connected:
            add("MQTT", ST_OK, f"Verbunden mit {cfg['mq_host']}:{cfg['mq_port']}")
        else:
            add("MQTT", ST_FAIL, f"Aktiviert, aber keine Verbindung zu {cfg['mq_host']}:{cfg['mq_port']}")
        ns = self.heartbeat.state()
        if not cfg["hb_en"]:
            add("Heartbeat", ST_SKIP, "Nicht aktiviert")
        elif ns["hb_age"] < 0:
            add("Heartbeat", ST_WARN, "Noch kein Lebenszeichen gesendet")
        elif ns["hb_ok"]:
            add("Heartbeat", ST_OK, f"Letztes Lebenszeichen vor {ns['hb_age']} s")
        else:
            add("Heartbeat", ST_FAIL, "Letzter Ping fehlgeschlagen")
        al = self.alarms.active_count()
        add("Alarmzentrale", ST_OK if al == 0 else ST_WARN,
            "Keine offenen Störungen" if al == 0 else f"{al} offene Störung(en) – siehe Dashboard")
        st["items"] = items
        st["done_t"] = CLOCK.mono()
        st["running"] = False
        self.events.log(EV_CONFIG, -1, ER_MANUAL, True, "Selbsttest")

    def selftest_json(self) -> dict:
        st = self.selftest
        done = bool(st["done_t"])
        return {"running": st["running"], "done": done,
                "age": int(CLOCK.mono() - st["done_t"]) if done else -1,
                "items": [] if st["running"] else st["items"]}
