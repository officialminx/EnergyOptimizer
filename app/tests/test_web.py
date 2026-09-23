import pytest
from aiohttp.test_utils import TestClient, TestServer

from energyoptimizer.web import WebApi

from .fakes import make_app


@pytest.fixture
async def client(tmp_path):
    eo = await make_app(tmp_path)
    api = WebApi(eo)
    cl = TestClient(TestServer(api.build()))
    await cl.start_server()
    cl.eo = eo
    yield cl
    await cl.close()
    await eo.session.close()


async def test_open_without_password_and_status_shape(client):
    r = await client.get("/api/status")
    assert r.status == 200
    d = await r.json()
    for k in ("prod", "cons", "grid", "sl_age", "sl_next", "energy", "shelly", "ext", "cfg", "mqtt"):
        assert k in d
    assert d["cfg"]["hyst_on"] == 3
    r = await client.get("/")
    assert "<!DOCTYPE html>" in await r.text()
    assert r.headers["X-Frame-Options"] == "DENY"


async def test_password_login_flow(client):
    client.eo.settings.cfg["web_pass"] = "pw1"
    r = await client.get("/api/status")
    assert r.status == 401 and (await r.json()) == {"ok": False, "auth": False}
    r = await client.get("/")
    assert "login" in (await r.text()).lower()
    r = await client.post("/api/login", json={"pw": "falsch"})
    assert r.status == 401
    r = await client.post("/api/login", json={"pw": "pw1"})
    assert r.status == 200 and "eo_auth=" in r.headers["Set-Cookie"]
    r = await client.get("/api/status")
    assert r.status == 200


async def test_brute_force_brake(client):
    client.eo.settings.cfg["web_pass"] = "pw1"
    codes = []
    for _ in range(6):
        r = await client.post("/api/login", json={"pw": "x"})
        codes.append(r.status)
    assert 429 in codes


async def test_cross_origin_post_is_rejected(client):
    r = await client.post("/api/refresh", headers={"Origin": "http://evil.example"})
    assert r.status == 403
    r = await client.post("/api/refresh", headers={"Origin": f"http://{client.host}:{client.port}"})
    assert r.status == 200


async def test_save_compacts_devices_and_export_import_roundtrip(client):
    r = await client.post("/api/save", json={
        "s1_ip": "192.168.1.60", "s1_name": "Pool", "s1_pw": "900", "s1_auto": "true",
        "s1_sch": "480,600,31,1", "e2_name": "Wallbox", "e2_pw": "4000",
        "on_margin": "250", "nt_srv": "http://ntfy.example.com", "sl_pass": "",
    })
    d = await r.json()
    assert d["ok"] and "ntfy-Server nicht übernommen" in d["warn"]
    c = client.eo.settings.cfg
    assert c["sh_count"] == 1 and c["shelly"][0]["ip"] == "192.168.1.60"
    assert c["shelly"][0]["auto"] is True and c["shelly"][0]["pw"] == 900
    assert c["ex_count"] == 1 and c["ext"][0]["name"] == "Wallbox"
    assert c["on_margin"] == 250
    exp = await (await client.get("/api/config/export")).json()
    assert exp["eo_config"] == 1 and exp["s0_sch"] == "480,600,31,1"
    exp["on_margin"] = 300
    r = await client.post("/api/config/import", json=exp)
    assert (await r.json())["ok"]
    assert client.eo.settings.cfg["on_margin"] == 300
    assert client.eo.settings.cfg["shelly"][0]["ip"] == "192.168.1.60"


async def test_daily_import_and_ideas(client):
    csv = "date,prod_wh,cons_wh,grid_in_wh,grid_out_wh\n2026-01-01,1000,2000,1500,500\n2026-01-02,1,2,3,4,1\n"
    form = {"file": csv.encode()}
    import aiohttp
    data = aiohttp.FormData()
    data.add_field("file", form["file"], filename="d.csv", content_type="text/csv")
    r = await client.post("/api/history_daily_import", data=data)
    assert (await r.json())["ok"]
    days = (await (await client.get("/api/history_daily")).json())["days"]
    assert days[0] == [20260101, 1000, 2000, 1500, 500, 1]
    assert days[1][5] == 3
    r = await client.post("/api/ideas", json={"title": "Test", "text": "x", "prio": 2})
    iid = (await r.json())["id"]
    ideas = await (await client.get("/api/ideas")).json()
    assert ideas["n"] == 1 and ideas["ideas"][0]["prio_txt"] == "hoch"
    r = await client.post(f"/api/ideas/delete?id={iid}")
    assert r.status == 200


async def test_update_explains_docker_way(client):
    r = await client.post("/api/update")
    d = await r.json()
    assert not d["ok"] and "docker compose pull" in d["msg"]
