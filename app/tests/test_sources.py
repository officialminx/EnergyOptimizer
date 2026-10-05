import asyncio
import struct

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from energyoptimizer import sources
from energyoptimizer.sources import compose, extract, parse_topic_spec

from .fakes import make_app


def test_compose_conventions():
    sd = compose(3000, 1000, None)
    assert sd.grid_w == -2000 and sd.surplus_w == 2000 and sd.has_prod
    sd = compose(None, None, 800)          # grid meter only
    assert not sd.has_prod and sd.consumption_w == 800 and sd.surplus_w == -800
    sd = compose(None, None, -1200)
    assert sd.surplus_w == 1200 and sd.consumption_w == 0
    sd = compose(3000, None, -500, batt=1000, soc=55)   # 1 kW into the battery
    assert sd.consumption_w == 1500 and sd.has_battery and sd.battery_soc == 55
    assert compose(1000, None, None) is None


def test_mqtt_spec_and_extract():
    assert parse_topic_spec("tele/meter/SENSOR#ENERGY.Power*-1") == ("tele/meter/SENSOR", "ENERGY.Power", -1.0)
    assert parse_topic_spec("pv/power") == ("pv/power", "", 1.0)
    assert extract('{"ENERGY": {"Power": 512}}', "ENERGY.Power") == 512
    assert extract("  1234.5 ", "") == 1234.5
    assert extract('{"a": [1, 2]}', "a.1") == 2
    assert extract("garbage", "x") is None


@pytest.fixture
async def eo(tmp_path):
    app = await make_app(tmp_path)
    yield app
    await app.session.close()


async def _serve(routes):
    a = web.Application()
    for path, doc in routes.items():
        async def h(req, doc=doc):
            return web.json_response(doc)
        a.router.add_get(path, h)
    srv = TestServer(a)
    await srv.start_server()
    return srv


async def test_fronius(eo):
    srv = await _serve({"/solar_api/v1/GetPowerFlowRealtimeData.fcgi": {"Body": {"Data": {
        "Site": {"P_PV": 4200.0, "P_Load": -1300.0, "P_Grid": -2400.0, "P_Akku": -500.0},
        "Inverters": {"1": {"SOC": 61.5}}}}}})
    c = eo.settings.cfg
    c.update(src="fronius", src_host=srv.host, src_port=srv.port)
    src = sources.make_source(eo, "fronius")
    sd = await src.fetch(c)
    assert sd.production_w == 4200 and sd.consumption_w == 1300 and sd.grid_w == -2400
    assert sd.battery_w == 500 and sd.battery_soc == 62 and sd.surplus_w == 2900
    assert not sd.has_counters
    await srv.close()


async def test_shelly_em_grid_and_pv_meter(eo):
    grid = await _serve({"/rpc/EM.GetStatus": {"total_act_power": -900.0}})
    pv = await _serve({"/rpc/Switch.GetStatus": {"apower": 2500.0}})
    c = eo.settings.cfg
    c.update(src="shelly_em", src_host=f"{grid.host}:{grid.port}", em_pv_ip=f"{pv.host}:{pv.port}")
    src = sources.make_source(eo, "shelly_em")
    sd = await src.fetch(c)
    assert sd.production_w == 2500 and sd.grid_w == -900 and sd.consumption_w == 1600
    assert sd.surplus_w == 900
    c["em_pv_ip"] = ""
    sd = await src.fetch(c)
    assert not sd.has_prod and sd.surplus_w == 900
    await grid.close()
    await pv.close()


async def test_shelly_gen1_3em(eo):
    srv = await _serve({"/status": {"total_power": 450.0, "emeters": []}})
    c = eo.settings.cfg
    c.update(src="shelly_em", src_host=f"{srv.host}:{srv.port}", em_pv_ip="")
    sd = await sources.make_source(eo, "shelly_em").fetch(c)
    assert sd.grid_w == 450 and sd.consumption_w == 450
    await srv.close()


