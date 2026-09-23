import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from energyoptimizer.settings import defaults
from energyoptimizer.solarlog import SolarDevices, SolarLogClient, SolarLogReader

from . import mock_solarlog


@pytest.fixture
async def server():
    state: dict = {}
    srv = TestServer(mock_solarlog.build(state))
    await srv.start_server()
    srv.state = state
    yield srv
    await srv.close()


def _cfg(srv, password="geheim"):
    c = defaults()
    c["sl_ip"] = srv.host
    c["sl_port"] = srv.port
    c["sl_pass"] = password
    return c


async def test_hashed_login_and_battery(server):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as s:
        reader = SolarLogReader(s)
        sd = await reader.fetch(_cfg(server))
    assert sd is not None
    assert sd.production_w == 5200
    assert sd.consumption_w == 1800
    assert sd.grid_w == 1800 - 5200
    assert sd.surplus_w == 3400
    assert sd.prod_today_wh == 12345
    # Batterie (858) steht hinter dem Login: [V, SoC, Laden, Entladen]
    assert sd.has_battery and sd.battery_soc == 81 and sd.battery_w == -400
    # Kontonamen der Reihe nach, dann bcrypt-Hash mit dem Salz des Geräts
    logins = server.state["logins"]
    assert logins[0].startswith("u=user&")
    assert logins[1] == "u=installer&p=geheim"
    assert logins[2] == f"u=installer&p={mock_solarlog.HASHED}"


async def test_without_password_basic_values_still_work(server):
    async with aiohttp.ClientSession() as s:
        reader = SolarLogReader(s)
        sd = await reader.fetch(_cfg(server, password=""))
    assert sd is not None and sd.production_w == 5200
    assert not sd.has_battery  # 858 verweigert ohne Sitzung
    assert "ACCESS DENIED" in reader.raw_main or "denied" in reader.raw_main.lower()


async def test_relogin_after_session_expired(server):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as s:
        client = SolarLogClient(s, server.host, server.port, "geheim")
        assert await client.async_login()
        server.state["expire"] = True  # der Solar-Log hat die Sitzung verworfen
        n = len(server.state["logins"])
        data = await client.request('{"858":null}')
        assert data["858"][1] == 81
        assert len(server.state["logins"]) > n


async def test_devices_and_names(server):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as s:
        reader = SolarLogReader(s)
        cfg = _cfg(server)
        await reader.fetch(cfg)
        devs = await reader.fetch_devices(cfg, SolarDevices())
        assert devs is not None and devs.count == 2
        assert devs.d[0].status == "Power" and devs.d[0].power_w == 3000
        while await reader.fetch_one_name(cfg, devs):
            pass
        assert devs.d[1].name == "WR 2"


async def test_wrong_content_type_is_rejected_by_mock(server):
    # Gegenprobe: so hat die ESP32-Firmware gefragt – der Solar-Log lehnt ab.
    async with aiohttp.ClientSession() as s:
        async with s.post(f"http://{server.host}:{server.port}/getjp", data='{"801":{"170":null}}',
                          headers={"Content-Type": "application/json"}) as r:
            assert r.status == 400
