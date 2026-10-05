"""Sign-in sessions of the web interface.

Every sign-in gets its own random token (cookie ``eo_auth``). Only a SHA-256 hash
of the token is stored, in <data>/sessions.json, together with when it was
created and last used and a short description of the browser. That allows
signing out a single device and signing out everywhere; changing or resetting
the password ends all other sessions.
"""

from __future__ import annotations

import hashlib
import os
import secrets

from .clock import CLOCK
from .storage import load_json, save_json

MAX_SESSIONS = 30
SESSION_MAX_AGE_S = 365 * 86400       # unused for a year: signed out
SEEN_SAVE_S = 3600                    # last use is written at most once an hour


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def describe_agent(ua: str) -> str:
    """Short browser description, e.g. "Safari · iPhone"."""
    ua = ua or ""
    if "Firefox/" in ua:
        br = "Firefox"
    elif "Edg/" in ua:
        br = "Edge"
    elif "Chrome/" in ua or "CriOS/" in ua:
        br = "Chrome"
    elif "Safari/" in ua:
        br = "Safari"
    elif "curl/" in ua:
        br = "curl"
    elif "HomeAssistant" in ua or "aiohttp" in ua or "python" in ua.lower():
        br = "Script"
    else:
        br = "Browser"
    for key, os_name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                         ("Mac OS X", "Mac"), ("Windows", "Windows"), ("Linux", "Linux")):
        if key in ua:
            return f"{br} · {os_name}"
    return br


class Sessions:
    def __init__(self, data_dir: str) -> None:
        self.path = os.path.join(data_dir, "sessions.json")
        self.s: dict[str, dict] = {}
        self.dirty = False
        self._saved_t = 0.0

    def load(self) -> None:
        data = load_json(self.path, {})
        if isinstance(data, dict):
            self.s = {k: v for k, v in data.items() if isinstance(v, dict)}
        self._expire()

    def flush(self) -> None:
        if self.dirty:
            self.dirty = False
            self._saved_t = CLOCK.mono()
            save_json(self.path, self.s)

    def tick(self) -> None:
        if self.dirty and CLOCK.mono() - self._saved_t >= SEEN_SAVE_S:
            self._expire()
            self.flush()

    def _expire(self) -> None:
        now = CLOCK.time()
        old = [k for k, v in self.s.items() if now - float(v.get("seen", 0)) > SESSION_MAX_AGE_S]
        for k in old:
            del self.s[k]
            self.dirty = True

    def create(self, ua: str = "") -> str:
        token = secrets.token_urlsafe(24)
        now = int(CLOCK.time())
        if len(self.s) >= MAX_SESSIONS:
            oldest = min(self.s, key=lambda k: self.s[k].get("seen", 0))
            del self.s[oldest]
        self.s[_hash(token)] = {"created": now, "seen": now, "ua": describe_agent(ua)}
        self.dirty = True
        self.flush()
        return token

    def valid(self, token: str) -> bool:
        if not token:
            return False
        rec = self.s.get(_hash(token))
        if rec is None:
            return False
        now = int(CLOCK.time())
        if now - rec.get("seen", 0) > 300:
            rec["seen"] = now
            self.dirty = True
        return True

    def revoke(self, token: str) -> bool:
        if self.s.pop(_hash(token), None) is not None:
            self.dirty = True
            self.flush()
            return True
        return False

    def revoke_id(self, sid: str) -> bool:
        for k in list(self.s):
            if k[:12] == sid:
                del self.s[k]
                self.dirty = True
                self.flush()
                return True
        return False

    def revoke_all(self, keep: str = "") -> int:
        keep_h = _hash(keep) if keep else ""
        n = 0
        for k in list(self.s):
            if k != keep_h:
                del self.s[k]
                n += 1
        if n:
            self.dirty = True
            self.flush()
        return n

    def listing(self, current: str = "") -> list[dict]:
        cur = _hash(current) if current else ""
        out = [{"id": k[:12], "created": v.get("created", 0), "seen": v.get("seen", 0),
                "ua": v.get("ua", ""), "current": k == cur} for k, v in self.s.items()]
        out.sort(key=lambda x: -x["seen"])
        return out