class FakeModbus:
    """Modbus TCP server answering function 3 from a register map."""

    def __init__(self, regs: dict[int, int]) -> None:
        self.regs = regs
        self.server = None
        self.port = 0

    async def handle(self, r, w):
        try:
            while True:
                hdr = await r.readexactly(7)
                tid, _p, length, unit = struct.unpack(">HHHB", hdr)
                body = await r.readexactly(length - 1)
                _fc, addr, count = struct.unpack(">BHH", body)
                if addr not in self.regs:
                    pdu = struct.pack(">BB", 0x83, 2)
                else:
                    words = [self.regs.get(addr + k, 0) for k in range(count)]
                    pdu = struct.pack(">BB", 3, count * 2) + struct.pack(f">{count}H", *words)
                w.write(struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu)
                await w.drain()
        except asyncio.IncompleteReadError:
            pass

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def close(self):
        self.server.close()
        await self.server.wait_closed()


def _u32(v):
    v &= 0xFFFFFFFF
    return {0: v >> 16, 1: v & 0xFFFF}


def _map(base, v):
    w = _u32(v)
    return {base: w[0], base + 1: w[1]}


async def test_modbus_sma_preset(eo, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep(asyncio.sleep))
    regs = {}
    regs.update(_map(30775, 5200))          # AC power
    regs.update(_map(30865, 0))             # grid draw
    regs.update(_map(30867, 3100))          # feed-in
    fake = FakeModbus(regs)                 # no battery registers: exception → no battery
    await fake.start()
    c = eo.settings.cfg
    c.update(src="modbus", src_host="127.0.0.1", src_port=fake.port, mb_preset="sma", mb_unit=3)
    src = sources.make_source(eo, "modbus")
    sd = await src.fetch(c)
    assert sd.production_w == 5200 and sd.grid_w == -3100 and sd.consumption_w == 2100
    assert not sd.has_battery
    sd = await src.fetch(c)                 # second reading over the same connection
    assert sd.surplus_w == 3100
    src.close()
    await fake.close()


async def test_modbus_custom_registers(eo, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep(asyncio.sleep))
    regs = {}
    regs.update(_map(100, 2000))
    regs.update(_map(200, -700))            # signed grid power, already + import
    regs[300] = 455                          # SOC × 10
    fake = FakeModbus(regs)
    await fake.start()
    c = eo.settings.cfg
    c.update(src="modbus", src_host="127.0.0.1", src_port=fake.port, mb_preset="custom", mb_unit=1,
             mb_prod="100:u32:1", mb_grid="200:s32", mb_batt="", mb_soc="300:u16:0.1")
    sd = await sources.make_source(eo, "modbus").fetch(c)
    assert sd.production_w == 2000 and sd.grid_w == -700 and sd.consumption_w == 1300
    await fake.close()


def _fast_sleep(real):
    async def sleep(t, *a, **kw):
        return await real(0 if t >= 0.5 else t, *a, **kw)
    return sleep


async def test_mqtt_source_reads_topic_values(eo):
    c = eo.settings.cfg
    c.update(src="mqtt", mq_en=True, mqs_prod="pv/w", mqs_grid="meter/SENSOR#E.P*-1", mqs_cons="")
    from energyoptimizer.clock import CLOCK
    eo.mqtt.topic_values = {"pv/w": ("3000", CLOCK.mono()), "meter/SENSOR": ('{"E": {"P": 1800}}', CLOCK.mono())}
    sd = await sources.make_source(eo, "mqtt").fetch(c)
    assert sd.production_w == 3000 and sd.grid_w == -1800 and sd.consumption_w == 1200
    assert sources.mqtt_topics(c) == ["pv/w", "meter/SENSOR"]
    eo.mqtt.topic_values = {}
    assert await sources.make_source(eo, "mqtt").fetch(c) is None
