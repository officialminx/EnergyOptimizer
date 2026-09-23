import pytest
from aiohttp.test_utils import TestClient, TestServer

from energyoptimizer.web import WebApi

from .fakes import make_app


@pytest.fixture
async def fresh(tmp_path):
    """Erststart: noch kein Web-Passwort gesetzt."""
    eo = await make_app(tmp_path)
    api = WebApi(eo)
    cl = TestClient(TestServer(api.build()))
    await cl.start_server()
    cl.eo = eo
    yield cl
    await cl.close()
    await eo.session.close()


@pytest.fixture
async def client(fresh):
    """Eingerichtet und angemeldet."""
    r = await fresh.post("/api/setup", json={"pw": "geheim1"})
    assert r.status == 200
    return fresh


async def test_first_start_requires_password_setup(fresh):
    r = await fresh.get("/api/status")
    assert r.status == 401 and (await r.json())["setup"] is True
    r = await fresh.get("/")
    assert "/api/setup" in await r.text()
    r = await fresh.post("/api/login", json={"pw": ""})
    assert r.status == 409 and (await r.json())["setup"] is True
    r = await fresh.post("/api/setup", json={"pw": "kurz"})
    assert r.status == 400
    r = await fresh.post("/api/setup", json={"pw": "geheim1"},
                         headers={"Origin": "http://evil.example"})
    assert r.status == 403
    r = await fresh.post("/api/setup", json={"pw": "geheim1"})
    assert r.status == 200 and "eo_auth=" in r.headers["Set-Cookie"]
    assert fresh.eo.settings.cfg["web_pass"] == "geheim1"
    r = await fresh.get("/api/status")
    assert r.status == 200
    # Danach ist die Einrichtung gesperrt: niemand kann das Passwort so überschreiben.
    r = await fresh.post("/api/setup", json={"pw": "anderes1"})
    assert r.status == 409 and fresh.eo.settings.cfg["web_pass"] == "geheim1"


async def test_password_reset_flag(client, tmp_path):
    from energyoptimizer.__main__ import reset_password
    import os
    os.environ["EO_DATA_DIR"] = str(tmp_path)
    try:
        assert reset_password() == 0
    finally:
        del os.environ["EO_DATA_DIR"]
    client.eo.check_password_reset()
    assert client.eo.settings.cfg["web_pass"] == ""
    assert not (tmp_path / "RESET_PASSWORD").exists()
    r = await client.get("/api/status")
    assert r.status == 401 and (await r.json())["setup"] is True


async def test_status_shape(client):
    r = await client.get("/api/status")
    assert r.status == 200
    d = await r.json()
    for k in ("prod", "cons", "grid", "sl_age", "sl_next", "energy", "shelly", "ext", "cfg", "mqtt"):
        assert k in d
    assert d["cfg"]["hyst_on"] == 3
    r = await client.get("/")
    assert "/api/wizard" in await r.text()   # frisch eingerichtet: zuerst der Assistent
    r = await client.post("/api/wizard", json={"done": True})
    assert r.status == 200 and client.eo.settings.cfg["wizard_done"] is True
    r = await client.get("/")
    assert "<!DOCTYPE html>" in await r.text()
    assert r.headers["X-Frame-Options"] == "DENY"


async def test_password_login_flow(client):
    client.eo.settings.cfg["web_pass"] = "pw1"
    r = await client.get("/api/status")
    assert r.status == 401 and (await r.json()) == {"ok": False, "auth": False, "setup": False}
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
        "on_margin": "250", "hb_url": "http://hc.example.com/ping", "sl_pass": "",
    })
    d = await r.json()
    assert d["ok"] and "Heartbeat-URL nicht übernommen" in d["warn"]
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


async def test_hostname_setting(client):
    r = await client.post("/api/save", json={"hostname": "Solar-Pi.local"})
    assert (await r.json())["warn"] == ""
    assert client.eo.settings.cfg["hostname"] == "solar-pi"
    r = await client.post("/api/save", json={"hostname": "kein name!"})
    assert "Name im Netzwerk" in (await r.json())["warn"]
    assert client.eo.settings.cfg["hostname"] == "solar-pi"
    d = await (await client.get("/api/status")).json()
    assert d["cfg"]["hostname"] == "solar-pi"
    info = await (await client.get("/api/sysinfo")).json()
    assert info["version"] and "mem_total" in info and "disk_free" in info


async def test_setup_language_and_page_lang(fresh):
    r = await fresh.get("/?lang=en")
    assert '<html lang="en">' in await r.text()
    r = await fresh.post("/api/setup", json={"pw": "kurz", "lang": "en"})
    assert (await r.json())["msg"] == "At least 6 characters"
    r = await fresh.post("/api/setup", json={"pw": "geheim1", "lang": "en"})
    assert r.status == 200 and fresh.eo.settings.cfg["lang"] == "en"
    await fresh.post("/api/wizard", json={"done": True})
    r = await fresh.get("/")
    assert '<html lang="en">' in await r.text()
    r = await fresh.get("/i18n.js")
    js = await r.text()
    assert "/*EN*/" not in js and "window.T=T" in js
    r = await fresh.post("/api/save", json={"lang": "de"})
    r = await fresh.get("/")
    assert '<html lang="de">' in await r.text()


async def test_existing_install_skips_wizard(tmp_path):
    import json as _json
    (tmp_path / "settings.json").write_text(_json.dumps({"web_pass": "geheim1"}))
    from energyoptimizer.settings import SettingsStore as Settings
    st = Settings(str(tmp_path))
    st.load()
    assert st.cfg["wizard_done"] is True and st.cfg["lang"] == "de"


async def test_solarlog_test_endpoint(client):
    from aiohttp.test_utils import TestServer
    from . import mock_solarlog
    srv = TestServer(mock_solarlog.build({}))
    await srv.start_server()
    try:
        body = {"sl_ip": srv.host, "sl_port": srv.port, "sl_pass": mock_solarlog.PASSWORD}
        d = await (await client.post("/api/solarlog/test", json=body)).json()
        assert d["ok"] and d["prod"] == 5200 and d["cons"] == 1800 and d["user"] == "installer"
        body["sl_pass"] = "falsch"
        d = await (await client.post("/api/solarlog/test", json=body)).json()
        assert not d["ok"] and "Passwort" in d["msg"]
        # Nichts davon wird gespeichert.
        assert client.eo.settings.cfg["sl_ip"] != srv.host
    finally:
        await srv.close()
    d = await (await client.post("/api/solarlog/test", json={"sl_ip": "127.0.0.1", "sl_port": 1})).json()
    assert not d["ok"] and "Keine Antwort" in d["msg"]
    d = await (await client.post("/api/solarlog/test", json={"sl_ip": "bad host!"})).json()
    assert not d["ok"]
