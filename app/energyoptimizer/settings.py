"""Einstellungen.

Gespeichert wird als JSON in <data>/settings.json. Die Schlüssel entsprechen den
Feldnamen der Web-API, damit exportierte Sicherungen ("energyoptimizer-config.json")
direkt wieder importiert werden können.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import logging
import os
import re
from typing import Any

from . import sched
from .const import MAX_EXT, MAX_SHELLY
from .i18n import tr
from .passwords import hash_password, is_hash

_LOGGER = logging.getLogger(__name__)

DEFAULT_HOSTNAME = "energyoptimizer"
LANGS = ("de", "en")
MIN_PASSWORD_LEN = 6
MAX_PASSWORD_LEN = 64
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_hostname(name: str) -> str | None:
    """Name im Netzwerk (ohne ".local"); None, wenn er kein gültiger DNS-Name ist."""
    n = name.strip().lower()
    if n.endswith(".local"):
        n = n[:-6]
    return n if _HOST_LABEL.match(n) else None


def _shelly_default(i: int) -> dict[str, Any]:
    return {
        "name": "", "ip": "", "id": "", "pw": 0, "pri": i + 1, "auto": False,
        "rt": 0, "mx": 0, "rw": 0, "io": 0, "ws": 0, "we": 24, "wd": 0x7F,
        "sch": sched.empty(),
    }


def _ext_default(i: int) -> dict[str, Any]:
    return {
        "name": "", "pw": 0, "pri": i + 1, "auto": False, "rt": 0, "mx": 0,
        "ws": 0, "we": 24, "wd": 0x7F, "sch": sched.empty(),
    }


def defaults() -> dict[str, Any]:
    return {
        "web_pass": "", "hostname": DEFAULT_HOSTNAME, "lang": "de", "wizard_done": False,
        "sl_ip": "192.168.0.81", "sl_port": 80, "sl_user": "", "sl_pass": "",
        "sl_fprod": "101", "sl_fcons": "110", "sl_fgrid": "",
        "sl_fyday": "105", "sl_fcday": "111", "sl_fytot": "109", "sl_fctot": "115",
        "sl_fsoc": "858", "sl_fbatt": "858",
        "sl_poll_min": 1, "sl_avg_s": 300,
        "on_margin": 150, "off_margin": 200, "hyst_on_s": 180, "hyst_off_s": 180,
        "min_on_min": 7, "min_off_min": 5, "fw_start": 20, "fw_end": 24,
        "sl_fsafe": 30, "batt_grd": 100,
        "p_buy": 0, "p_feed": 0, "p_base": 0,
        "hb_en": False, "hb_url": "", "hb_min": 15,
        "mo_sl": 15, "mo_dev": 15, "mo_np": 45, "mo_inv": 30,
        "lat": 47.05, "lon": 8.31, "sl_dev": False,
        "mq_en": False, "mq_host": "", "mq_port": 1883, "mq_user": "", "mq_pass": "",
        "mq_pfx": "energyoptimizer", "mq_disc": True,
        "auto_en": True,
        "sh_count": 1, "shelly": [_shelly_default(i) for i in range(MAX_SHELLY)],
        "ex_count": 0, "ext": [_ext_default(i) for i in range(MAX_EXT)],
    }


def hyst_ticks(secs: int, poll_min: int) -> int:
    poll_ms = max(1000, int(poll_min) * 60000)
    need = (int(secs) * 1000 + poll_ms - 1) // poll_ms
    return max(1, min(240, need))


def hyst_on_ticks(c: dict) -> int:
    return hyst_ticks(c["hyst_on_s"], c["sl_poll_min"])


def hyst_off_ticks(c: dict) -> int:
    return hyst_ticks(c["hyst_off_s"], c["sl_poll_min"])


# ── Hilfen für die Validierung ──────────────────────────────────────────────

def ip_looks_valid(s: Any) -> bool:
    if not isinstance(s, str) or not s:
        return False
    parts = s.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or len(p) > 3 or int(p) > 255:
            return False
    return True


def host_looks_valid(s: Any) -> bool:
    """IP-Adresse oder Hostname (im Container ist ein DNS-Name für den Solar-Log praktisch)."""
    if ip_looks_valid(s):
        return True
    if not isinstance(s, str) or not s or len(s) > 63:
        return False
    return all(c.isalnum() or c in "-." for c in s) and not s.startswith(("-", "."))


def host_is_local(host: str) -> bool:
    host = host.lower()
    if not host:
        return False
    if ip_looks_valid(host):
        return ipaddress.ip_address(host).is_private or host.startswith("169.254.")
    if "." not in host:
        return True
    return host.endswith((".local", ".lan", ".home", ".internal", ".fritz.box"))


def url_is_https_ok(url: Any) -> tuple[bool, str]:
    if not isinstance(url, str) or not url:
        return False, "leer"
    low = url.lower()
    https = low.startswith("https://")
    http = low.startswith("http://")
    if not https and not http:
        return False, "muss mit https:// beginnen"
    host = url[8 if https else 7:]
    host = host.split("/", 1)[0]
    if "@" in host:
        host = host.split("@", 1)[1]
    host = host.split(":", 1)[0]
    if not host:
        return False, "kein Hostname angegeben"
    if https or host_is_local(host):
        return True, ""
    return False, "http:// nur für Adressen im eigenen Netz, sonst https:// verwenden"


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _as_int(v: Any) -> int:
    """Zahlen direkt, Zahl-Strings geparst, sonst 0."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        try:
            return int(float(v.strip()))
        except ValueError:
            return 0
    return 0


