"""Web-Oberfläche (static/) und JSON-API."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
from calendar import monthrange

from aiohttp import web

from . import __version__, sched
from .clock import CLOCK
from .const import (
    ER_MANUAL, ER_NONE, EV_AUTHFAIL, MAX_SHELLY, NS_CRIT, NS_WARN, RST_TXT,
    RUN_START_GRACE_S,
)
from .settings import MAX_PASSWORD_LEN, MIN_PASSWORD_LEN, hyst_off_ticks, hyst_on_ticks

_LOGGER = logging.getLogger(__name__)

STATIC = os.path.join(os.path.dirname(__file__), "static")
AUTH_ALERT_AFTER = 10
AUTH_ALERT_REPEAT_S = 900


def _json(fn):
    """Einfacher GET-Handler, der nur ein JSON-Dokument liefert."""
    async def handler(req):
        return web.json_response(fn())
    return handler


def _read(name: str, mode: str = "r"):
    with open(os.path.join(STATIC, name), mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


class WebApi:
    def __init__(self, eo) -> None:
        self.eo = eo
        self.index_html = _read("index.html")
        self.login_html = _read("login.html")
        self.setup_html = _read("setup.html")
        self.icon = _read("icon.png", "rb")
        self.etag = '"%s"' % hashlib.sha256(self.index_html.encode()).hexdigest()[:8]
        self.secret = self._load_secret()
        self.fails = 0
        self.last_fail = 0.0
        self.block_until = 0.0
        self.alert_t = 0.0

    # ── Anmeldung (Cookie eo_auth = SHA-256(secret ‖ passwort)[:16] hex) ─────
    def _load_secret(self) -> bytes:
        path = os.path.join(self.eo.data_dir, "auth_secret")
        try:
            with open(path, encoding="ascii") as f:
                sec = bytes.fromhex(f.read().strip())
                if len(sec) == 16:
                    return sec
        except (OSError, ValueError):
            pass
        sec = secrets.token_bytes(16)
        try:
            with open(path, "w", encoding="ascii") as f:
                f.write(sec.hex())
            os.chmod(path, 0o600)
        except OSError:
            _LOGGER.warning("[Auth] Server-Secret nicht speicherbar – Sitzung gilt nur bis zum Neustart")
        return sec

    @property
    def web_pass(self) -> str:
        return self.eo.settings.cfg["web_pass"]

    def token(self) -> str:
        return hashlib.sha256(self.secret + self.web_pass.encode()).digest()[:16].hex()

    def block_remaining(self) -> int:
        if not self.block_until:
            return 0
        rem = self.block_until - CLOCK.mono()
        if rem <= 0:
            self.block_until = 0.0
            return 0
        return int(rem + 0.999)

    def note_failure(self) -> None:
        if self.block_remaining() > 0:
            return
        now = CLOCK.mono()
        if self.last_fail and now - self.last_fail > 60:
            self.fails = 0
            self.alert_t = 0.0
        self.last_fail = now
        self.fails += 1
        wait = 5 if self.fails >= 10 else (2 if self.fails >= 5 else 0)
        if wait:
            self.block_until = now + wait
            _LOGGER.warning("[Auth] %d Fehlversuche – Passwortprüfung für %ds gesperrt", self.fails, wait)
        if self.fails >= AUTH_ALERT_AFTER and (not self.alert_t or now - self.alert_t >= AUTH_ALERT_REPEAT_S):
            self.alert_t = now
            self.eo.events.log(EV_AUTHFAIL, -1, ER_NONE, True, f"{self.fails} Fehlversuche")
            self.eo.notify.send(NS_WARN, "Wiederholte Fehlanmeldungen",
                                f"{self.fails} fehlgeschlagene Anmeldeversuche am Web-Interface. "
                                "Falls das nicht du warst: Passwort ändern und prüfen, wer im Netz ist.",
                                "lock")

    def note_success(self) -> None:
        self.fails = 0
        self.last_fail = 0.0
        self.block_until = 0.0
        self.alert_t = 0.0

    @property
    def setup_required(self) -> bool:
        """Kein Passwort gesetzt (Erststart oder nach reset-password): nur die Einrichtung ist offen."""
        return not self.web_pass

    def is_authed(self, req: web.Request) -> bool:
        if self.setup_required:
            return False
        ck = req.cookies.get("eo_auth", "").strip()
        if ck and hmac.compare_digest(ck, self.token()):
            return True
        auth = req.headers.get("Authorization")
        if auth:
            if self.block_remaining() > 0:
                return False
            if auth.startswith("Basic "):
                import base64
                try:
                    user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                except (ValueError, UnicodeDecodeError):
                    user, pw = "", ""
                if user == "admin" and hmac.compare_digest(pw.encode(), self.web_pass.encode()):
                    return True
            self.note_failure()
            return False
        if ck:
            self.note_failure()
        return False

    @staticmethod
    def check_origin(req: web.Request) -> bool:
        """CSRF-Schutz: Origin/Referer muss auf denselben Host zeigen wie die Anfrage."""
        val = req.headers.get("Origin") or req.headers.get("Referer")
        if not val:
            return True
        if "://" in val:
            val = val.split("://", 1)[1]
        val = val.split("/", 1)[0]
        own = req.headers.get("X-Forwarded-Host") or req.host or ""
        return val.lower() == own.lower() or val.split(":")[0].lower() == own.split(":")[0].lower()

    # ── Middleware ──────────────────────────────────────────────────────────
    @web.middleware
    async def middleware(self, req: web.Request, handler):
        path = req.path
        open_paths = ("/", "/api/login", "/api/logout", "/api/setup", "/apple-touch-icon.png",
                      "/apple-touch-icon-precomposed.png", "/favicon.ico")
        if path.startswith("/api/") and path not in open_paths:
            if not self.is_authed(req):
                resp = web.json_response({"ok": False, "auth": False, "setup": self.setup_required},
                                         status=401)
                return self._headers(resp)
            if req.method == "POST" and not self.check_origin(req):
                return self._headers(web.json_response({"ok": False, "msg": "Origin"}, status=403))
        try:
            resp = await handler(req)
        except web.HTTPException as exc:
            resp = exc
        return self._headers(resp)

    @staticmethod
    def _headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    # ── Routen ──────────────────────────────────────────────────────────────
    def build(self) -> web.Application:
        app = web.Application(middlewares=[self.middleware], client_max_size=16 * 1024 * 1024)
        r = app.router
        for p in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/favicon.ico"):
            r.add_get(p, self.h_icon)
        r.add_get("/", self.h_index)
        r.add_get("/api/status", self.h_status)
        r.add_post("/api/login", self.h_login)
        r.add_post("/api/setup", self.h_setup)
        r.add_post("/api/logout", self.h_logout)
        r.add_get("/api/history/stats", _json(lambda: self.eo.history.stats()))
        r.add_get("/api/history/export", self.h_hist_export)
        r.add_post("/api/history/clear", self.h_hist_clear)
        r.add_get("/api/history", self.h_history)
        r.add_get("/api/energy", self.h_energy)
        r.add_get("/api/history_daily", _json(lambda: self.eo.history.daily_json()))
        r.add_post("/api/history_daily_import", self.h_daily_import)
        r.add_post("/api/refresh", self.h_refresh)
        r.add_post("/api/save", self.h_save)
        r.add_post(r"/api/shelly/{idx:\d+}/{cmd:(on|off|autoon|autooff)}", self.h_cmd)
        r.add_post(r"/api/ext/{idx:\d+}/{cmd:(on|off|autoon|autooff)}", self.h_cmd)
        r.add_post("/api/discover", self.h_discover_start)
        r.add_get("/api/discover", self.h_discover)
        r.add_get("/api/sysinfo", self.h_sysinfo)
        r.add_get("/api/events/export", self.h_events_export)
        r.add_get("/api/events", self.h_events)
        r.add_post("/api/events/clear", self.h_events_clear)
        r.add_get("/api/ideas", _json(lambda: self.eo.ideas.build()))
        r.add_post("/api/ideas", self.h_ideas_post)
        r.add_post("/api/ideas/delete", self.h_ideas_delete)
        r.add_get("/api/ideas/export", self.h_ideas_export)
        r.add_get("/api/alarms", _json(lambda: self.eo.alarms.build()))
        r.add_post("/api/alarms/ack", self.h_alarm_ack)
        r.add_post("/api/notify/test", self.h_notify_test)
        r.add_post("/api/selftest", self.h_selftest_start)
        r.add_get("/api/selftest", _json(lambda: self.eo.selftest_json()))
        r.add_get("/api/config/export", self.h_config_export)
        r.add_post("/api/config/import", self.h_config_import)
        r.add_get("/api/solarlog/raw", self.h_sl_raw)
        r.add_post("/api/restart", self.h_restart)
        return app

    async def h_icon(self, req):
        return web.Response(body=self.icon, content_type="image/png",
                            headers={"Cache-Control": "public, max-age=604800"})

    async def h_index(self, req):
        if self.setup_required:
            return web.Response(text=self.setup_html, content_type="text/html",
                                headers={"Cache-Control": "no-store"})
        if not self.is_authed(req):
            return web.Response(text=self.login_html, content_type="text/html")
        if req.headers.get("If-None-Match") == self.etag:
            return web.Response(status=304)
        return web.Response(text=self.index_html, content_type="text/html",
                            headers={"Cache-Control": "no-cache", "ETag": self.etag})

    async def _json_body(self, req) -> dict | None:
        try:
            doc = json.loads(await req.text())
        except (ValueError, UnicodeDecodeError):
            return None
        return doc if isinstance(doc, dict) else None

    async def h_login(self, req):
        if not self.check_origin(req):
            return web.json_response({"ok": False}, status=403)
        wait = self.block_remaining()
        if wait:
            return web.json_response({"ok": False, "wait": wait}, status=429,
                                     headers={"Retry-After": str(wait)})
        doc = await self._json_body(req)
        if doc is None:
            return web.json_response({"ok": False}, status=400)
        if self.setup_required:
            return web.json_response({"ok": False, "setup": True}, status=409)
        pw = doc.get("pw") if isinstance(doc.get("pw"), str) else ""
        if not hmac.compare_digest(pw.encode(), self.web_pass.encode()):
            self.note_failure()
            w = self.block_remaining()
            return web.json_response({"ok": False, "wait": w} if w else {"ok": False}, status=401)
        self.note_success()
        return self._with_auth_cookie(web.json_response({"ok": True}))

    def _with_auth_cookie(self, resp):
        resp.headers["Set-Cookie"] = (f"eo_auth={self.token()}; Path=/; Max-Age=31536000; "
                                      "SameSite=Lax; HttpOnly")
        return resp

    async def h_setup(self, req):
        """Erststart: das erste Passwort festlegen. Danach ist dieser Endpunkt gesperrt."""
        if not self.check_origin(req):
            return web.json_response({"ok": False}, status=403)
        if not self.setup_required:
            return web.json_response({"ok": False, "done": True}, status=409)
        doc = await self._json_body(req)
        pw = doc.get("pw") if isinstance(doc, dict) and isinstance(doc.get("pw"), str) else ""
        if len(pw) < MIN_PASSWORD_LEN:
            return web.json_response(
                {"ok": False, "msg": f"Mindestens {MIN_PASSWORD_LEN} Zeichen"}, status=400)
        if len(pw) > MAX_PASSWORD_LEN:
            return web.json_response(
                {"ok": False, "msg": f"Höchstens {MAX_PASSWORD_LEN} Zeichen"}, status=400)
        self.eo.set_web_password(pw)
        self.note_success()
        return self._with_auth_cookie(web.json_response({"ok": True}))

    async def h_logout(self, req):
        if not self.check_origin(req):
            return web.json_response({"ok": False}, status=403)
        resp = web.json_response({"ok": True})
        resp.headers["Set-Cookie"] = "eo_auth=; Path=/; Max-Age=0; SameSite=Lax; HttpOnly"
        return resp

    # ── Status ──────────────────────────────────────────────────────────────
    async def h_status(self, req):
        return web.json_response(self.status())

    def status(self) -> dict:
        eo = self.eo
        c = eo.settings.cfg
        dv = eo.devices
        now = CLOCK.mono()
        dt = CLOCK.now()
        sd = eo.history.solar_avg(c["sl_avg_s"])
        doc: dict = {"prod": sd.production_w, "cons": sd.consumption_w, "grid": sd.grid_w}
        if sd.has_battery:
            doc["batt"] = sd.battery_w
            doc["soc"] = sd.battery_soc
        doc["avg_s"] = c["sl_avg_s"]
        doc["sl_age"] = int(now - eo.history.live_t) if eo.history.live_t else -1
        doc["sl_next"] = max(0, int(eo.next_solar - now)) if eo.next_solar else -1
        doc["failsafe"] = dv.failsafe_active()
        doc["battblk"] = dv.batt_block
        doc["al_n"] = eo.alarms.active_count()
        doc["al_sev"] = eo.alarms.max_severity()
        en = eo.energy.c
        doc["energy"] = {
            "dp": en["day_prod_wh"] / 1000, "dc": en["day_cons_wh"] / 1000,
            "dgi": en["day_grid_in_wh"] / 1000, "dgo": en["day_grid_out_wh"] / 1000,
            "tp": en["total_prod_wh"] / 1000, "tc": en["total_cons_wh"] / 1000,
            "tgi": en["total_grid_in_wh"] / 1000, "tgo": en["total_grid_out_wh"] / 1000,
        }
        if c["p_buy"] > 0 or c["p_feed"] > 0 or c["p_base"] > 0:
            self_kwh = max(0.0, (en["day_prod_wh"] - en["day_grid_out_wh"]) / 1000)
            doc["cost"] = {
                "buy": en["day_grid_in_wh"] / 1000 * c["p_buy"],
                "sell": en["day_grid_out_wh"] / 1000 * c["p_feed"],
                "saved": self_kwh * c["p_buy"],
                "base": c["p_base"] / monthrange(dt.year, dt.month)[1],
            }
        min_on, min_off = c["min_on_min"] * 60, c["min_off_min"] * 60

        def lock_s(e: dict, s) -> int:
            if not e["auto"]:
                return 0
            if s.on and s.last_on and now - s.last_on < min_on:
                return int(min_on - (now - s.last_on))
            if not s.on and s.last_off and now - s.last_off < min_off:
                return int(min_off - (now - s.last_off))
            return 0

        def sched_fields(o: dict, e: dict, s) -> None:
            act = sched.active(e["sch"], dt)
            o["sch"] = s.sched_on
            o["schp"] = sched.any_used(e["sch"])
            o["schr"] = sched.ends_in_min(e["sch"][act], dt) if act >= 0 else -1
            o["schn"] = sched.next_start_min(e["sch"], dt)
            o["schs"] = s.sched_skip >= 0 and s.sched_skip == act

        def ov(s) -> int:
            return max(0, int(s.override_until - now)) if s.override_until else 0

        sh = []
        for i in range(c["sh_count"]):
            e = c["shelly"][i]
            if not e["ip"]:
                continue
            s = dv.st["shelly"][i]
            o = {
                "idx": i, "name": dv.display_name("shelly", i), "id": e["id"], "pw": e["pw"],
                "pri": e["pri"], "auto": e["auto"], "on": s.on, "reach": s.reachable,
                "apower": int(round(s.apower_w)), "volt": int(round(s.voltage_v)),
                "amp": round(s.current_a, 2), "temp": round(s.temp_c, 1),
                "rt_min": e["rt"], "rt_on": s.today_on_s // 60, "forced": s.forced_on,
                "mx_min": e["mx"], "cap": dv.daycap_reached("shelly", i),
            }
            run_state = 0
            if e["rw"] > 0:
                if not s.on or not s.reachable:
                    run_state = 0
                elif s.running:
                    run_state = 1
                elif s.last_on and now - s.last_on < RUN_START_GRACE_S:
                    run_state = 3
                else:
                    run_state = 2
                o["rw"] = e["rw"]
                o["io"] = e["io"]
                o["idle"] = int(now - s.idle_since) if s.idle_since else 0
                o["nd"] = int(s.nd_retry - now) if s.nd_retry and now < s.nd_retry else 0
            o["run"] = run_state
            o["ping"] = int(now - s.last_poll) if s.last_poll else -1
            o["e_day"] = en["day_shelly_wh"][i] / 1000
            o["e_tot"] = en["total_shelly_wh"][i] / 1000
            o["pv_day"] = en["day_shelly_pv_wh"][i] / 1000
            o["pv_tot"] = en["total_shelly_pv_wh"][i] / 1000
            o["on_day"] = en["day_on_s"][i]
            o["on_tot"] = en["total_on_s"][i]
            o["sw"] = en["switch_cnt"][i]
            o["ot"] = s.on_ticks
            o["ft"] = s.off_ticks
            o["ov"] = ov(s)
            o["ova"] = s.override_ret_auto
            o["win"] = dv.window_open("shelly", i)
            sched_fields(o, e, s)
            o["lock"] = lock_s(e, s)
            sh.append(o)
        doc["shelly"] = sh

        ex = []
        for i in range(c["ex_count"]):
            e = c["ext"][i]
            s = dv.st["ext"][i]
            o = {
                "idx": i, "name": e["name"] or "Extern", "pw": e["pw"], "pri": e["pri"],
                "auto": e["auto"], "on": s.on, "rt_min": e["rt"], "rt_on": s.today_on_s // 60,
                "forced": s.forced_on, "mx_min": e["mx"], "cap": dv.daycap_reached("ext", i),
                "ot": s.on_ticks, "ft": s.off_ticks, "ov": ov(s), "ova": s.override_ret_auto,
                "win": dv.window_open("ext", i),
            }
            sched_fields(o, e, s)
            o["lock"] = lock_s(e, s)
            ex.append(o)
        doc["ext"] = ex
        doc["led"] = "scan" if dv.scan.running else ("connected" if eo.sl_ok else "disconnected")
        doc["mqtt"] = {"conn": eo.mqtt.connected}
        up = eo.updates
        doc["update"] = {"available": up.available, "latest": up.latest, "url": up.url}

        cf = {k: c[k] for k in ("sl_ip", "sl_port", "sl_user", "sl_fprod", "sl_fcons", "sl_fgrid",
                                 "sl_fyday", "sl_fcday", "sl_fytot", "sl_fctot", "sl_fsoc", "sl_fbatt",
                                 "sl_poll_min", "sl_avg_s", "on_margin", "off_margin", "hyst_on_s",
                                 "hyst_off_s", "min_on_min", "min_off_min", "fw_start", "fw_end",
                                 "sl_fsafe", "batt_grd", "sh_count", "p_buy", "p_feed", "p_base",
                                 "nt_en", "nt_srv", "nt_sev", "nt_qs", "nt_qe", "hb_en", "hb_min",
                                 "mo_sl", "mo_dev", "mo_np", "mo_inv", "sl_dev", "lat", "lon",
                                 "mq_en", "mq_host", "mq_port", "mq_user", "mq_disc")}
        cf["hostname"] = c["hostname"]
        cf["hyst_on"] = hyst_on_ticks(c)
        cf["hyst_off"] = hyst_off_ticks(c)
        cf["ip"] = req_host_ip()
        cf["nt_top_set"] = bool(c["nt_top"])
        cf["hb_url_set"] = bool(c["hb_url"])
        cf["mq_pfx"] = c["mq_pfx"]
        cf["shelly"] = [
            {"name": e["name"], "ip": e["ip"], "id": e["id"], "pw": e["pw"], "pri": e["pri"],
             "auto": e["auto"], "rt": e["rt"], "mx": e["mx"], "rw": e["rw"], "io": e["io"],
             "ws": e["ws"], "we": e["we"], "wd": e["wd"], "sch": sched.to_str(e["sch"])}
            for e in c["shelly"][: c["sh_count"]]
        ]
        cf["ext"] = [
            {"name": e["name"], "pw": e["pw"], "pri": e["pri"], "auto": e["auto"], "rt": e["rt"],
             "mx": e["mx"], "ws": e["ws"], "we": e["we"], "wd": e["wd"], "sch": sched.to_str(e["sch"])}
            for e in c["ext"][: c["ex_count"]]
        ]
        doc["cfg"] = cf
        return doc

    # ── Verlauf ─────────────────────────────────────────────────────────────
    async def h_history(self, req):
        dv = self.eo.devices
        names = [dv.display_name("shelly", i) for i in range(MAX_SHELLY)]
        return web.json_response(self.eo.history.build(self.eo.settings.cfg, names))

    async def h_hist_export(self, req):
        h = self.eo.history
        if not h.datalog_exists():
            return web.json_response({"ok": False, "msg": "Kein Verlauf vorhanden"}, status=404)
        return web.FileResponse(h.datalog_path, headers={
            "Content-Type": "text/csv", "Content-Disposition": 'attachment; filename="hist.csv"'})

    async def h_hist_clear(self, req):
        self.eo.history.datalog_clear()
        return web.json_response({"ok": True})

    async def h_energy(self, req):
        rng = {"year": 3, "month": 2}.get(req.query.get("range", ""), 1)
        return web.json_response(self.eo.history.energy(rng))

    async def h_daily_import(self, req):
        force = req.query.get("force", "0") != "0"
        text = None
        try:
            if req.content_type.startswith("multipart/"):
                reader = await req.multipart()
                async for part in reader:
                    if part.filename is not None or part.name in ("file", "csv"):
                        text = (await part.read(decode=True)).decode("utf-8", errors="replace")
                        break
            else:
                text = await req.text()
        except (ValueError, UnicodeDecodeError):
            text = None
        if text is None:
            return web.json_response({"ok": False, "msg": "Keine Datei empfangen"}, status=500)
        ok, msg = self.eo.history.daily_import(text, force)
        return web.json_response({"ok": ok, "msg": msg.replace('"', "'")}, status=200 if ok else 500)

    # ── Steuerung ───────────────────────────────────────────────────────────
    async def h_refresh(self, req):
        self.eo.solar_refresh_req = True
        return web.json_response({"ok": True})

    async def h_save(self, req):
        doc = await self._json_body(req)
        if doc is None:
            return web.json_response({"ok": False, "msg": "JSON Fehler"}, status=400)
        warn = self.eo.apply_settings(doc)
        return web.json_response({"ok": True, "reboot": False, "warn": warn.replace('"', "'")})

    async def h_cmd(self, req):
        kind = "shelly" if req.path.startswith("/api/shelly/") else "ext"
        try:
            mins = int(req.query.get("min", "0") or 0)
        except ValueError:
            mins = 0
        if not self.eo.devices.apply_command(kind, int(req.match_info["idx"]), req.match_info["cmd"],
                                             ER_MANUAL, mins):
            return web.json_response({"ok": False}, status=404)
        return web.json_response({"ok": True})

    async def h_discover_start(self, req):
        self.eo.devices.start_scan()
        return web.json_response({"ok": True})

    async def h_discover(self, req):
        sc = self.eo.devices.scan
        return web.json_response({
            "running": sc.running, "done": sc.done, "count": len(sc.found),
            "devices": [{"ip": f.ip, "name": f.name, "model": f.model, "id": f.id} for f in sc.found],
        })

    # ── System ──────────────────────────────────────────────────────────────
    async def h_sysinfo(self, req):
        eo = self.eo
        d = eo.sysinfo.get()
        host = eo.settings.cfg["hostname"]
        d.update({
            "save_err": eo.settings.save_error, "boots": eo.boots, "rst_txt": RST_TXT,
            "version": __version__, "build": os.environ.get("EO_BUILD", "dev"),
            "ip": req_host_ip(), "port": eo.port,
            "mdns": f"{host}.local" if eo.mdns.enabled else "", "mdns_err": eo.mdns.error,
            "notify": eo.notify.state(), "update": eo.updates.state(),
        })
        return web.json_response(d)

    async def h_restart(self, req):
        # Im Container: sauber beenden, Docker (restart: unless-stopped) startet neu.
        self.eo.flush()
        asyncio.get_running_loop().call_later(0.3, lambda: os.kill(os.getpid(), 15))
        return web.json_response({"ok": True})

    # ── Protokoll, Notizen, Alarme ──────────────────────────────────────────
    async def h_events(self, req):
        try:
            n = max(1, int(req.query.get("n", "120")))
        except ValueError:
            n = 1
        d = req.query.get("dev")
        if d is None:
            flt = -2
        elif d == "sys":
            flt = -1
        else:
            try:
                flt = int(d)
            except ValueError:
                flt = 0
        return web.json_response(self.eo.events.build(n, flt))

    async def h_events_export(self, req):
        return web.Response(text=self.eo.events.csv(), content_type="text/csv",
                            headers={"Content-Disposition": 'attachment; filename="events.csv"'})

    async def h_events_clear(self, req):
        return web.json_response({"ok": self.eo.events.clear()})

    async def h_ideas_post(self, req):
        doc = await self._json_body(req)
        if doc is None:
            return web.json_response({"ok": False, "msg": "JSON Fehler"}, status=400)
        ok, iid, err, nf = self.eo.ideas.upsert(doc)
        if not ok:
            return web.json_response({"ok": False, "msg": err.replace('"', "'")}, status=404 if nf else 400)
        return web.json_response({"ok": True, "id": iid})

    async def h_ideas_delete(self, req):
        try:
            iid = int(req.query.get("id", "0"))
        except ValueError:
            iid = 0
        if not self.eo.ideas.delete(iid):
            return web.json_response({"ok": False, "msg": "Notiz nicht gefunden"}, status=404)
        return web.json_response({"ok": True})

    async def h_ideas_export(self, req):
        return web.Response(text=self.eo.ideas.markdown(), content_type="text/markdown", charset="utf-8",
                            headers={"Content-Disposition": 'attachment; filename="verbesserungen.md"'})

    async def h_alarm_ack(self, req):
        try:
            i = int(req.query.get("id", "-1"))
        except ValueError:
            i = -1
        self.eo.alarms.ack(i)
        return web.json_response({"ok": True})

    async def h_notify_test(self, req):
        c = self.eo.settings.cfg
        if not c["nt_en"] or not c["nt_top"]:
            return web.json_response({"ok": False, "msg": "Benachrichtigungen sind nicht aktiviert oder "
                                                          "es fehlt der Topic."})
        self.eo.notify.send(NS_CRIT, "EnergyOptimizer – Testmeldung",
                            "Wenn diese Meldung ankommt, funktioniert die Alarmierung.", "bell")
        return web.json_response({"ok": True, "msg": "Testmeldung eingereiht – Ergebnis erscheint gleich hier."})

    async def h_selftest_start(self, req):
        self.eo.request_selftest()
        return web.json_response({"ok": True})

    # ── Sicherung ───────────────────────────────────────────────────────────
    async def h_config_export(self, req):
        body = json.dumps(self.eo.settings.export(), ensure_ascii=False, indent=2)
        return web.Response(text=body, content_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="energyoptimizer-config.json"'})

    async def h_config_import(self, req):
        doc = await self._json_body(req)
        if doc is None:
            return web.json_response({"ok": False, "msg": "Datei ist kein gültiges JSON"}, status=400)
        if not isinstance(doc.get("eo_config"), int) or isinstance(doc.get("eo_config"), bool):
            return web.json_response({"ok": False, "msg": "Das ist keine EnergyOptimizer-Sicherung"},
                                     status=400)
        warn = self.eo.apply_settings(doc, "Import")
        msg = "Einstellungen übernommen. Passwörter, ntfy-Topic und Heartbeat-URL bleiben unverändert."
        if warn:
            msg += " " + warn
        return web.json_response({"ok": True, "msg": msg.replace('"', "'")})

    async def h_sl_raw(self, req):
        eo = self.eo
        d = eo.devs
        devs = []
        for i in range(d.count):
            x = d.d[i]
            if not x.present:
                continue
            o = {"i": i, "name": x.name, "st": x.status, "max": int(round(x.seen_max_w))}
            if x.has_power:
                o["w"] = int(round(x.power_w))
            devs.append(o)
        main = eo.reader.raw_main
        if eo.reader.last_error:
            main = f"[Fehler] {eo.reader.last_error}\n" + main
        client = eo.reader._client
        if client is not None and client.login_trace:
            main += "\n[Login] " + json.dumps(client.login_report(), ensure_ascii=False)
        return web.json_response({
            "main": main, "dev": eo.reader.raw_dev,
            "age": int(CLOCK.mono() - eo.devs_t) if eo.devs_t else -1, "devs": devs,
        })


_host_ip = ("", 0.0)


def req_host_ip() -> str:
    """LAN-Adresse, wie sie die Einstellungsseite als "IP" anzeigt (1 min gecacht)."""
    global _host_ip
    from .devices import local_ipv4
    if os.environ.get("EO_HOST_IP"):
        return os.environ["EO_HOST_IP"]
    if not _host_ip[0] or CLOCK.mono() - _host_ip[1] > 60:
        _host_ip = (local_ipv4(), CLOCK.mono())
    return _host_ip[0]
