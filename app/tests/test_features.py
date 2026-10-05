import json
from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from energyoptimizer.const import AL_EXT0, MAX_SHELLY
from energyoptimizer.settings import SettingsStore
from energyoptimizer.web import WebApi

from .fakes import FakeClock, FakeShelly, make_app


@pytest.fixture
async def env(tmp_path, monkeypatch):
    clock = FakeClock(monkeypatch, datetime(2026, 6, 10, 12, 0))
    eo = await make_app(tmp_path)
    shellies = [FakeShelly("Boiler", apower=1800), FakeShelly("Pumpe", apower=600)]
    c = eo.settings.cfg
    for i, sh in enumerate(shellies):
        c["shelly"][i].update(ip=await sh.start(), name=sh.name, pw=1000, pri=i + 1, auto=True)
    c["sh_count"] = 2
    c["src_poll_s"] = 60
    yield eo, clock, shellies
    for sh in shellies:
        await sh.close()
    await eo.session.close()


async def test_dry_run_decides_but_switches_nothing(env):
    eo, clock, (boiler, pumpe) = env
    dv = eo.devices
    eo.settings.cfg["dry_run"] = True
    for _ in range(3):
        await dv.distribute(1300)
        clock.advance(60)
    assert boiler.cmds == [] and pumpe.cmds == []
    s = dv.st["shelly"][0]
    assert s.on and s.virt and not s.real_on
    ev = [e for e in eo.events.ev if e[2] == 2]
    assert ev and ev[-1][5] & 0x04            # logged as dry run
    # The virtual boiler would draw 1000 W: the same surplus does not start the pump.
    for _ in range(4):
        await dv.distribute(1300)
        clock.advance(60)
    assert not dv.st["shelly"][1].on
    # Polling does not overwrite the virtual state …
    await dv.poll_all()
    assert s.on and s.virt
    # … and a manual command still switches for real.
    dv.apply_command("shelly", 1, "on")
    await asyncio_sleep()
    assert pumpe.cmds == [True]
    # Leaving the dry run goes back to the real states.
    dv.dry_run_changed(False)
    eo.settings.cfg["dry_run"] = False
    assert not s.on and not s.virt


async def asyncio_sleep():
    import asyncio
    for _ in range(5):
        await asyncio.sleep(0.01)


async def test_learned_power_from_readings(env):
    eo, clock, (boiler, _) = env
    dv = eo.devices
    boiler.on = True
    for _ in range(12):
        await dv.get_status(0)
    assert dv.learned_w(0) == 1800
    assert dv.learned_w(1) == 0


async def test_external_feedback_mismatch(env):
    eo, clock, _ = env
    c = eo.settings.cfg
    c["ext"][0].update(name="Wallbox", pw=2000, auto=False, st="wallbox/state")
    c["ex_count"] = 1
    dv = eo.devices
    await dv.set("ext", 0, True)
    assert not dv.ext_mismatch(0)              # within the grace time
    clock.advance(61)
    assert dv.ext_mismatch(0)                  # no answer on the state topic
    eo.mqtt._extra = ["wallbox/state"]
    eo.mqtt._handle("wallbox/state", b'{"state": "ON"}')
    assert dv.st["ext"][0].fb is True and not dv.ext_mismatch(0)
    eo.mqtt._handle("wallbox/state", b"off")
    assert dv.ext_mismatch(0)
    for _ in range(3):
        eo.alarms.tick(0, eo.solar, False, eo.devs)
        clock.advance(61)
    assert eo.alarms.al[AL_EXT0].active


async def test_explain_reasons(env):
    eo, clock, _ = env
    dv = eo.devices
    eo.sl_ok = True
    for s in dv.st["shelly"][:2]:
        s.reachable = True
    await dv.distribute(500)
    why = dv.explain("shelly", 0)
    assert why["c"] == "waiting" and why["need"] == 1150 and why["miss"] == 650
    await dv.distribute(1300)
    assert dv.explain("shelly", 0)["c"] == "switching_on"
    eo.settings.cfg["shelly"][1]["auto"] = False
    assert dv.explain("shelly", 1)["c"] == "auto_off"


