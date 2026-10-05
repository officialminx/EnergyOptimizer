"""Settings.

Stored as JSON in <data>/settings.json. The keys match the field names of the web
API, so exported backups ("energyoptimizer-config.json") can be imported as they are.
"""

from __future__ import annotations

import copy
import ipaddress
import logging
import os
import re
from typing import Any

from . import sched
from .const import (
    MAX_EXT,
    MAX_SHELLY,
    SH_POLL_DEFAULT_S,
    SH_POLL_MAX_S,
    SH_POLL_MIN_S,
    SRC_POLL_DEFAULT_S,
    SRC_POLL_MAX_S,
    SRC_POLL_MIN_S,
)
from .i18n import tr
from .passwords import hash_password, is_hash
from .storage import load_json, save_json

_LOGGER = logging.getLogger(__name__)

DEFAULT_HOSTNAME = "energyoptimizer"
LANGS = ("de", "en")
# Where production, consumption and grid power come from (see sources/).
SOURCES = ("solarlog", "fronius", "shelly_em", "modbus", "mqtt")
MODBUS_PRESETS = ("sma", "huawei", "custom")
MIN_PASSWORD_LEN = 6
MAX_PASSWORD_LEN = 64
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_hostname(name: str) -> str | None:
    """Network name (without ".local"); None if it is not a valid DNS label."""
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
    # st: optional MQTT topic on which the switch reports its real state.
    return {
        "name": "", "pw": 0, "pri": i + 1, "auto": False, "rt": 0, "mx": 0,
        "ws": 0, "we": 24, "wd": 0x7F, "sch": sched.empty(), "st": "",
    }


def defaults() -> dict[str, Any]:
    return {
        "web_pass": "", "hostname": DEFAULT_HOSTNAME, "lang": "de", "wizard_done": False,
        "src": "solarlog",
        "sl_ip": "192.168.0.81", "sl_port": 80, "sl_user": "", "sl_pass": "",
        "sl_fprod": "101", "sl_fcons": "110", "sl_fgrid": "",
        "sl_fyday": "105", "sl_fcday": "111", "sl_fytot": "109", "sl_fctot": "115",
        "sl_fsoc": "858", "sl_fbatt": "858",
        # Fronius, Shelly meter and Modbus share address and port.
        "src_host": "", "src_port": 0, "em_pv_ip": "",
        "mb_preset": "sma", "mb_unit": 3, "mb_prod": "", "mb_grid": "", "mb_batt": "", "mb_soc": "",
        "mqs_prod": "", "mqs_cons": "", "mqs_grid": "", "mqs_batt": "", "mqs_soc": "",
        "src_poll_s": SRC_POLL_DEFAULT_S, "sh_poll_s": SH_POLL_DEFAULT_S, "sl_avg_s": 300,
        "dry_run": False,
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
        "sh_count": 0, "shelly": [_shelly_default(i) for i in range(MAX_SHELLY)],
        "ex_count": 0, "ext": [_ext_default(i) for i in range(MAX_EXT)],
    }


def hyst_ticks(secs: int, poll_s: int) -> int:
    """Number of readings in a row that cover the hysteresis time."""
    poll_s = max(1, int(poll_s))
    need = (int(secs) + poll_s - 1) // poll_s
    return max(1, min(720, need))


def hyst_on_ticks(c: dict) -> int:
    return hyst_ticks(c["hyst_on_s"], c["src_poll_s"])


def hyst_off_ticks(c: dict) -> int:
    return hyst_ticks(c["hyst_off_s"], c["src_poll_s"])


def _pad(items: Any, dflt) -> list[dict[str, Any]]:
    """Stored device list, completed to the current number of slots."""
    out = []
    items = items if isinstance(items, list) else []
    for i in range(MAX_SHELLY if dflt is _shelly_default else MAX_EXT):
        entry = dflt(i)
        if i < len(items) and isinstance(items[i], dict):
            entry.update({k: v for k, v in items[i].items() if k in entry})
            if isinstance(entry["sch"], str):
                entry["sch"] = sched.from_str(entry["sch"])
        out.append(entry)
    return out


