"""Web password storage: only a salted scrypt hash is kept, never the password."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_PREFIX = "scrypt"
# About 16 MB and a few dozen milliseconds per check on a Raspberry Pi 4.
_N, _R, _P = 2 ** 14, 8, 1


def _derive(pw: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    return f"{_PREFIX}${_N}${_R}${_P}${salt.hex()}${_derive(pw, salt, _N, _R, _P).hex()}"


def is_hash(stored: str) -> bool:
    return isinstance(stored, str) and stored.startswith(_PREFIX + "$") and stored.count("$") == 5


def verify_password(pw: str, stored: str) -> bool:
    if not pw or not is_hash(stored):
        return False
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = _derive(pw, bytes.fromhex(salt), int(n), int(r), int(p))
        return hmac.compare_digest(got, bytes.fromhex(digest))
    except (ValueError, MemoryError):
        return False