async def test_slots_follow_their_plug_when_one_is_removed(env):
    eo, clock, (boiler, pumpe) = env
    e = eo.energy.c
    e["total_shelly_wh"][1] = 4242
    dv = eo.devices
    dv.st["shelly"][1].today_on_s = 99
    # Remove the first plug: the pump moves to slot 0 with its counters.
    eo.apply_settings({"s0_ip": ""})
    assert eo.settings.cfg["shelly"][0]["name"] == "Pumpe" and eo.settings.cfg["sh_count"] == 1
    assert e["total_shelly_wh"][0] == 4242 and e["total_shelly_wh"][1] == 0
    assert dv.st["shelly"][0].today_on_s == 99


def test_settings_migrate_from_0_0_5(tmp_path):
    old = {"web_pass": "", "sl_poll_min": 2, "sh_count": 2,
           "shelly": [{"name": "A", "ip": "10.0.0.1", "pw": 500}, {"name": "B", "ip": "10.0.0.2"},
                      {}, {}],
           "ext": [{}, {}, {}, {}], "p_buy": 30}
    (tmp_path / "settings.json").write_text(json.dumps(old))
    st = SettingsStore(str(tmp_path))
    st.load()
    c = st.cfg
    assert c["src_poll_s"] == 120 and c["sh_poll_s"] == 60 and c["src"] == "solarlog"
    assert len(c["shelly"]) == MAX_SHELLY and c["shelly"][1]["ip"] == "10.0.0.2"
    assert c["ext"][0]["st"] == "" and c["sh_count"] == 2


def test_energy_counters_padded_on_load(tmp_path):
    from energyoptimizer.energy import Energy
    (tmp_path / "energy.json").write_text(json.dumps({"total_shelly_wh": [1, 2, 3, 4], "yday": 1}))
    en = Energy(str(tmp_path), lambda *a: None)
    en.load()
    assert en.c["total_shelly_wh"][:5] == [1, 2, 3, 4, 0] and len(en.c["learned_w"]) == MAX_SHELLY


def test_history_migration_and_day_points(tmp_path, monkeypatch):
    from energyoptimizer.clock import CLOCK
    from energyoptimizer.history import DATALOG_HDR, History
    FakeClock(monkeypatch, datetime(2026, 6, 10, 12, 0))
    t0 = int(datetime(2026, 6, 9, 10, 0, tzinfo=CLOCK.tz).timestamp())
    old_hdr = "epoch,prod_w,cons_w,grid_w,sh0_w,sh1_w,sh2_w,sh3_w,sh0_on,sh1_on,sh2_on,sh3_on"
    (tmp_path / "hist.csv").write_text(f"{old_hdr}\n{t0},3000,1000,-2000,900,0,0,0,1,0,0,0\n")
    h = History(str(tmp_path))
    h.load()
    lines = (tmp_path / "hist.csv").read_text().splitlines()
    assert lines[0] == DATALOG_HDR
    pts = h.day_points(20260609)
    assert len(pts) == 1 and pts[0][:7] == [t0, 3000, 1000, -2000, 0, 1, 0] and pts[0][7] == 900
    assert h.day_points(20260608) == []
    assert 20260609 in h.days_available()
    # Old ring format (four plugs, combined mask) is converted on load.
    assert History._upgrade_point([1, 2, 3, 4, 10, 20, 30, 40, 0x13])[:9] == [1, 2, 3, 4, 0, 3, 1, 10, 20]


@pytest.fixture
async def client(tmp_path):
    eo = await make_app(tmp_path)
    api = WebApi(eo)
    cl = TestClient(TestServer(api.build()))
    await cl.start_server()
    cl.eo = eo
    r = await cl.post("/api/setup", json={"pw": "geheim1"})
    assert r.status == 200
    yield cl
    await cl.close()
    await eo.session.close()


