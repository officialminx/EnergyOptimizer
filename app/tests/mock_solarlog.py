"""Nachbau des Solar-Log-Verhaltens, wie ha-advanced-solarlog es erwartet.

* /getjp nimmt nur text/html mit X-SL-CSRF-PROTECTION an (application/json → 400,
  so scheitert eine Abfrage mit Basic-Auth),
* 801/170 ist offen, 858/740/782 verlangen eine Sitzung,
* /login kennt nur das Konto "installer" und verlangt das Passwort bcrypt-gehasht.
"""

from __future__ import annotations

import json

import bcrypt
from aiohttp import web

PASSWORD = "geheim"
SALT = bcrypt.gensalt(rounds=4).decode()
HASHED = bcrypt.hashpw(PASSWORD.encode(), SALT.encode()).decode()
COOKIE = "sess123"

BASIC = {"100": "23.09.26 12:00:00", "101": 5200, "110": "1800", "105": 12345, "111": 8000,
         "109": 99000000, "115": 45000000}


def build(state: dict) -> web.Application:
    state.setdefault("logins", [])
    state.setdefault("getjp_bodies", [])

    async def login(req: web.Request) -> web.Response:
        body = await req.text()
        state["logins"].append(body)
        params = dict(p.split("=", 1) for p in body.split("&"))
        if params.get("u") != "installer":
            return web.Response(text="FAILED - User was wrong")
        if params.get("p") != HASHED:
            return web.Response(text="FAILED - Password was wrong")
        state["expire"] = False  # neue Sitzung
        resp = web.Response(text="SUCCESS - Password was correct, you are now logged in")
        resp.set_cookie("SolarLog", COOKIE)
        return resp

    async def getjp(req: web.Request) -> web.Response:
        if req.headers.get("Content-Type") != "text/html" or req.headers.get("X-SL-CSRF-PROTECTION") != "1":
            return web.Response(status=400, text="bad request")
        body = await req.text()
        state["getjp_bodies"].append(body)
        authed = req.headers.get("Cookie") == f"SolarLog={COOKIE}" and not state.get("expire")
        if body.startswith("token="):
            body = body.split("; ", 1)[1]
        q = json.loads(body)
        if "550" in q:
            return web.Response(text=json.dumps({"550": {"100": "ACCESS DENIED", "104": SALT}}))
        out = {}
        for key in q:
            if key == "801":
                out["801"] = {"170": BASIC}
            elif not authed:
                return web.Response(text=json.dumps({key: "ACCESS DENIED"}))
            elif key == "858":
                out["858"] = [52, 81, 0, 400]
            elif key == "740":
                out["740"] = {"0": "SN1", "1": "SN2", "2": "Err"}
            elif key == "608":
                out["608"] = {"0": " Power", "1": "Power"}
            elif key == "782":
                out["782"] = {"0": "3000", "1": "2200"}
            elif key == "141":
                idx = next(iter(q["141"]))
                out["141"] = {idx: {"119": f"WR {int(idx) + 1}"}}
        return web.Response(text=json.dumps(out))

    app = web.Application()
    app.router.add_post("/login", login)
    app.router.add_post("/getjp", getjp)
    return app
