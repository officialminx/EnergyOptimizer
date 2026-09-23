"""Solar-Log-Abfrage.

`SolarLogClient` ist eine direkte Übernahme des Clients aus ha-advanced-solarlog
(custom_components/advanced_solarlog/api.py). Nur dessen Anmeldung öffnet den
passwortgeschützten JSON-Zugang zuverlässig:

* POST /getjp mit ``Content-Type: text/html`` und ``X-SL-CSRF-PROTECTION: 1``
  (application/json und HTTP-Basic-Auth, wie die ESP32-Firmware sie schickte,
  lehnt der Solar-Log ab),
* Login über POST /login mit ``u=<konto>&p=<passwort>``, wobei die Kontonamen
  der Reihe nach probiert werden,
* bei "Password was wrong" ein zweiter Versuch mit dem bcrypt-Hash des Passworts
  (Salz aus ``{"550":null}``) – neuere Firmware verlangt das,
* das Sitzungs-Cookie ``SolarLog`` wird als eigener Header mitgeschickt, bei
  Klartext-Passwort zusätzlich ``token=…; `` vor dem Anfragetext.

`SolarLogReader` bildet daraus die Messwerte, die die Regelung braucht
(solarlog.cpp der Firmware: Felder aus 801/170, Batterie aus 858, Geräteebene
aus 740/608/782/141).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import SL_MAX_DEV

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 80
LOGIN_USERNAMES = ("user", "installer", "installateur", "pm")
REQ_BASIC = '{"801":{"170":null}}'
REQ_BATTERY = '{"858":null}'
REQ_DEVICE_LIST = '{"740":null}'
REQ_DEVICE_STATUS = '{"608":null}'
REQ_INVERTER_POWER = '{"782":null}'
REQ_SALT = '{"550":null}'

REQUEST_TIMEOUT = 30

# Solar-Log answers with these markers instead of an HTTP status code.
MARKER_DENIED = "ACCESS DENIED"
MARKER_IMPOSSIBLE = "QUERY IMPOSSIBLE 000"

SL_RAW_KEEP = 1400


class SolarLogError(Exception):
    """The device could not be reached or answered with something unusable."""


class SolarLogAuthError(SolarLogError):
    """The device refused the request because of a wrong or missing password."""


class SolarLogClient:
    """Read-only client for one Solar-Log device (from ha-advanced-solarlog)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = DEFAULT_PORT,
        password: str | None = None,
        username: str | None = None,
    ) -> None:
        self._session = session
        self.host = host
        self.port = port
        self.password = password or ""
        # Ein in den Einstellungen hinterlegter Kontoname wird zuerst probiert,
        # danach die Namen, die ha-advanced-solarlog kennt.
        names = list(LOGIN_USERNAMES)
        if username and username not in names:
            names.insert(0, username)
        elif username:
            names.remove(username)
            names.insert(0, username)
        self.usernames = tuple(names)
        # The account name the device accepted, once one has been found.
        self.username = self.usernames[0]
        # The session cookie, sent as a header on every later request.
        self._cookie = ""
        # Older firmware ignores the cookie and expects the session token
        # repeated in the request body.
        self._token = ""
        self._hashed_password = False
        self._denial_reported = False
        # What the device answered to each login attempt of the last login.
        self.login_trace: list[dict[str, Any]] = []

    @property
    def base_url(self) -> str:
        if self.port == DEFAULT_PORT:
            return f"http://{self.host}"
        return f"http://{self.host}:{self.port}"

    async def _post_response(self, body: str, path: str = "getjp") -> tuple[int, str, dict[str, str]]:
        """Send one request and return status, text and the cookies it set."""
        url = f"{self.base_url}/{path}"
        # Solar-Log rejects application/json here; its own web UI posts the
        # JSON document as text/html and relies on the CSRF header.
        headers = {"Content-Type": "text/html", "X-SL-CSRF-PROTECTION": "1"}
        if self._cookie:
            headers["Cookie"] = f"SolarLog={self._cookie}"
        if self._token:
            body = f"token={self._token}; " + body

        try:
            async with self._session.post(
                url,
                headers=headers,
                data=body,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                text = await response.text(errors="replace")
                cookies = {k: m.value for k, m in response.cookies.items()}
                status = response.status
        except asyncio.TimeoutError as err:
            raise SolarLogError(f"Timeout while connecting to Solar-Log at {self.host}") from err
        except aiohttp.ClientError as err:
            raise SolarLogError(f"Cannot connect to Solar-Log at {self.host}: {err}") from err

        if status == 401:
            raise SolarLogAuthError("Solar-Log rejected the credentials")
        if status != 200:
            raise SolarLogError(f"Solar-Log answered with HTTP {status} on {path}")
        return status, text, cookies

    async def _post(self, body: str, path: str = "getjp") -> str:
        _, text, _ = await self._post_response(body, path)
        return text

    async def request_text(self, body: str, *, allow_relogin: bool = True) -> str:
        """Send one `/getjp` request and return the checked raw text."""
        text = await self._post(body)

        if MARKER_IMPOSSIBLE in text:
            raise SolarLogError(f"Solar-Log cannot answer this query: {body}")
        # The salt query legitimately carries "ACCESS DENIED" alongside the salt.
        if MARKER_DENIED in text and not text.startswith('{"550"'):
            _LOGGER.debug("Solar-Log denied %s; %s", body, self.login_report())
            # The device drops a session after a while – a denial is worth one
            # fresh login.
            if allow_relogin and self.password and await self.async_login():
                return await self.request_text(body, allow_relogin=False)
            self._report_denial()
            raise SolarLogAuthError(
                "Solar-Log denied access -- a password is required for this value"
            )
        return text

    async def request(self, body: str, *, allow_relogin: bool = True) -> dict[str, Any]:
        """Send one `/getjp` request and return the decoded response."""
        text = await self.request_text(body, allow_relogin=allow_relogin)
        try:
            data = json.loads(text)
        except ValueError as err:
            raise SolarLogError(f"Solar-Log sent a response that is not JSON: {text[:200]}") from err
        if not isinstance(data, dict):
            raise SolarLogError(f"Solar-Log sent unexpected JSON: {text[:200]}")
        return data

    async def async_login(self) -> bool:
        """Log in if a password is configured."""
        self.login_trace = []
        if not self.password:
            return False

        for username in self.usernames:
            _, text, cookies = await self._post_response(
                f"u={username}&p={self.password}", path="login"
            )
            _LOGGER.debug("Solar-Log login as %r answered: %s", username, text[:200])

            if "FAILED - User was wrong" in text:
                self._trace_login(username, text, cookies, "account name refused")
                continue

            if "FAILED - Password was wrong" in text:
                # Newer firmware expects the password bcrypt-hashed with a salt
                # the device hands out. Only a second failure is a real auth error.
                self._trace_login(username, text, cookies, "retrying hashed")
                text, cookies = await self._retry_login_hashed(username)

            if "FAILED" in text:
                self._trace_login(username, text, cookies, "password refused")
                raise SolarLogAuthError("Solar-Log rejected the password")

            self._trace_login(username, text, cookies, "accepted")
            self.username = username
            self._remember_session(cookies)
            return True

        _LOGGER.warning(
            "Solar-Log answered 'User was wrong' for every account name (%s). Either "
            "the device has no password set, or its login expects another account name",
            ", ".join(self.usernames),
        )
        return False

    def _trace_login(self, username: str, text: str, cookies: dict[str, str], outcome: str) -> None:
        self.login_trace.append(
            {"username": username, "outcome": outcome, "answer": text[:120],
             "cookies": list(cookies.keys())}
        )

    def login_report(self) -> dict[str, Any]:
        return {
            "password_configured": bool(self.password),
            "accepted_username": self.username if self.login_trace else None,
            "known_usernames": list(self.usernames),
            "password_is_hashed": self._hashed_password,
            "body_token_set": bool(self._token),
            "session_cookie_held": bool(self._cookie),
            "attempts": self.login_trace,
        }

    def _report_denial(self) -> None:
        if self._denial_reported:
            return
        self._denial_reported = True
        _LOGGER.warning("Solar-Log refuses the protected values. Login report: %s",
                        self.login_report())

    async def _retry_login_hashed(self, username: str) -> tuple[str, dict[str, str]]:
        """Second login attempt with the bcrypt-hashed password."""
        import bcrypt  # noqa: PLC0415

        salt = (await self.request(REQ_SALT, allow_relogin=False)).get("550", {})
        salt = salt.get("104") if isinstance(salt, dict) else None
        if not salt or salt == MARKER_IMPOSSIBLE:
            self.login_trace.append(
                {"username": username, "outcome": "no salt for the hashed login",
                 "answer": str(salt)[:120], "cookies": []}
            )
            raise SolarLogAuthError("Solar-Log rejected the password")

        try:
            hashed = bcrypt.hashpw(self.password.encode(), salt.encode()).decode()
        except (TypeError, ValueError) as err:
            raise SolarLogAuthError("Solar-Log returned a salt that bcrypt does not accept") from err

        _, text, cookies = await self._post_response(f"u={username}&p={hashed}", path="login")
        _LOGGER.debug("Solar-Log hashed-password login response: %s", text[:200])
        if "FAILED" not in text:
            # Keep the hash: the device expects it on every later login.
            self.password = hashed
            self._hashed_password = True
        return text, cookies

    def _remember_session(self, cookies: dict[str, str]) -> None:
        """Keep the session cookie and send it back ourselves (see ha-advanced-solarlog)."""
        cookie = cookies.get("SolarLog")
        if not cookie:
            _LOGGER.warning(
                "Solar-Log login reported success but sent no 'SolarLog' cookie "
                "(cookies in the response: %s)", list(cookies.keys()),
            )
            return
        self._cookie = cookie
        if not self._hashed_password:
            self._token = cookie


def _as_float(value: Any) -> float:
    """Solar-Log mixes numbers and numeric strings in the same response."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _num_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if not value or value == "Err":
            return None
        try:
            return float(value)
        except ValueError:
            return None
    if isinstance(value, list):
        for e in value:
            if isinstance(e, (int, float)) and not isinstance(e, bool):
                return float(e)
    return None


def _entries(v: Any):
    """(index, value) für Array oder Objekt mit Positionsnummern als Schlüssel."""
    if isinstance(v, list):
        for i, e in enumerate(v[:SL_MAX_DEV]):
            yield i, e
    elif isinstance(v, dict):
        for k, e in v.items():
            try:
                i = int(k)
            except ValueError:
                continue
            if 0 <= i < SL_MAX_DEV:
                yield i, e


def _present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v) and v != "Err"
    if isinstance(v, list):
        return len(v) > 0
    return True


@dataclass
class SolarData:
    production_w: float = 0.0
    consumption_w: float = 0.0
    grid_w: float = 0.0
    surplus_w: float = 0.0
    valid: bool = False
    prod_today_wh: float = 0.0
    cons_today_wh: float = 0.0
    prod_total_wh: float = 0.0
    cons_total_wh: float = 0.0
    has_battery: bool = False
    battery_w: float = 0.0
    battery_soc: int = 0


@dataclass
class SolarDevice:
    present: bool = False
    name: str = ""
    status: str = ""
    has_power: bool = False
    power_w: float = 0.0
    seen_max_w: float = 0.0


@dataclass
class SolarDevices:
    valid: bool = False
    count: int = 0
    d: list[SolarDevice] = field(default_factory=lambda: [SolarDevice() for _ in range(SL_MAX_DEV)])


def _raw_keep(text: str) -> str:
    return text if len(text) <= SL_RAW_KEEP else text[:SL_RAW_KEEP] + " …[gekürzt]"


class SolarLogReader:
    """Liest die Messwerte gemäss den Feldnummern aus den Einstellungen."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._client: SolarLogClient | None = None
        self._key: tuple | None = None
        self._logged_in = False
        self.raw_main = ""
        self.raw_dev = ""
        self.last_error = ""

    def client_for(self, cfg: dict) -> SolarLogClient:
        key = (cfg["sl_ip"], int(cfg["sl_port"]), cfg["sl_pass"], cfg["sl_user"])
        if self._client is None or key != self._key:
            self._client = SolarLogClient(
                self._session, host=cfg["sl_ip"], port=int(cfg["sl_port"]) or DEFAULT_PORT,
                password=cfg["sl_pass"], username=cfg["sl_user"] or None,
            )
            self._key = key
            self._logged_in = False
        return self._client

    async def _ensure_login(self, client: SolarLogClient) -> None:
        # Wie ha-advanced-solarlog beim Einrichten: mit Passwort vorab anmelden,
        # damit auch die geschützten Werte (Batterie, Geräte) lesbar sind.
        if self._logged_in or not client.password:
            return
        self._logged_in = True
        try:
            await client.async_login()
        except SolarLogAuthError as err:
            _LOGGER.warning("Solar-Log-Anmeldung fehlgeschlagen: %s", err)

    async def fetch(self, cfg: dict) -> SolarData | None:
        client = self.client_for(cfg)
        try:
            await self._ensure_login(client)
            text = await client.request_text(REQ_BASIC)
            data = json.loads(text)
            values = data.get("801", {}).get("170") if isinstance(data, dict) else None
            if not isinstance(values, dict):
                raise SolarLogError("Solar-Log did not return the expected 801/170 block")
        except (SolarLogError, ValueError) as err:
            self.last_error = str(err)
            _LOGGER.warning("[SolarLog] Abfrage fehlgeschlagen: %s", err)
            return None

        raw = text
        out = SolarData()

        def fld(key: str) -> float:
            return _as_float(values.get(key)) if key else 0.0

        out.production_w = fld(cfg["sl_fprod"])
        out.consumption_w = fld(cfg["sl_fcons"])
        out.grid_w = fld(cfg["sl_fgrid"]) if cfg["sl_fgrid"] else out.consumption_w - out.production_w
        out.surplus_w = out.production_w - out.consumption_w
        out.valid = True
        out.prod_today_wh = fld(cfg["sl_fyday"])
        out.cons_today_wh = fld(cfg["sl_fcday"])
        out.prod_total_wh = fld(cfg["sl_fytot"])
        out.cons_total_wh = fld(cfg["sl_fctot"])

        # Batterie nur, wenn in den Einstellungen aktiviert. Eigene Abfrage wie in
        # ha-advanced-solarlog: 858 steht hinter dem Passwort, ein Fehler hier darf
        # die Hauptwerte nicht mitreissen.
        if cfg["sl_fsoc"] or cfg["sl_fbatt"]:
            try:
                btext = await client.request_text(REQ_BATTERY)
                raw += "\n" + btext
                batt = json.loads(btext).get("858")
                if isinstance(batt, list) and len(batt) >= 4:
                    out.has_battery = True
                    out.battery_soc = int(_as_float(batt[1]))
                    out.battery_w = _as_float(batt[2]) - _as_float(batt[3])
            except (SolarLogError, ValueError, AttributeError) as err:
                raw += f"\n[858] {err}"
                _LOGGER.debug("[SolarLog] Batterie nicht lesbar: %s", err)

        self.raw_main = _raw_keep(raw)
        self.last_error = ""
        if out.has_battery:
            _LOGGER.info("[SolarLog] %.0f W | Verbrauch %.0f W | Netz %.0f W | Überschuss %.0f W | "
                         "Batt %.0f W (%d%%)", out.production_w, out.consumption_w, out.grid_w,
                         out.surplus_w, out.battery_w, out.battery_soc)
        else:
            _LOGGER.info("[SolarLog] %.0f W | Verbrauch %.0f W | Netz %.0f W | Überschuss %.0f W",
                         out.production_w, out.consumption_w, out.grid_w, out.surplus_w)
        return out

    async def fetch_devices(self, cfg: dict, prev: SolarDevices) -> SolarDevices | None:
        client = self.client_for(cfg)
        parts: dict[str, Any] = {}
        raws = []
        for key, body in (("740", REQ_DEVICE_LIST), ("608", REQ_DEVICE_STATUS),
                          ("782", REQ_INVERTER_POWER)):
            try:
                text = await client.request_text(body)
                raws.append(text)
                parts[key] = json.loads(text).get(key)
            except (SolarLogError, ValueError, AttributeError) as err:
                raws.append(f"[{key}] {err}")
        self.raw_dev = _raw_keep("\n".join(raws))

        res = SolarDevices()
        for i, v in _entries(parts.get("740")):
            if _present(v):
                res.d[i].present = True
                res.count = max(res.count, i + 1)
        if res.count == 0:
            for i, v in _entries(parts.get("782")):
                if _num_or_none(v) is not None:
                    res.d[i].present = True
                    res.count = max(res.count, i + 1)
        for i, v in _entries(parts.get("608")):
            if res.d[i].present and isinstance(v, str):
                res.d[i].status = v.lstrip(" ")[:15]
        for i, v in _entries(parts.get("782")):
            f = _num_or_none(v)
            if res.d[i].present and f is not None:
                res.d[i].has_power = True
                res.d[i].power_w = f
        if res.count == 0:
            _LOGGER.info("[SolarLog] Geräteabfrage: keine Positionen erkannt")
            return None
        for i in range(SL_MAX_DEV):
            res.d[i].name = prev.d[i].name
            res.d[i].seen_max_w = prev.d[i].seen_max_w
            if res.d[i].has_power and res.d[i].power_w > res.d[i].seen_max_w:
                res.d[i].seen_max_w = res.d[i].power_w
        res.valid = True
        return res

    async def fetch_one_name(self, cfg: dict, devs: SolarDevices) -> bool:
        if not devs.valid:
            return False
        client = self.client_for(cfg)
        for i in range(devs.count):
            if not devs.d[i].present or devs.d[i].name:
                continue
            # Nur EIN Gerät pro Aufruf, nie das ganze 141-Objekt.
            try:
                data = await client.request(f'{{"141":{{"{i}":{{"119":null}}}}}}')
            except SolarLogError:
                return False
            nm = data.get("141", {})
            nm = nm.get(str(i), {}) if isinstance(nm, dict) else {}
            nm = nm.get("119") if isinstance(nm, dict) else None
            devs.d[i].name = str(nm)[:23] if nm else f"Gerät {i + 1}"
            return True
        return False