def _as_float(v: Any) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return 0.0
    return 0.0


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _truthy(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v in ("true", "on", "1")
    return None


class SettingsStore:
    def __init__(self, data_dir: str) -> None:
        self.path = os.path.join(data_dir, "settings.json")
        self.cfg: dict[str, Any] = defaults()
        self.save_error = False
        self.first_start = False

    # ── Laden / Speichern ───────────────────────────────────────────────────
    def load(self) -> None:
        base = defaults()
        try:
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
        except FileNotFoundError:
            self.first_start = True
            self._apply_env_bootstrap(base)
            self.cfg = base
            self.save()
            return
        except (OSError, ValueError) as err:
            _LOGGER.error("Einstellungen nicht lesbar (%s) – starte mit Standardwerten", err)
            self.cfg = base
            return
        for k, v in stored.items():
            if k in ("shelly", "ext"):
                continue
            if k in base:
                base[k] = v
        if "wizard_done" not in stored:
            # Installation from before the setup wizard existed: already set up.
            base["wizard_done"] = True
        for key, dflt in (("shelly", _shelly_default), ("ext", _ext_default)):
            items = stored.get(key) or []
            for i in range(len(base[key])):
                if i < len(items) and isinstance(items[i], dict):
                    entry = dflt(i)
                    entry.update({k: v for k, v in items[i].items() if k in entry})
                    if isinstance(entry["sch"], str):
                        entry["sch"] = sched.from_str(entry["sch"])
                    base[key][i] = entry
        self.cfg = base
        if not isinstance(base["web_pass"], str):
            base["web_pass"] = ""
        if base["web_pass"] and not is_hash(base["web_pass"]):
            # Older installs kept the password in plain text: replace it with its hash.
            base["web_pass"] = hash_password(base["web_pass"])
            self.save()

    def _apply_env_bootstrap(self, c: dict) -> None:
        """Erststart: Werte aus Umgebungsvariablen übernehmen (docker-compose)."""
        env = os.environ
        if env.get("EO_SOLARLOG_HOST"):
            c["sl_ip"] = env["EO_SOLARLOG_HOST"]
        if env.get("EO_SOLARLOG_PORT", "").isdigit():
            c["sl_port"] = int(env["EO_SOLARLOG_PORT"])
        if env.get("EO_SOLARLOG_PASSWORD"):
            c["sl_pass"] = env["EO_SOLARLOG_PASSWORD"]
        if env.get("EO_WEB_PASSWORD"):
            c["web_pass"] = hash_password(env["EO_WEB_PASSWORD"])
        host = normalize_hostname(env.get("EO_HOSTNAME", ""))
        if host:
            c["hostname"] = host

    def save(self) -> bool:
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            self.save_error = False
            return True
        except OSError as err:
            _LOGGER.error("Einstellungen konnten nicht gespeichert werden: %s", err)
            self.save_error = True
            return False

    # ── applyAndSave ────────────────────────────────────────────────────────
    def apply(self, doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Übernimmt ein Formular/Import-Dokument. Rückgabe: (alt, neu, Warnungen)."""
        warn: list[str] = []
        lang = doc["lang"] if doc.get("lang") in LANGS else self.cfg.get("lang", "de")

        def note(feld: str, why: str) -> None:
            warn.append(f"{tr(feld, lang)}: {tr(why, lang)}.")

        orig = copy.deepcopy(self.cfg)
        t = copy.deepcopy(self.cfg)

        def has(k: str) -> bool:
            return k in doc and doc[k] is not None

        def nonempty_str(k: str) -> bool:
            return _is_str(doc.get(k)) and len(doc[k]) > 0

        if nonempty_str("web_pass"):
            if MIN_PASSWORD_LEN <= len(doc["web_pass"]) <= MAX_PASSWORD_LEN:
                t["web_pass"] = hash_password(doc["web_pass"])
            else:
                note("Web-Passwort nicht geändert",
                     tr("{a} bis {b} Zeichen erforderlich", lang, a=MIN_PASSWORD_LEN, b=MAX_PASSWORD_LEN))
        if _is_str(doc.get("hostname")):
            host = normalize_hostname(doc["hostname"])
            if host:
                t["hostname"] = host
            else:
                note("Name im Netzwerk nicht übernommen",
                     "nur Kleinbuchstaben, Ziffern und Bindestriche, höchstens 63 Zeichen")
        if _is_str(doc.get("lang")) and doc["lang"] in LANGS:
            t["lang"] = doc["lang"]
        if _is_str(doc.get("sl_ip")) and host_looks_valid(doc["sl_ip"]):
            t["sl_ip"] = doc["sl_ip"]
        if has("sl_port"):
            t["sl_port"] = _as_int(doc["sl_port"])
        if not 1 <= t["sl_port"] <= 65535:
            t["sl_port"] = 80
        if _is_str(doc.get("sl_user")):
            t["sl_user"] = doc["sl_user"]
        if nonempty_str("sl_pass"):
            t["sl_pass"] = doc["sl_pass"]
        for k in ("sl_fprod", "sl_fcons", "sl_fgrid", "sl_fyday", "sl_fcday",
                  "sl_fytot", "sl_fctot", "sl_fsoc", "sl_fbatt"):
            if _is_str(doc.get(k)):
                t[k] = doc[k].strip()
        if has("sl_poll_min"):
            t["sl_poll_min"] = _clamp(_as_int(doc["sl_poll_min"]), 1, 240)
        if has("sl_avg_s"):
            t["sl_avg_s"] = _clamp(_as_int(doc["sl_avg_s"]), 0, 1800)
        if has("on_margin"):
            t["on_margin"] = _clamp(_as_int(doc["on_margin"]), 0, 30000)
        if has("off_margin"):
            t["off_margin"] = _clamp(_as_int(doc["off_margin"]), 0, 30000)
        poll_s = t["sl_poll_min"] * 60
        if has("hyst_on_s"):
            t["hyst_on_s"] = _clamp(_as_int(doc["hyst_on_s"]), 0, 3600)
        elif has("hyst_on"):
            t["hyst_on_s"] = _clamp(_as_int(doc["hyst_on"]), 1, 60) * poll_s
        if has("hyst_off_s"):
            t["hyst_off_s"] = _clamp(_as_int(doc["hyst_off_s"]), 0, 3600)
        elif has("hyst_off"):
            t["hyst_off_s"] = _clamp(_as_int(doc["hyst_off"]), 1, 60) * poll_s
        if has("min_on_min"):
            t["min_on_min"] = _clamp(_as_int(doc["min_on_min"]), 0, 1440)
        if has("min_off_min"):
            t["min_off_min"] = _clamp(_as_int(doc["min_off_min"]), 0, 1440)
        if has("fw_start"):
            h = _as_int(doc["fw_start"])
            if 0 <= h <= 23:
                t["fw_start"] = h
        if has("fw_end"):
            h = _as_int(doc["fw_end"])
            if 1 <= h <= 24:
                t["fw_end"] = h
        if has("sl_fsafe"):
            t["sl_fsafe"] = _clamp(_as_int(doc["sl_fsafe"]), 0, 1440)
        if has("batt_grd"):
            t["batt_grd"] = _clamp(_as_int(doc["batt_grd"]), 0, 30000)
        if has("p_buy"):
            t["p_buy"] = _clamp(_as_int(doc["p_buy"]), 0, 500)
        elif has("p_ht"):
            t["p_buy"] = _clamp(_as_int(doc["p_ht"]), 0, 500)
        if has("p_feed"):
            t["p_feed"] = _clamp(_as_int(doc["p_feed"]), 0, 500)
        if has("p_base"):
            t["p_base"] = _clamp(_as_int(doc["p_base"]), 0, 100000)

        def as_bool(k: str) -> None:
            b = _truthy(doc.get(k))
            if b is not None:
                t[k] = b

        as_bool("hb_en")
        if nonempty_str("hb_url"):
            u = doc["hb_url"]
            if u == "-":
                t["hb_url"] = ""
            else:
                ok, why = url_is_https_ok(u)
                if ok:
                    t["hb_url"] = u
                else:
                    note("Heartbeat-URL nicht übernommen", why)
        if has("hb_min"):
            t["hb_min"] = _clamp(_as_int(doc["hb_min"]), 1, 1440)
        for k in ("mo_sl", "mo_dev", "mo_np", "mo_inv"):
            if has(k):
                t[k] = _clamp(_as_int(doc[k]), 0, 1440)
        as_bool("sl_dev")
        if has("lat"):
            v = _as_float(doc["lat"])
            if -90 <= v <= 90:
                t["lat"] = v
        if has("lon"):
            v = _as_float(doc["lon"])
            if -180 <= v <= 180:
                t["lon"] = v
        as_bool("mq_en")
        if _is_str(doc.get("mq_host")):
            t["mq_host"] = doc["mq_host"].strip()
        if has("mq_port"):
            mp = _as_int(doc["mq_port"])
            if 1 <= mp <= 65535:
                t["mq_port"] = mp
        if _is_str(doc.get("mq_user")):
            t["mq_user"] = doc["mq_user"]
        if nonempty_str("mq_pass"):
            t["mq_pass"] = doc["mq_pass"]
        if nonempty_str("mq_pfx"):
            t["mq_pfx"] = doc["mq_pfx"]
        as_bool("mq_disc")
        as_bool("auto_en")

        for i in range(MAX_SHELLY):
            e = t["shelly"][i]
            p = f"s{i}_"
            if _is_str(doc.get(p + "name")):
                e["name"] = doc[p + "name"]
            if _is_str(doc.get(p + "ip")):
                new_ip = doc[p + "ip"].strip()
                if new_ip == "" or ip_looks_valid(new_ip):
                    old_ip = e["ip"]
                    e["ip"] = new_ip
                    formid = doc.get(p + "id") if _is_str(doc.get(p + "id")) else ""
                    if formid:
                        e["id"] = formid
                    elif old_ip != new_ip:
                        e["id"] = ""
            if has(p + "pw"):
                e["pw"] = _clamp(_as_int(doc[p + "pw"]), 1, 30000)
            if has(p + "pri"):
                e["pri"] = _clamp(_as_int(doc[p + "pri"]), 1, MAX_SHELLY)
            b = _truthy(doc.get(p + "auto")) if _is_str(doc.get(p + "auto")) else None
            if b is not None:
                e["auto"] = b
            if has(p + "rt"):
                rt = _as_int(doc[p + "rt"])
                e["rt"] = rt if 0 <= rt <= 1440 else 0
            if has(p + "mx"):
                mx = _as_int(doc[p + "mx"])
                e["mx"] = mx if 0 <= mx <= 1440 else 0
            if has(p + "rw"):
                e["rw"] = _clamp(_as_int(doc[p + "rw"]), 0, 30000)
            if has(p + "io"):
                e["io"] = _clamp(_as_int(doc[p + "io"]), 0, 1440)
            if has(p + "ws"):
                e["ws"] = _clamp(_as_int(doc[p + "ws"]), 0, 23)
            if has(p + "we"):
                e["we"] = _clamp(_as_int(doc[p + "we"]), 1, 24)
            if has(p + "wd"):
                e["wd"] = _clamp(_as_int(doc[p + "wd"]), 0, 0x7F)
            if _is_str(doc.get(p + "sch")):
                e["sch"] = sched.from_str(doc[p + "sch"])

        for i in range(MAX_EXT):
            e = t["ext"][i]
            p = f"e{i}_"
            if _is_str(doc.get(p + "name")):
                e["name"] = doc[p + "name"]
            if has(p + "pw"):
                e["pw"] = _clamp(_as_int(doc[p + "pw"]), 0, 30000)
            if has(p + "pri"):
                e["pri"] = _clamp(_as_int(doc[p + "pri"]), 1, MAX_EXT)
            b = _truthy(doc.get(p + "auto")) if _is_str(doc.get(p + "auto")) else None
            if b is not None:
                e["auto"] = b
            if has(p + "rt"):
                rt = _as_int(doc[p + "rt"])
                e["rt"] = rt if 0 <= rt <= 1440 else 0
            if has(p + "mx"):
                mx = _as_int(doc[p + "mx"])
                e["mx"] = mx if 0 <= mx <= 1440 else 0
            if has(p + "ws"):
                e["ws"] = _clamp(_as_int(doc[p + "ws"]), 0, 23)
            if has(p + "we"):
                e["we"] = _clamp(_as_int(doc[p + "we"]), 1, 24)
            if has(p + "wd"):
                e["wd"] = _clamp(_as_int(doc[p + "wd"]), 0, 0x7F)
            if _is_str(doc.get(p + "sch")):
                e["sch"] = sched.from_str(doc[p + "sch"])

        # Belegte Slots nach vorne schieben (Shelly: mit IP, Extern: mit Namen)
        sh = [e for e in t["shelly"] if e["ip"]]
        t["shelly"] = sh + [_shelly_default(i) for i in range(len(sh), MAX_SHELLY)]
        t["sh_count"] = len(sh) if sh else 1
        ex = [e for e in t["ext"] if e["name"]]
        t["ext"] = ex + [_ext_default(i) for i in range(len(ex), MAX_EXT)]
        t["ex_count"] = len(ex)

        self.cfg = t
        self.save()
        return orig, t, " ".join(warn)

    # ── Ausgabe ─────────────────────────────────────────────────────────────
    def export(self) -> dict[str, Any]:
        c = self.cfg
        doc: dict[str, Any] = {"eo_config": 1, "device": "energyoptimizer"}
        doc["hostname"] = c["hostname"]
        doc["lang"] = c["lang"]
        for k in ("sl_ip", "sl_port", "sl_user", "sl_fprod", "sl_fcons", "sl_fgrid",
                  "sl_fyday", "sl_fcday", "sl_fytot", "sl_fctot", "sl_fsoc", "sl_fbatt",
                  "sl_poll_min", "sl_avg_s", "sl_fsafe", "batt_grd", "on_margin",
                  "off_margin", "hyst_on_s", "hyst_off_s", "min_on_min", "min_off_min",
                  "fw_start", "fw_end", "p_buy", "p_feed", "p_base"):
            doc[k] = c[k]
        doc["hb_en"] = "true" if c["hb_en"] else "false"
        for k in ("hb_min", "mo_sl", "mo_dev", "mo_np", "mo_inv"):
            doc[k] = c[k]
        doc["sl_dev"] = "true" if c["sl_dev"] else "false"
        doc["lat"] = c["lat"]
        doc["lon"] = c["lon"]
        doc["mq_en"] = "true" if c["mq_en"] else "false"
        for k in ("mq_host", "mq_port", "mq_user", "mq_pfx"):
            doc[k] = c[k]
        doc["mq_disc"] = "true" if c["mq_disc"] else "false"
        doc["auto_en"] = "true" if c["auto_en"] else "false"
        for i, e in enumerate(c["shelly"]):
            p = f"s{i}_"
            for k in ("name", "ip", "id", "pw", "pri"):
                doc[p + k] = e[k]
            doc[p + "auto"] = "true" if e["auto"] else "false"
            for k in ("rt", "mx", "rw", "io", "ws", "we", "wd"):
                doc[p + k] = e[k]
            doc[p + "sch"] = sched.to_str(e["sch"])
        for i, e in enumerate(c["ext"]):
            p = f"e{i}_"
            for k in ("name", "pw", "pri"):
                doc[p + k] = e[k]
            doc[p + "auto"] = "true" if e["auto"] else "false"
            for k in ("rt", "mx", "ws", "we", "wd"):
                doc[p + k] = e[k]
            doc[p + "sch"] = sched.to_str(e["sch"])
        return doc
