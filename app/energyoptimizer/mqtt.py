"""MQTT mit Home-Assistant-Discovery.

Themen:
  <prefix>/status                      online/offline (Last Will)
  <prefix>/state                       Hauptschalter, geschaltete Last, Sperren (JSON)
  <prefix>/auto/set                    Hauptschalter (ON/OFF)
  <prefix>/solar/state                 Leistungen (JSON)
  <prefix>/energy/state                Zählerstände (JSON, minütlich)
  <prefix>/shelly/<i>/state            Zustand je Steckdose
  <prefix>/shelly/<i>/set, …/auto/set  Befehle (ON/OFF)
  <prefix>/ext/<i>/set                 Wunschzustand externer Schalter (ON/OFF)
  <prefix>/ext/<i>/state, …/auto/set

Die Entitäten entsprechen der Home-Assistant-Integration ha-energyoptimizer:
ein Gerät für die Anlage (verfügbarer Überschuss, geschaltete Last,
Hauptschalter, veraltete Messwerte, Batterie-Vorrang) und ein Gerät je Last
(Status, Laufzeit heute, vom Optimizer eingeschaltet, Automatik).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING

from . import __version__
from .clock import CLOCK
from .const import ER_MQTT, MAX_EXT, MAX_SHELLY
from .i18n import tr

if TYPE_CHECKING:
    from .app import EnergyOptimizer

_LOGGER = logging.getLogger(__name__)

try:
    import paho.mqtt.client as paho
except ImportError:  # pragma: no cover
    paho = None


# Values of the load status sensor, as in ha-energyoptimizer, plus "schedule"
# for the weekly programs this app has.
LOAD_STATUS = ("off", "waiting", "surplus", "catchup", "manual", "blocked", "disabled",
               "external", "schedule")
STATUS_TXT = {
    "off": "Aus", "waiting": "Wartet auf Überschuss", "surplus": "Läuft auf Überschuss",
    "catchup": "Nachlauf", "manual": "Manuell übersteuert", "blocked": "Gesperrt",
    "disabled": "Automatik aus", "external": "Von Hand eingeschaltet", "schedule": "Wochenprogramm",
}
STATE_REFRESH_S = 2.0


def _device_id() -> str:
    mac = uuid.getnode()
    return "eo%06x" % (mac & 0xFFFFFF)


class Mqtt:
    def __init__(self, app: "EnergyOptimizer") -> None:
        self.app = app
        self.client = None
        self.connected = False
        self.dev_id = _device_id()
        self.prefix = "energyoptimizer"
        self._key: tuple | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.solar_dirty = False
        self.shelly_dirty = 0
        self.ext_dirty = 0
        self._last_energy = 0.0
        self._last_counts = (0, 0)
        self._ext_echo: dict[int, tuple[str, float]] = {}
        self._on_connect_pending = False
        self._sent: dict[str, str] = {}
        self._disc_sig: tuple | None = None
        self._last_refresh = 0.0

    # ── Markierungen aus der Steuerung ──────────────────────────────────────
    def mark_shelly_dirty(self, i: int) -> None:
        if 0 <= i < MAX_SHELLY:
            self.shelly_dirty |= 1 << i

    def mark_ext_dirty(self, i: int) -> None:
        if 0 <= i < MAX_EXT:
            self.ext_dirty |= 1 << i

    def apply_settings(self) -> None:
        self._key = None  # erzwingt Neuverbindung mit den neuen Einstellungen

    # ── Verbindung ──────────────────────────────────────────────────────────
    def _stop(self) -> None:
        if self.client is not None:
            try:
                self.client.publish(f"{self.prefix}/status", "offline", retain=True)
                self.client.disconnect()
                self.client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
        self.client = None
        self.connected = False

    def _start(self, c: dict) -> None:
        if paho is None:
            return
        self.prefix = c["mq_pfx"] or "energyoptimizer"
        cl = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=self.dev_id)
        if c["mq_user"]:
            cl.username_pw_set(c["mq_user"], c["mq_pass"] or None)
        cl.will_set(f"{self.prefix}/status", "offline", retain=True)
        cl.reconnect_delay_set(5, 60)
        cl.on_connect = self._on_connect
        cl.on_disconnect = self._on_disconnect
        cl.on_message = self._on_message
        self.client = cl
        try:
            cl.connect_async(c["mq_host"], int(c["mq_port"]), keepalive=30)
            cl.loop_start()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("[MQTT] Verbindung zu %s fehlgeschlagen: %s", c["mq_host"], err)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            _LOGGER.warning("[MQTT] Verbindung abgelehnt: %s", reason_code)
            return
        if self._loop:
            self._loop.call_soon_threadsafe(self._connected)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected = False

    def _on_message(self, client, userdata, msg) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._handle, msg.topic, msg.payload)

    def _connected(self) -> None:
        c = self.app.settings.cfg
        cl = self.client
        if cl is None:
            return
        self.connected = True
        self._sent.clear()
        _LOGGER.info("[MQTT] Verbunden mit %s:%s", c["mq_host"], c["mq_port"])
        cl.publish(f"{self.prefix}/status", "online", retain=True)
        cl.subscribe(f"{self.prefix}/auto/set")
        for i in range(c["sh_count"]):
            cl.subscribe(f"{self.prefix}/shelly/{i}/set")
            cl.subscribe(f"{self.prefix}/shelly/{i}/auto/set")
        for i in range(c["ex_count"]):
            cl.subscribe(f"{self.prefix}/ext/{i}/set")
            cl.subscribe(f"{self.prefix}/ext/{i}/auto/set")
        self._disc_sig = None
        self._last_counts = (c["sh_count"], c["ex_count"])
        self.solar_dirty = True
        self.shelly_dirty = 0xFF
        self.ext_dirty = 0xFF
        self._last_energy = 0.0

    def _handle(self, topic: str, payload: bytes) -> None:
        val = payload[:15].decode(errors="replace").strip().upper()
        on = val in ("ON", "1", "TRUE")
        if topic == f"{self.prefix}/auto/set":
            self.app.devices.set_enabled(on, ER_MQTT)
            return
        for kind, base in (("shelly", f"{self.prefix}/shelly/"), ("ext", f"{self.prefix}/ext/")):
            if not topic.startswith(base):
                continue
            rest = topic[len(base):]
            if "/" not in rest:
                return
            idx_s, action = rest.split("/", 1)
            try:
                idx = int(idx_s)
            except ValueError:
                return
            if kind == "ext" and action == "set":
                # Den eigenen Wunschzustand, den wir gerade auf dieses Thema
                # geschrieben haben, nicht als Handbefehl zurücklesen.
                echo = self._ext_echo.get(idx)
                if echo and echo[0] == val and CLOCK.mono() - echo[1] < 5:
                    return
            if action == "set":
                self.app.devices.apply_command(kind, idx, "on" if on else "off", ER_MQTT)
            elif action == "auto/set":
                self.app.devices.apply_command(kind, idx, "autoon" if on else "autooff", ER_MQTT)
            return

    # ── Veröffentlichen ─────────────────────────────────────────────────────
    def _pub(self, topic: str, payload, retain: bool = True) -> None:
        if self.client is not None:
            if not isinstance(payload, str):
                payload = json.dumps(payload, ensure_ascii=False)
            # Only publish what changed; the broker keeps the retained value.
            if self._sent.get(topic) == payload:
                return
            self._sent[topic] = payload
            self.client.publish(topic, payload, retain=retain)

    def _publish_solar(self) -> None:
        sd = self.app.history.live
        doc = {"prod": sd.production_w, "cons": sd.consumption_w, "grid": sd.grid_w,
               "surplus": sd.surplus_w}
        if sd.has_battery:
            doc["batt"] = sd.battery_w
            doc["soc"] = sd.battery_soc
        self._pub(f"{self.prefix}/solar/state", doc)

    def _publish_energy(self) -> None:
        en = self.app.energy.c
        self._pub(f"{self.prefix}/energy/state", {
            "day_prod_kwh": en["day_prod_wh"] / 1000, "day_cons_kwh": en["day_cons_wh"] / 1000,
            "day_grid_in_kwh": en["day_grid_in_wh"] / 1000, "day_grid_out_kwh": en["day_grid_out_wh"] / 1000,
            "total_prod_kwh": en["total_prod_wh"] / 1000, "total_cons_kwh": en["total_cons_wh"] / 1000,
            "total_grid_in_kwh": en["total_grid_in_wh"] / 1000,
            "total_grid_out_kwh": en["total_grid_out_wh"] / 1000,
        })

    def _stale(self) -> bool:
        app, dv = self.app, self.app.devices
        if dv.failsafe_active() or not app.last_sl_ok:
            return True
        return CLOCK.mono() - app.last_sl_ok > max(180, app.settings.cfg["sl_poll_min"] * 120)

    def _publish_hub(self) -> None:
        dv = self.app.devices
        self._pub(f"{self.prefix}/state", {
            "enabled": dv.enabled, "managed": round(dv.managed_power()),
            "stale": self._stale(), "batt_hold": dv.batt_block,
        })

    def _load_doc(self, kind: str, i: int, c: dict) -> dict:
        dv = self.app.devices
        s = dv.st[kind][i]
        status = dv.load_status(kind, i)
        return {"on": s.on, "auto": c[kind][i]["auto"], "status": status,
                "status_text": tr(STATUS_TXT[status], c.get("lang", "de")),
                "rt_today": s.today_on_s // 60, "managed": dv.managed(kind, i)}

    def _publish_shelly(self, i: int, c: dict) -> None:
        if i >= c["sh_count"]:
            return
        s = self.app.devices.st["shelly"][i]
        en = self.app.energy.c
        doc = self._load_doc("shelly", i, c)
        doc.update({"reach": s.reachable, "apower": s.apower_w})
        if c["shelly"][i]["rw"] > 0:
            doc["running"] = s.on and s.reachable and s.running
        doc["e_day"] = en["day_shelly_wh"][i] / 1000
        doc["e_tot"] = en["total_shelly_wh"][i] / 1000
        self._pub(f"{self.prefix}/shelly/{i}/state", doc)

    def _publish_ext(self, i: int, c: dict) -> None:
        if i >= c["ex_count"]:
            return
        s = self.app.devices.st["ext"][i]
        val = "ON" if s.on else "OFF"
        if self._sent.get(f"{self.prefix}/ext/{i}/set") != val:
            self._ext_echo[i] = (val, CLOCK.mono())
        self._pub(f"{self.prefix}/ext/{i}/set", val, retain=False)
        self._pub(f"{self.prefix}/ext/{i}/state", self._load_doc("ext", i, c))

    # ── Home-Assistant-Discovery ────────────────────────────────────────────
    def _hub_device(self) -> dict:
        return {"ids": [self.dev_id], "name": "EnergyOptimizer", "mf": "EnergyOptimizer",
                "mdl": "Docker", "sw": __version__}

    def _load_device(self, kind: str, i: int, name: str) -> dict:
        return {"ids": [f"{self.dev_id}_{kind}_{i}"], "name": name, "via_device": self.dev_id,
                "mf": "Shelly" if kind == "shelly" else "EnergyOptimizer",
                "mdl": self._t("Steckdose") if kind == "shelly" else self._t("Externer Schalter")}

    def _t(self, text: str) -> str:
        return tr(text, self.app.settings.cfg.get("lang", "de"))

    def _ha(self, component: str, object_id: str, payload: dict, dev: dict | None = None) -> None:
        payload["uniq_id"] = f"{self.dev_id}_{object_id}"
        payload["obj_id"] = f"energyoptimizer_{object_id}"
        payload["avty_t"] = f"{self.prefix}/status"
        payload["dev"] = dev or self._hub_device()
        self._pub(f"homeassistant/{component}/{self.dev_id}/{object_id}/config", payload)

    def _power(self, oid: str, name: str | None, st: str, fld: str, dev: dict | None = None) -> None:
        self._ha("sensor", oid, {"name": name, "stat_t": st, "val_tpl": f"{{{{ value_json.{fld} }}}}",
                                 "unit_of_meas": "W", "dev_cla": "power", "stat_cla": "measurement"}, dev)

    def _energy(self, oid: str, name: str, st: str, fld: str, dev: dict | None = None) -> None:
        self._ha("sensor", oid, {"name": name, "stat_t": st, "val_tpl": f"{{{{ value_json.{fld} }}}}",
                                 "unit_of_meas": "kWh", "dev_cla": "energy",
                                 "stat_cla": "total_increasing"}, dev)

    def _flag(self, comp: str, oid: str, name: str, st: str, fld: str, dev: dict | None = None,
              **extra) -> None:
        self._ha(comp, oid, {"name": name, "stat_t": st,
                             "val_tpl": f"{{{{ 'ON' if value_json.{fld} else 'OFF' }}}}", **extra}, dev)

    def _load_entities(self, kind: str, i: int, name: str, dev: dict) -> None:
        """Status, runtime, managed flag and automatic switch, as in ha-energyoptimizer."""
        p, t = self.prefix, self._t
        st = f"{p}/{kind}/{i}/state"
        oid = f"{kind}_{i}"
        self._flag("switch", f"{oid}_switch", None, st, "on", dev, cmd_t=f"{p}/{kind}/{i}/set")
        self._flag("switch", f"{oid}_auto", t("Automatik"), st, "auto", dev,
                   cmd_t=f"{p}/{kind}/{i}/auto/set", ent_cat="config")
        self._ha("sensor", f"{oid}_status", {
            "name": t("Status"), "stat_t": st, "val_tpl": "{{ value_json.status }}",
            "dev_cla": "enum", "ops": list(LOAD_STATUS),
            "json_attr_t": st, "json_attr_tpl": "{{ {'text': value_json.status_text} | tojson }}",
        }, dev)
        self._ha("sensor", f"{oid}_runtime", {
            "name": t("Laufzeit heute"), "stat_t": st, "val_tpl": "{{ value_json.rt_today }}",
            "unit_of_meas": "min", "dev_cla": "duration", "stat_cla": "total", "ent_cat": "diagnostic",
        }, dev)
        self._flag("binary_sensor", f"{oid}_managed", t("Vom Optimizer eingeschaltet"), st, "managed", dev)

    def _discovery_sig(self, c: dict) -> tuple:
        return (c.get("lang"), c["sh_count"], c["ex_count"], bool(c["sl_fsoc"] or c["sl_fbatt"]),
                tuple((e["name"], e["ip"], e["rw"] > 0) for e in c["shelly"][: c["sh_count"]]),
                tuple(e["name"] for e in c["ext"][: c["ex_count"]]))

    def _publish_discovery(self, c: dict) -> None:
        p, t = self.prefix, self._t
        solar, energy, hub = f"{p}/solar/state", f"{p}/energy/state", f"{p}/state"
        self._power("production", t("Produktion"), solar, "prod")
        self._power("consumption", t("Verbrauch"), solar, "cons")
        self._power("grid", t("Netz"), solar, "grid")
        self._power("surplus", t("Verfügbarer Überschuss"), solar, "surplus")
        self._power("managed_power", t("Geschaltete Last"), hub, "managed")
        self._flag("switch", "automation", "Optimizer", hub, "enabled", cmd_t=f"{p}/auto/set",
                   ent_cat="config")
        self._flag("binary_sensor", "source_stale", t("Messwerte veraltet"), hub, "stale",
                   dev_cla="problem", ent_cat="diagnostic")
        self._flag("binary_sensor", "battery_hold", t("Batterie hat Vorrang"), hub, "batt_hold",
                   ent_cat="diagnostic")
        if c["sl_fsoc"] or c["sl_fbatt"]:
            self._power("battery_power", t("Batterie"), solar, "batt")
            self._ha("sensor", "battery_soc", {"name": t("Batterie-Ladestand"), "stat_t": solar,
                                               "val_tpl": "{{ value_json.soc }}", "unit_of_meas": "%",
                                               "dev_cla": "battery", "stat_cla": "measurement"})
        self._energy("total_produced", t("Gesamt Produktion"), energy, "total_prod_kwh")
        self._energy("total_consumed", t("Gesamt Verbrauch"), energy, "total_cons_kwh")
        self._energy("total_grid_in", t("Gesamt Netzbezug"), energy, "total_grid_in_kwh")
        self._energy("total_grid_out", t("Gesamt Einspeisung"), energy, "total_grid_out_kwh")
        for i in range(c["sh_count"]):
            e = c["shelly"][i]
            st = f"{p}/shelly/{i}/state"
            dev = self._load_device("shelly", i, e["name"] or e["ip"])
            self._load_entities("shelly", i, e["name"] or e["ip"], dev)
            self._power(f"shelly_{i}_power", t("Leistung"), st, "apower", dev)
            self._energy(f"shelly_{i}_energy", t("Energie"), st, "e_tot", dev)
            self._flag("binary_sensor", f"shelly_{i}_conn", t("Erreichbar"), st, "reach", dev,
                       dev_cla="connectivity", ent_cat="diagnostic")
            if e["rw"] > 0:
                self._flag("binary_sensor", f"shelly_{i}_run", t("Gerät läuft"), st, "running", dev,
                           dev_cla="running")
            else:
                self._pub(f"homeassistant/binary_sensor/{self.dev_id}/shelly_{i}_run/config", "")
        for i in range(c["ex_count"]):
            name = c["ext"][i]["name"] or t("Extern")
            self._load_entities("ext", i, name, self._load_device("ext", i, name))

    def _cleanup_removed(self, c: dict) -> None:
        old_sh, old_ex = self._last_counts
        load = (("switch", "switch"), ("auto", "switch"), ("status", "sensor"), ("runtime", "sensor"),
                ("managed", "binary_sensor"))
        comps = load + (("power", "sensor"), ("energy", "sensor"), ("conn", "binary_sensor"),
                        ("run", "binary_sensor"))
        for i in range(c["sh_count"], min(old_sh, MAX_SHELLY)):
            for suffix, comp in comps:
                self._pub(f"homeassistant/{comp}/{self.dev_id}/shelly_{i}_{suffix}/config", "")
            self._pub(f"{self.prefix}/shelly/{i}/state", "")
        for i in range(c["ex_count"], min(old_ex, MAX_EXT)):
            for suffix, comp in load:
                self._pub(f"homeassistant/{comp}/{self.dev_id}/ext_{i}_{suffix}/config", "")
            self._pub(f"{self.prefix}/ext/{i}/state", "")
        self._last_counts = (c["sh_count"], c["ex_count"])

    # ── Hauptschleife ───────────────────────────────────────────────────────
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            while True:
                c = self.app.settings.cfg
                want = (bool(c["mq_en"] and c["mq_host"]), c["mq_host"], c["mq_port"], c["mq_user"],
                        c["mq_pass"], c["mq_pfx"], c["mq_disc"])
                if want != self._key:
                    self._stop()
                    self._key = want
                    if want[0]:
                        self._start(c)
                if self.client is not None and self.connected:
                    if self._last_counts != (c["sh_count"], c["ex_count"]):
                        self._cleanup_removed(c)
                    if c["mq_disc"]:
                        sig = self._discovery_sig(c)
                        if sig != self._disc_sig:
                            self._disc_sig = sig
                            self._publish_discovery(c)
                    if self.solar_dirty:
                        self.solar_dirty = False
                        self._publish_solar()
                    for i in range(MAX_SHELLY):
                        if self.shelly_dirty & (1 << i):
                            self.shelly_dirty &= ~(1 << i)
                            self._publish_shelly(i, c)
                    for i in range(MAX_EXT):
                        if self.ext_dirty & (1 << i):
                            self.ext_dirty &= ~(1 << i)
                            self._publish_ext(i, c)
                    now = CLOCK.mono()
                    if now - self._last_refresh >= STATE_REFRESH_S:
                        # Status and runtime change without a switch command; the
                        # comparison in _pub keeps unchanged values off the network.
                        self._last_refresh = now
                        self._publish_hub()
                        for i in range(c["sh_count"]):
                            self._publish_shelly(i, c)
                        for i in range(c["ex_count"]):
                            self._publish_ext(i, c)
                    if now - self._last_energy >= 60:
                        self._last_energy = now
                        self._publish_energy()
                await asyncio.sleep(0.2)
        finally:
            self._stop()