async def test_sessions_endpoints_and_password_change(client):
    d = await (await client.get("/api/sessions")).json()
    assert len(d["sessions"]) == 1 and d["sessions"][0]["current"]
    client.eo.sessions.create("other device")
    r = await client.post("/api/sessions/revoke", json={"all": True})
    assert (await r.json())["n"] == 1
    client.eo.sessions.create("again")
    await client.post("/api/save", json={"web_pass": "neues-pw"})
    d = await (await client.get("/api/sessions")).json()
    assert len(d["sessions"]) == 1        # the other one was signed out, this one stays
    await client.post("/api/logout")
    assert (await client.get("/api/status")).status == 401


async def test_legacy_cookie_is_upgraded(client):
    api_token = None
    for app_obj in [client.server.app]:
        for r in app_obj.router.routes():
            h = getattr(r.handler, "__self__", None)
            if isinstance(h, WebApi):
                api_token = h.legacy_token()
                break
    client.session.cookie_jar.clear()
    r = await client.get("/api/status", cookies={"eo_auth": api_token})
    assert r.status == 200 and "eo_auth=" in r.headers.get("Set-Cookie", "")


async def test_simulate_and_history_endpoints(client):
    r = await client.get("/api/simulate")
    d = await r.json()
    assert r.status == 200 and "loads" in d and "real" in d
    r = await client.post("/api/simulate?d=20260101", json={"on_margin": 0})
    assert (await r.json())["day"] == 20260101
    d = await (await client.get("/api/history?d=20260101")).json()
    assert d["day"] == 20260101 and d["pts"] == []
    assert "days" in await (await client.get("/api/history/days")).json()
    r = await client.post("/api/shelly/0/learned")
    assert r.status == 404


async def test_source_test_endpoint_rejects_bad_host(client):
    r = await client.post("/api/source/test", json={"src": "fronius", "src_host": "bad host!"})
    assert (await r.json())["ok"] is False


async def test_static_assets_are_versioned(client):
    await client.post("/api/wizard", json={"done": True})
    html = await (await client.get("/")).text()
    assert "/static/app.css?v=" in html and "{{V}}" not in html
    r = await client.get("/static/js/core.js?v=x")
    assert r.status == 200 and r.headers["Cache-Control"] == "no-cache"
    assert (await client.get("/static/../web.py")).status == 404


async def test_invalid_day_is_ignored_not_an_error(client):
    r = await client.get("/api/history?d=20250231")
    assert r.status == 200 and "day" not in await r.json()
    assert (await client.get("/api/simulate?d=20251399")).status == 200


async def test_signed_out_cookie_does_not_count_as_failed_login(client):
    api = next(r.handler.__self__ for r in client.server.app.router.routes()
               if isinstance(getattr(r.handler, "__self__", None), WebApi))
    client.session.cookie_jar.clear()
    for _ in range(8):
        r = await client.get("/", cookies={"eo_auth": "x" * 32})
        assert "eo_auth=;" in r.headers.get("Set-Cookie", "")
    assert api.fails == 0 and api.block_remaining() == 0


async def test_dry_run_self_regulated_load_counts_runtime(env):
    eo, clock, _ = env
    dv = eo.devices
    eo.settings.cfg["shelly"][0]["rw"] = 50
    eo.settings.cfg["dry_run"] = True
    for _ in range(3):
        await dv.distribute(1300)
        clock.advance(60)
    assert dv.st["shelly"][0].virt and dv.is_running("shelly", 0)
    assert dv.explain("shelly", 0)["c"] != "no_demand"


def test_empty_plug_slot_from_0_0_5_is_dropped(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"web_pass": "", "sh_count": 1, "shelly": [{}]}))
    st = SettingsStore(str(tmp_path))
    st.load()
    assert st.cfg["sh_count"] == 0


def test_today_is_empty_until_the_first_point_after_midnight(tmp_path, monkeypatch):
    from energyoptimizer.history import History
    FakeClock(monkeypatch, datetime(2026, 6, 10, 0, 5))
    h = History(str(tmp_path))
    h.yday = 160                      # 9 June
    h.pts.append([1] * 23)
    assert h.day_points(20260610) == []
