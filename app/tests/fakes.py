from __future__ import annotations

from datetime import datetime

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from energyoptimizer.clock import CLOCK


class FakeClock:
    """Ersetzt CLOCK.mono/time durch steuerbare Werte."""

    def __init__(self, monkeypatch, start: datetime) -> None:
        self.mono_t = 1000.0
        self.epoch = start.replace(tzinfo=CLOCK.tz).timestamp()
        monkeypatch.setattr(CLOCK, "mono", lambda: self.mono_t)
        monkeypatch.setattr(CLOCK, "time", lambda: self.epoch)

    def advance(self, seconds: float) -> None:
        self.mono_t += seconds
        self.epoch += seconds


class FakeShelly:
    """Shelly Gen2-RPC: Switch.Set / Switch.GetStatus / Shelly.GetDeviceInfo."""

    def __init__(self, name: str = "Boiler", apower: float = 0.0) -> None:
        self.on = False
        self.name = name
        self.apower = apower
        self.cmds: list[bool] = []
        self.server: TestServer | None = None

    def app(self) -> web.Application:
        async def set_(req):
            on = req.query.get("on") == "true"
            self.cmds.append(on)
            self.on = on
            return web.json_response({"was_on": not on})

        async def status(req):
            return web.json_response({"id": 0, "output": self.on, "apower": self.apower if self.on else 0,
                                      "voltage": 230.1, "current": 0.5, "temperature": {"tC": 40.2},
                                      "aenergy": {"total": 1000.0}})

        async def info(req):
            return web.json_response({"name": self.name, "id": f"shellyplus1pm-{self.name.lower()}",
                                      "gen": 2, "model": "SNSW-001P16EU"})

        a = web.Application()
        a.router.add_get("/rpc/Switch.Set", set_)
        a.router.add_get("/rpc/Switch.GetStatus", status)
        a.router.add_get("/rpc/Shelly.GetDeviceInfo", info)
        return a

    async def start(self) -> str:
        self.server = TestServer(self.app())
        await self.server.start_server()
        return f"{self.server.host}:{self.server.port}"

    async def close(self) -> None:
        if self.server:
            await self.server.close()


async def make_app(tmp_path):
    from energyoptimizer.app import EnergyOptimizer

    eo = EnergyOptimizer(str(tmp_path))
    eo.settings.load()
    eo.session = aiohttp.ClientSession()
    from energyoptimizer.solarlog import SolarLogReader
    eo.reader = SolarLogReader(eo.session)
    return eo
