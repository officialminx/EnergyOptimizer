"""Data sources for production, consumption, grid and battery.

Every source returns a `SolarData` (see solarlog.py) or None when it could not
be read. Conventions:

* production_w and consumption_w are positive,
* grid_w is positive for import and negative for feed-in,
* battery_w is positive while charging and negative while discharging,
* surplus_w = production − consumption; without a production reading it is the
  feed-in (−grid).

Sources:

* ``solarlog`` – Solar-Log JSON API (solarlog.py), with day and total counters.
* ``fronius`` – Fronius Solar API v1 (GetPowerFlowRealtimeData).
* ``shelly_em`` – Shelly Pro 3EM / Pro EM / EM Gen3 (RPC) or 3EM / EM Gen1 as grid
  meter, optionally a second Shelly that measures the PV output.
* ``modbus`` – Modbus TCP with presets for SMA and Huawei SUN2000, or registers
  of your own ("address:type:factor").
* ``mqtt`` – values from MQTT topics ("topic", "topic#json.key", "…*factor").
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from typing import TYPE_CHECKING, Any

import aiohttp

from .clock import CLOCK
from .solarlog import SolarData, SolarLogReader

if TYPE_CHECKING:
    from .app import EnergyOptimizer

_LOGGER = logging.getLogger(__name__)

RAW_KEEP = 1400
HTTP_TIMEOUT = 5


def compose(prod: float | None, cons: float | None, grid: float | None,
            batt: float | None = None, soc: float | None = None) -> SolarData | None:
    """Builds a SolarData from whichever values a source measures."""
    out = SolarData(valid=True, has_counters=False)
    out.has_prod = prod is not None
    p = max(0.0, prod) if prod is not None else 0.0
    b = batt or 0.0
    if batt is not None:
        out.has_battery = True
        out.battery_w = b
        out.battery_soc = int(round(soc)) if soc is not None else 0
    if cons is None and grid is None:
        return None
    if cons is None:
        # House load = what comes from PV and the grid, minus what goes into the battery.
        cons = p + float(grid or 0.0) - b if prod is not None else max(0.0, float(grid or 0.0))
    if grid is None:
        grid = cons - p + b
    out.production_w = p
    out.consumption_w = max(0.0, float(cons))
    out.grid_w = float(grid)
    out.surplus_w = out.production_w - out.consumption_w if prod is not None else -out.grid_w
    return out


class Source:
    name = ""

    def __init__(self, app: EnergyOptimizer) -> None:
        self.app = app
        self.last_error = ""
        self.raw = ""

    @property
    def session(self) -> aiohttp.ClientSession:
        return self.app.session

    async def fetch(self, cfg: dict) -> SolarData | None:
        raise NotImplementedError

    def target(self, cfg: dict) -> str:
        return cfg["src_host"]

    def close(self) -> None:
        pass

    def _fail(self, msg: str) -> None:
        if msg != self.last_error:
            _LOGGER.warning("[%s] %s", self.name, msg)
        self.last_error = msg

    async def _get_json(self, url: str) -> Any:
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)) as r:
            if r.status != 200:
                raise ValueError(f"HTTP {r.status}")
            text = await r.text(errors="replace")
        self.raw = (self.raw + "\n" + text)[-RAW_KEEP:] if self.raw else text[:RAW_KEEP]
        return json.loads(text)


class SolarLogSource(Source):
    name = "Solar-Log"

    def __init__(self, app: EnergyOptimizer, reader: SolarLogReader) -> None:
        super().__init__(app)
        self.reader = reader

    def target(self, cfg: dict) -> str:
        return cfg["sl_ip"]

    async def fetch(self, cfg: dict) -> SolarData | None:
        sd = await self.reader.fetch(cfg)
        self.last_error = self.reader.last_error
        self.raw = self.reader.raw_main
        return sd


def _url(host: str, port: int, path: str) -> str:
    return f"http://{host}{'' if port in (0, 80) else f':{port}'}{path}"


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class FroniusSource(Source):
    """Fronius Symo/Gen24 with Smart Meter: Solar API v1 power flow."""

    name = "Fronius"

    async def fetch(self, cfg: dict) -> SolarData | None:
        self.raw = ""
        url = _url(cfg["src_host"], cfg["src_port"], "/solar_api/v1/GetPowerFlowRealtimeData.fcgi")
        try:
            doc = await self._get_json(url)
            site = doc["Body"]["Data"]["Site"]
        except (TimeoutError, aiohttp.ClientError, ValueError, KeyError, TypeError) as err:
            self._fail(f"{cfg['src_host']}: {type(err).__name__} {err}".strip())
            return None
        pv = _num(site.get("P_PV")) or 0.0
        load = _num(site.get("P_Load"))
        grid = _num(site.get("P_Grid"))
        akku = _num(site.get("P_Akku"))
        soc = None
        inv = doc["Body"]["Data"].get("Inverters")
        if isinstance(inv, dict):
            for x in inv.values():
                if isinstance(x, dict) and _num(x.get("SOC")) is not None:
                    soc = _num(x.get("SOC"))
                    break
        # Fronius: P_Load is negative for consumption, P_Akku positive while discharging.
        sd = compose(pv, abs(load) if load is not None else None, grid,
                     -akku if akku is not None else None, soc)
        if sd is None:
            self._fail("Fronius sent neither load nor grid power – is a Smart Meter installed?")
            return None
        self.last_error = ""
        return sd


class ShellyEmSource(Source):
    """Shelly energy meter at the grid connection, optionally one more for the PV output."""

    name = "Shelly EM"

    def __init__(self, app: EnergyOptimizer) -> None:
        super().__init__(app)
        self._kind: dict[str, str] = {}

    async def _power(self, host: str, pv: bool) -> float:
        """Active power of a Shelly meter; the working request is remembered per host."""
        tries = [self._kind[host]] if host in self._kind else []
        tries += [t for t in ("em", "em1", "pm1", "switch", "gen1") if t not in tries]
        last: Exception | None = None
        for kind in tries:
            try:
                val = await self._read(host, kind, pv)
            except (TimeoutError, aiohttp.ClientError, ValueError, KeyError, TypeError) as err:
                last = err
                continue
            self._kind[host] = kind
            return val
        self._kind.pop(host, None)
        raise ValueError(f"{host}: no Shelly meter answer ({type(last).__name__ if last else '-'})")

    async def _read(self, host: str, kind: str, pv: bool) -> float:
        if kind == "em":       # Pro 3EM
            d = await self._get_json(f"http://{host}/rpc/EM.GetStatus?id=0")
            return float(d["total_act_power"])
        if kind == "em1":      # Pro EM, EM Gen3
            d = await self._get_json(f"http://{host}/rpc/EM1.GetStatus?id=0")
            return float(d["act_power"])
        if kind == "pm1":      # PM Mini (PV meter)
            d = await self._get_json(f"http://{host}/rpc/PM1.GetStatus?id=0")
            return float(d["apower"])
        if kind == "switch":   # Plus/Pro PM (PV meter)
            d = await self._get_json(f"http://{host}/rpc/Switch.GetStatus?id=0")
            return float(d["apower"])
        d = await self._get_json(f"http://{host}/status")   # Gen1 3EM / EM
        if "total_power" in d:
            return float(d["total_power"])
        meters = d.get("emeters") or d.get("meters")
        return float(meters[0]["power"])

    async def fetch(self, cfg: dict) -> SolarData | None:
        self.raw = ""
        try:
            grid = await self._power(cfg["src_host"], False)
            prod = abs(await self._power(cfg["em_pv_ip"], True)) if cfg["em_pv_ip"] else None
        except ValueError as err:
            self._fail(str(err))
            return None
        self.last_error = ""
        return compose(prod, None, grid)


# ── Modbus TCP ──────────────────────────────────────────────────────────────

class ModbusError(Exception):
    pass


class ModbusTcp:
    """Minimal Modbus TCP client: read holding registers (function 3), one connection."""

    def __init__(self, host: str, port: int, unit: int) -> None:
        self.host, self.port, self.unit = host, port, unit
        self._r: asyncio.StreamReader | None = None
        self._w: asyncio.StreamWriter | None = None
        self._tid = 0
        self._lock = asyncio.Lock()

    async def _connect(self) -> None:
        self._r, self._w = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 5)
        # Huawei inverters drop requests sent right after the connection is opened.
        await asyncio.sleep(0.5)

    def close(self) -> None:
        if self._w is not None:
            self._w.close()
        self._r = self._w = None

    async def read(self, addr: int, count: int) -> list[int]:
        async with self._lock:
            for attempt in range(2):
                try:
                    if self._w is None:
                        await self._connect()
                    return await self._read(addr, count)
                except (TimeoutError, OSError, asyncio.IncompleteReadError) as err:
                    self.close()
                    if attempt:
                        raise ModbusError(f"{self.host}:{self.port}: {type(err).__name__}") from err
        raise ModbusError("unreachable")

    async def _read(self, addr: int, count: int) -> list[int]:
        assert self._r is not None and self._w is not None
        self._tid = (self._tid + 1) & 0xFFFF
        pdu = struct.pack(">BHH", 3, addr, count)
        self._w.write(struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit) + pdu)
        await self._w.drain()
        while True:
            hdr = await asyncio.wait_for(self._r.readexactly(7), 5)
            tid, _proto, length, _unit = struct.unpack(">HHHB", hdr)
            body = await asyncio.wait_for(self._r.readexactly(length - 1), 5)
            if tid == self._tid:
                break
        if body[0] & 0x80:
            raise ModbusError(f"register {addr}: exception code {body[1]}")
        n = body[1]
        return list(struct.unpack(f">{n // 2}H", body[2:2 + n]))


def _decode(words: list[int], typ: str) -> float | None:
    if typ == "u16":
        v = words[0]
        return None if v == 0xFFFF else float(v)
    if typ == "s16":
        v = words[0]
        return None if v == 0x8000 else float(v - 0x10000 if v & 0x8000 else v)
    raw = (words[0] << 16) | words[1]
    if typ == "u32":
        return None if raw == 0xFFFFFFFF else float(raw)
    if raw == 0x80000000:          # SMA: not available (NaN)
        return None
    return float(raw - 0x100000000 if raw & 0x80000000 else raw)


def parse_spec(spec: str) -> tuple[int, str, float] | None:
    if not spec:
        return None
    parts = spec.split(":")
    try:
        return int(parts[0]), parts[1].lower(), float(parts[2]) if len(parts) > 2 else 1.0
    except (ValueError, IndexError):
        return None


# Presets: production, grid (+ import), battery (+ charging) and state of charge.
# Each value is a list of (address, type, factor) whose sum is the value.
MODBUS_PRESETS: dict[str, dict[str, list[tuple[int, str, float]]]] = {
    # SMA Sunny Tripower/Boy with Modbus on (unit 3). Grid values need a Sunny
    # Home Manager / Energy Meter; battery values exist on Sunny Island / SBS.
    "sma": {
        "prod": [(30775, "s32", 1.0)],
        "grid": [(30865, "u32", 1.0), (30867, "u32", -1.0)],
        "batt": [(31393, "u32", 1.0), (31395, "u32", -1.0)],
        "soc": [(30845, "u32", 1.0)],
    },
    # Huawei SUN2000 with power meter (unit 1, port 502 of the inverter or the
    # SDongle). The meter reports feed-in as positive.
    "huawei": {
        "prod": [(32080, "s32", 1.0)],
        "grid": [(37113, "s32", -1.0)],
        "batt": [(37765, "s32", 1.0)],
        "soc": [(37760, "u16", 0.1)],
    },
}


class ModbusSource(Source):
    name = "Modbus"

    def __init__(self, app: EnergyOptimizer) -> None:
        super().__init__(app)
        self._cl: ModbusTcp | None = None
        self._key: tuple | None = None
        self._missing: set[str] = set()

    def close(self) -> None:
        if self._cl is not None:
            self._cl.close()
        self._cl = None

    def _regs(self, cfg: dict) -> dict[str, list[tuple[int, str, float]]]:
        if cfg["mb_preset"] in MODBUS_PRESETS:
            return MODBUS_PRESETS[cfg["mb_preset"]]
        out = {}
        for key in ("prod", "grid", "batt", "soc"):
            spec = parse_spec(cfg[f"mb_{key}"])
            if spec:
                out[key] = [spec]
        return out

    async def _value(self, cl: ModbusTcp, regs: list[tuple[int, str, float]]) -> float | None:
        total = 0.0
        for addr, typ, factor in regs:
            words = await cl.read(addr, 1 if typ.endswith("16") else 2)
            v = _decode(words, typ)
            if v is None:
                return None
            total += v * factor
        return total

    async def fetch(self, cfg: dict) -> SolarData | None:
        key = (cfg["src_host"], cfg["src_port"] or 502, cfg["mb_unit"])
        if self._cl is None or key != self._key:
            self.close()
            self._cl = ModbusTcp(*key)
            self._key = key
            self._missing = set()
        regs = self._regs(cfg)
        vals: dict[str, float | None] = {}
        lines = []
        try:
            for name in ("prod", "grid", "batt", "soc"):
                if name not in regs or name in self._missing:
                    vals[name] = None
                    continue
                try:
                    vals[name] = await self._value(self._cl, regs[name])
                except ModbusError as err:
                    if name in ("prod", "grid") or "exception" not in str(err):
                        raise
                    # The device does not have this register (e.g. no battery): stop asking.
                    self._missing.add(name)
                    vals[name] = None
                lines.append(f"{name}: {vals[name]}")
        except ModbusError as err:
            self._fail(str(err))
            self.raw = "\n".join(lines + [f"[error] {err}"])
            return None
        self.raw = "\n".join(lines)
        sd = compose(vals["prod"], None, vals["grid"], vals["batt"], vals["soc"])
        if sd is None:
            self._fail("The grid power could not be read (is a power meter connected?)")
            return None
        self.last_error = ""
        return sd


# ── MQTT ────────────────────────────────────────────────────────────────────

MQTT_KEYS = ("mqs_prod", "mqs_cons", "mqs_grid", "mqs_batt", "mqs_soc")


def parse_topic_spec(spec: str) -> tuple[str, str, float]:
    """"topic#json.path*factor" → (topic, path, factor)."""
    factor = 1.0
    if "*" in spec:
        spec, f = spec.rsplit("*", 1)
        try:
            factor = float(f)
        except ValueError:
            factor = 1.0
    topic, _, path = spec.partition("#")
    return topic.strip(), path.strip(), factor


def extract(payload: str, path: str) -> float | None:
    if not path:
        return _num(payload.strip())
    try:
        v: Any = json.loads(payload)
    except ValueError:
        return None
    for part in path.split("."):
        if isinstance(v, dict):
            v = v.get(part)
        elif isinstance(v, list) and part.isdigit() and int(part) < len(v):
            v = v[int(part)]
        else:
            return None
    return _num(v)


class MqttSource(Source):
    name = "MQTT"

    def target(self, cfg: dict) -> str:
        return cfg["mq_host"]

    async def fetch(self, cfg: dict) -> SolarData | None:
        if not cfg["mq_en"]:
            self._fail("MQTT is switched off (Settings → MQTT)")
            return None
        values: dict[str, float | None] = {}
        oldest = 0.0
        lines = []
        now = CLOCK.mono()
        max_age = max(120.0, cfg["src_poll_s"] * 3.0)
        for key in MQTT_KEYS:
            spec = cfg[key]
            values[key] = None
            if not spec:
                continue
            topic, path, factor = parse_topic_spec(spec)
            got = self.app.mqtt.topic_values.get(topic)
            if got is None:
                lines.append(f"{topic}: –")
                continue
            payload, t = got
            lines.append(f"{topic}: {payload[:80]}")
            v = extract(payload, path)
            if v is not None and now - t <= max_age:
                values[key] = v * factor
                oldest = max(oldest, now - t)
        self.raw = "\n".join(lines)
        sd = compose(values["mqs_prod"], values["mqs_cons"], values["mqs_grid"],
                     values["mqs_batt"], values["mqs_soc"])
        if sd is None:
            self._fail("No recent values on the configured topics")
            return None
        self.last_error = ""
        return sd


def make_source(app: EnergyOptimizer, kind: str) -> Source:
    if kind == "fronius":
        return FroniusSource(app)
    if kind == "shelly_em":
        return ShellyEmSource(app)
    if kind == "modbus":
        return ModbusSource(app)
    if kind == "mqtt":
        return MqttSource(app)
    return SolarLogSource(app, app.reader)


def mqtt_topics(cfg: dict) -> list[str]:
    """Topics the MQTT source needs subscribed."""
    if cfg["src"] != "mqtt":
        return []
    return [t for t in (parse_topic_spec(cfg[k])[0] for k in MQTT_KEYS if cfg[k]) if t]
