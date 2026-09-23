"""Systemzustand des Servers (Raspberry Pi oder PC) für die Diagnose-Karte."""

from __future__ import annotations

import os

from .clock import CLOCK


def _meminfo() -> tuple[int, int]:
    total = avail = 0
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) * 1024
    except OSError:
        pass
    return total, avail


def _cpu_temp() -> float:
    for path in ("/sys/class/thermal/thermal_zone0/temp",):
        try:
            with open(path, encoding="ascii") as f:
                return int(f.read().strip()) / 1000.0
        except (OSError, ValueError):
            continue
    return 0.0


def _disk(path: str) -> tuple[int, int]:
    try:
        st = os.statvfs(path)
    except OSError:
        return 0, 0
    return st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize


class SysInfo:
    def __init__(self, data_dir: str = "/") -> None:
        self.data_dir = data_dir
        self.mem_min = 0
        self._cpu_prev: tuple[int, int] | None = None
        self._proc_prev: tuple[float, float] | None = None
        self.cpu_pct = 0
        self.proc_pct = 0

    def sample(self) -> None:
        total, avail = _meminfo()
        if avail and (not self.mem_min or avail < self.mem_min):
            self.mem_min = avail
        try:
            with open("/proc/stat", encoding="ascii") as f:
                vals = [int(x) for x in f.readline().split()[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            tot = sum(vals)
            if self._cpu_prev:
                dt, di = tot - self._cpu_prev[0], idle - self._cpu_prev[1]
                self.cpu_pct = int(round(100 * (dt - di) / dt)) if dt > 0 else 0
            self._cpu_prev = (tot, idle)
        except (OSError, ValueError, IndexError):
            pass
        t = os.times()
        busy = t.user + t.system
        now = CLOCK.mono()
        if self._proc_prev:
            dw = now - self._proc_prev[1]
            if dw > 0:
                self.proc_pct = int(round(100 * (busy - self._proc_prev[0]) / dw))
        self._proc_prev = (busy, now)

    def get(self) -> dict:
        total, avail = _meminfo()
        d_total, d_free = _disk(self.data_dir)
        return {
            "mem_free": avail, "mem_total": total or 1, "mem_min": self.mem_min or avail,
            "disk_free": d_free, "disk_total": d_total,
            "uptime": int(CLOCK.mono()), "temp": _cpu_temp(),
            "cpu": self.cpu_pct, "proc": self.proc_pct,
        }
