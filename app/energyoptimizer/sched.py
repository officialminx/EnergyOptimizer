"""Zeitschaltuhr-Programme (sched_prog.cpp).

Ein Programm ist [start_min, end_min, days, enabled]: Minuten seit Mitternacht,
Wochentagsmaske (Bit0 = Montag … Bit6 = Sonntag) und ob es aktiv ist. Endet ein
Programm vor seinem Start, läuft es über Mitternacht – massgeblich ist dann der
Wochentag, an dem es STARTET.
"""

from __future__ import annotations

from datetime import datetime

from .const import MAX_SCHED

Slot = list  # [start_min, end_min, days, enabled]


def empty() -> list[Slot]:
    return [[0, 0, 0, False] for _ in range(MAX_SCHED)]


def slot_used(s: Slot) -> bool:
    start, end, days, enabled = s
    if not enabled or days == 0 or start == end:
        return False
    return 0 <= start <= 1439 and 0 <= end <= 1439


def any_used(sl: list[Slot]) -> bool:
    return any(slot_used(s) for s in sl)


def active(sl: list[Slot], now: datetime) -> int:
    """Index des gerade laufenden Programms oder -1."""
    dow = now.weekday()
    now_min = now.hour * 60 + now.minute
    for k, s in enumerate(sl):
        if not slot_used(s):
            continue
        start, end, days, _ = s
        if start < end:
            if start <= now_min < end and days & (1 << dow):
                return k
        else:
            if now_min >= start and days & (1 << dow):
                return k
            prev = (dow + 6) % 7
            if now_min < end and days & (1 << prev):
                return k
    return -1


def next_start_min(sl: list[Slot], now: datetime) -> int:
    dow = now.weekday()
    now_min = now.hour * 60 + now.minute
    best = -1
    for d in range(8):
        day = (dow + d) % 7
        for s in sl:
            if not slot_used(s) or not (s[2] & (1 << day)):
                continue
            delta = d * 1440 + s[0] - now_min
            if delta <= 0:
                continue
            if best < 0 or delta < best:
                best = delta
    return best


def ends_in_min(s: Slot, now: datetime) -> int:
    delta = s[1] - (now.hour * 60 + now.minute)
    if delta <= 0:
        delta += 1440
    return delta


def to_str(sl: list[Slot]) -> str:
    last = -1
    for k, s in enumerate(sl):
        if s[0] != s[1] or s[2] or s[3]:
            last = k
    if last < 0:
        return ""
    return ";".join(f"{s[0]},{s[1]},{s[2]},{1 if s[3] else 0}" for s in sl[: last + 1])


def from_str(text: str | None) -> list[Slot]:
    out = empty()
    if not text:
        return out
    for k, part in enumerate(str(text).split(";")[:MAX_SCHED]):
        nums = []
        for tok in part.split(",")[:4]:
            try:
                nums.append(int(tok.strip()))
            except ValueError:
                break
        if len(nums) < 3:
            continue
        if len(nums) == 3:
            nums.append(1)  # ältere Sicherung ohne Aktiv-Flag: gilt als aktiv
        st, en, dy, fl = nums
        if 0 <= st <= 1439 and 0 <= en <= 1439 and 0 <= dy <= 0x7F:
            out[k] = [st, en, dy, fl != 0]
    return out