# ── Validation helpers ──────────────────────────────────────────────────────

def ip_looks_valid(s: Any) -> bool:
    if not isinstance(s, str) or not s:
        return False
    parts = s.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() and len(p) <= 3 and int(p) <= 255 for p in parts)


def host_looks_valid(s: Any) -> bool:
    """IP address or host name."""
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


_REG_TYPES = ("u16", "s16", "u32", "s32")


def register_spec_ok(spec: str) -> bool:
    """Modbus register as "address:type[:factor]", e.g. "30775:s32:1" or "37113:s32:-1"."""
    parts = spec.split(":")
    if not 2 <= len(parts) <= 3 or not parts[0].isdigit() or int(parts[0]) > 65535:
        return False
    if parts[1].lower() not in _REG_TYPES:
        return False
    if len(parts) == 3:
        try:
            float(parts[2])
        except ValueError:
            return False
    return True


# Plain values that a configuration backup carries.
EXPORT_KEYS = (
    "src", "sl_ip", "sl_port", "sl_user", "sl_fprod", "sl_fcons", "sl_fgrid", "sl_fyday",
    "sl_fcday", "sl_fytot", "sl_fctot", "sl_fsoc", "sl_fbatt", "src_host", "src_port", "em_pv_ip",
    "mb_preset", "mb_unit", "mb_prod", "mb_grid", "mb_batt", "mb_soc", "mqs_prod", "mqs_cons",
    "mqs_grid", "mqs_batt", "mqs_soc", "src_poll_s", "sh_poll_s", "sl_avg_s", "sl_fsafe",
    "batt_grd", "on_margin", "off_margin", "hyst_on_s", "hyst_off_s", "min_on_min", "min_off_min",
    "fw_start", "fw_end", "p_buy", "p_feed", "p_base",
)


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _as_int(v: Any) -> int:
    """Numbers as they are, numeric strings parsed, anything else 0."""
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

    # ── Load / save ─────────────────────────────────────────────────────────
    def load(self) -> None:
        base = defaults()
        stored = load_json(self.path, None)
        if stored is None and not os.path.exists(self.path) and not os.path.exists(self.path + ".bak"):
            self.first_start = True
            self._apply_env_bootstrap(base)
            self.cfg = base
            self.save()
            return
        if not isinstance(stored, dict):
            _LOGGER.error("Settings unreadable – starting with defaults")
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
        if "src_poll_s" not in stored and isinstance(stored.get("sl_poll_min"), int):
            # Up to 0.0.5 the Solar-Log was read every n minutes.
            base["src_poll_s"] = _clamp(stored["sl_poll_min"] * 60, SRC_POLL_MIN_S, SRC_POLL_MAX_S)
            base["sh_poll_s"] = 60
        base["shelly"] = _pad(stored.get("shelly"), _shelly_default)
        base["ext"] = _pad(stored.get("ext"), _ext_default)
        # Up to 0.0.5 an install without plugs had sh_count 1 with an empty slot.
        n = 0
        while n < MAX_SHELLY and base["shelly"][n]["ip"]:
            n += 1
        base["sh_count"] = min(_clamp(_as_int(base["sh_count"]), 0, MAX_SHELLY), n)
        base["ex_count"] = _clamp(_as_int(base["ex_count"]), 0, MAX_EXT)
        self.cfg = base
        if not isinstance(base["web_pass"], str):
            base["web_pass"] = ""
        if base["web_pass"] and not is_hash(base["web_pass"]):
            # Older installs kept the password in plain text: replace it with its hash.
            base["web_pass"] = hash_password(base["web_pass"])
            self.save()

    def _apply_env_bootstrap(self, c: dict) -> None:
        """First start: take values from environment variables (docker-compose)."""
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
        ok = save_json(self.path, self.cfg, indent=1)
        self.save_error = not ok
        return ok

    # ── Apply ───────────────────────────────────────────────────────────────
    def apply(self, doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Applies a form or import document. Returns (old, new, warnings)."""
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
        if _is_str(doc.get("src")) and doc["src"] in SOURCES:
            t["src"] = doc["src"]
        if _is_str(doc.get("src_host")):
            h = doc["src_host"].strip()
            if h == "" or host_looks_valid(h):
                t["src_host"] = h
            else:
                note("Adresse der Datenquelle nicht übernommen", "ungültige Adresse")
        if has("src_port"):
            sp = _as_int(doc["src_port"])
            t["src_port"] = sp if 0 <= sp <= 65535 else 0
        if _is_str(doc.get("em_pv_ip")):
            h = doc["em_pv_ip"].strip()
            if h == "" or host_looks_valid(h):
                t["em_pv_ip"] = h
        if _is_str(doc.get("mb_preset")) and doc["mb_preset"] in MODBUS_PRESETS:
            t["mb_preset"] = doc["mb_preset"]
        if has("mb_unit"):
            t["mb_unit"] = _clamp(_as_int(doc["mb_unit"]), 0, 255)
        for k in ("mb_prod", "mb_grid", "mb_batt", "mb_soc"):
            if _is_str(doc.get(k)):
                v = doc[k].strip()
                if v == "" or register_spec_ok(v):
                    t[k] = v
                else:
                    note("Modbus-Register nicht übernommen",
                         "Format Adresse:Typ:Faktor, z.B. 30775:s32:1")
        for k in ("mqs_prod", "mqs_cons", "mqs_grid", "mqs_batt", "mqs_soc"):
            if _is_str(doc.get(k)):
                t[k] = doc[k].strip()[:120]
        if has("src_poll_s"):
            t["src_poll_s"] = _clamp(_as_int(doc["src_poll_s"]), SRC_POLL_MIN_S, SRC_POLL_MAX_S)
        elif has("sl_poll_min"):
            t["src_poll_s"] = _clamp(_as_int(doc["sl_poll_min"]) * 60, SRC_POLL_MIN_S, SRC_POLL_MAX_S)
        if has("sh_poll_s"):
            t["sh_poll_s"] = _clamp(_as_int(doc["sh_poll_s"]), SH_POLL_MIN_S, SH_POLL_MAX_S)
        if has("sl_avg_s"):
            t["sl_avg_s"] = _clamp(_as_int(doc["sl_avg_s"]), 0, 1800)
        if has("on_margin"):
            t["on_margin"] = _clamp(_as_int(doc["on_margin"]), 0, 30000)
        if has("off_margin"):
            t["off_margin"] = _clamp(_as_int(doc["off_margin"]), 0, 30000)
        poll_s = t["src_poll_s"]
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
        as_bool("dry_run")

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
            if _is_str(doc.get(p + "st")):
                e["st"] = doc[p + "st"].strip()[:120]
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

        # Move used slots to the front (Shelly: with an address, external: with a name)
        sh = [e for e in t["shelly"] if e["ip"]]
        t["shelly"] = sh + [_shelly_default(i) for i in range(len(sh), MAX_SHELLY)]
        t["sh_count"] = len(sh)
        ex = [e for e in t["ext"] if e["name"]]
        t["ext"] = ex + [_ext_default(i) for i in range(len(ex), MAX_EXT)]
        t["ex_count"] = len(ex)

        self.cfg = t
        self.save()
        return orig, t, " ".join(warn)

    # ── Export ──────────────────────────────────────────────────────────────
    def export(self) -> dict[str, Any]:
        c = self.cfg
        doc: dict[str, Any] = {"eo_config": 1, "device": "energyoptimizer"}
        doc["hostname"] = c["hostname"]
        doc["lang"] = c["lang"]
        for k in EXPORT_KEYS:
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
        doc["dry_run"] = "true" if c["dry_run"] else "false"
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
            for k in ("rt", "mx", "ws", "we", "wd", "st"):
                doc[p + k] = e[k]
            doc[p + "sch"] = sched.to_str(e["sch"])
        return doc
