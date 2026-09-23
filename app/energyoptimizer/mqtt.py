"""MQTT mit Home-Assistant-Discovery.

Themen:
  <prefix>/status                      online/offline (Last Will)
  <prefix>/solar/state                 Leistungen (JSON)
  <prefix>/energy/state                Zählerstände (JSON, minütlich)
  <prefix>/shelly/<i>/state            Zustand je Steckdose
  <prefix>/shelly/<i>/set, …/auto/set  Befehle (ON/OFF)
  <prefix>/ext/<i>/set                 Wunschzustand externer Schalter (ON/OFF)
  <prefix>/ext/<i>/state, …/auto/set
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING

from .clock import CLOCK
from .const import ER_MQTT, MAX_EXT, MAX_SHELLY

if TYPE_CHECKING:
    from .app import EnergyOptimizer

_LOGGER = logging.getLogger(__name__)

try:
    import paho.mqtt.client as paho
except ImportError:  # pragma: no cover
    paho = None


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
        for i in range(c["sh_count"]):
            cl.subscribe(f"{self.prefix}/shelly/{i}/set")
            cl.subscribe(f"{self.prefix}/shelly/{i}/auto/set")
        for i in range(c["ex_count"]):
            cl.subscribe(f"{self.prefix}/ext/{i}/set")
            cl.subscribe(f"{self.prefix}/ext/{i}/auto/set")
        if c["mq_disc"]:
            self._publish_discovery(c)
        self._last_counts = (c["sh_count"], c["ex_count"])
        self.solar_dirty = True
        self.shelly_dirty = 0xFF
        self.ext_dirty = 0xFF
        self._last_energy = 0.0

    def _handle(self, topic: str, payload: bytes) -> None:
        val = payload[:15].decode(errors="replace").strip().upper()
        on = val in ("ON", "1", "TRUE")
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

    def _publish_shelly(self, i: int, c: dict) -> None:
        if i >= c["sh_count"]:
            return
        s = self.app.devices.st["shelly"][i]
        en = self.app.energy.c
        doc = {"on": s.on, "auto": c["shelly"][i]["auto"], "reach": s.reachable, "apower": s.apower_w}
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
        self._ext_echo[i] = (val, CLOCK.mono())
        self._pub(f"{self.prefix}/ext/{i}/set", val, retain=False)
        self._pub(f"{self.prefix}/ext/{i}/state", {"on": s.on, "auto": c["ext"][i]["auto"]})

    def _ha(self, component: str, object_id: str, payload: dict) -> None:
        payload["uniq_id"] = f"{self.dev_id}_{object_id}"
        payload["avty_t"] = f"{self.prefix}/status"
        payload["dev"] = {"ids": [self.dev_id], "name": "EnergyOptimizer", "mf": "DIY",
                          "mdl": "Docker"}
        self._pub(f"homeassistant/{component}/{self.dev_id}/{object_id}/config", payload)

    def _power(self, oid: str, name: str, st: str, fld: str) -> None:
        self._ha("sensor", oid, {"name": name, "stat_t": st, "val_tpl": f"{{{{ value_json.{fld} }}}}",
                                 "unit_of_meas": "W", "dev_cla": "power", "stat_cla": "measurement"})

    def _energy(self, oid: str, name: str, st: str, fld: str) -> None:
        self._ha("sensor", oid, {"name": name, "stat_t": st, "val_tpl": f"{{{{ value_json.{fld} }}}}",
                                 "unit_of_meas": "kWh", "dev_cla": "energy",
                                 "stat_cla": "total_increasing"})

    def _publish_discovery(self, c: dict) -> None:
        p = self.prefix
        solar, energy = f"{p}/solar/state", f"{p}/energy/state"
        self._power("production", "Produktion", solar, "prod")
        self._power("consumption", "Verbrauch", solar, "cons")
        self._power("grid", "Netz", solar, "grid")
        self._power("surplus", "Überschuss", solar, "surplus")
        if c["sl_fsoc"] or c["sl_fbatt"]:
            self._power("battery_power", "Batterie", solar, "batt")
            self._ha("sensor", "battery_soc", {"name": "Batterie SoC", "stat_t": solar,
                                               "val_tpl": "{{ value_json.soc }}", "unit_of_meas": "%",
                                               "dev_cla": "battery", "stat_cla": "measurement"})
        self._energy("total_produced", "Gesamt Produktion", energy, "total_prod_kwh")
        self._energy("total_consumed", "Gesamt Verbrauch", energy, "total_cons_kwh")
        self._energy("total_grid_in", "Gesamt Netzbezug", energy, "total_grid_in_kwh")
        self._energy("total_grid_out", "Gesamt Einspeisung", energy, "total_grid_out_kwh")
        for i in range(c["sh_count"]):
            e = c["shelly"][i]
            st, name = f"{p}/shelly/{i}/state", e["name"] or e["ip"]
            self._ha("switch", f"shelly_{i}_switch", {"name": name, "stat_t": st, "cmd_t": f"{p}/shelly/{i}/set",
                                                       "val_tpl": "{{ 'ON' if value_json.on else 'OFF' }}"})
            self._ha("switch", f"shelly_{i}_auto", {"name": f"{name} Automatik", "stat_t": st,
                                                     "cmd_t": f"{p}/shelly/{i}/auto/set",
                                                     "val_tpl": "{{ 'ON' if value_json.auto else 'OFF' }}"})
            self._power(f"shelly_{i}_power", f"{name} Leistung", st, "apower")
            self._energy(f"shelly_{i}_energy", f"{name} Energie", st, "e_tot")
            self._ha("binary_sensor", f"shelly_{i}_conn", {"name": f"{name} Erreichbar", "stat_t": st,
                                                            "val_tpl": "{{ 'ON' if value_json.reach else 'OFF' }}",
                                                            "dev_cla": "connectivity"})
            if e["rw"] > 0:
                self._ha("binary_sensor", f"shelly_{i}_run", {"name": f"{name} läuft", "stat_t": st,
                                                               "val_tpl": "{{ 'ON' if value_json.running else 'OFF' }}",
                                                               "dev_cla": "running"})
        for i in range(c["ex_count"]):
            name = c["ext"][i]["name"] or "Extern"
            st = f"{p}/ext/{i}/state"
            self._ha("switch", f"ext_{i}_switch", {"name": name, "stat_t": st, "cmd_t": f"{p}/ext/{i}/set",
                                                    "val_tpl": "{{ 'ON' if value_json.on else 'OFF' }}"})
            self._ha("switch", f"ext_{i}_auto", {"name": f"{name} Automatik", "stat_t": st,
                                                  "cmd_t": f"{p}/ext/{i}/auto/set",
                                                  "val_tpl": "{{ 'ON' if value_json.auto else 'OFF' }}"})

    def _cleanup_removed(self, c: dict) -> None:
        old_sh, old_ex = self._last_counts
        comps = (("switch", "switch"), ("auto", "switch"), ("power", "sensor"), ("energy", "sensor"),
                 ("conn", "binary_sensor"), ("run", "binary_sensor"))
        for i in range(c["sh_count"], min(old_sh, MAX_SHELLY)):
            for suffix, comp in comps:
                self._pub(f"homeassistant/{comp}/{self.dev_id}/shelly_{i}_{suffix}/config", "")
            self._pub(f"{self.prefix}/shelly/{i}/state", "")
        for i in range(c["ex_count"], min(old_ex, MAX_EXT)):
            for suffix in ("switch", "auto"):
                self._pub(f"homeassistant/switch/{self.dev_id}/ext_{i}_{suffix}/config", "")
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
                    if now - self._last_energy >= 60:
                        self._last_energy = now
                        self._publish_energy()
                await asyncio.sleep(0.2)
        finally:
            self._stop()
