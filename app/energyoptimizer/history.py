"""History.

* Day curve: 96 points on a 15-minute grid, average of all readings of the
  interval, kept in history.json. A point is
  [epoch, prod, cons, grid, batt, on_mask, run_mask, w0 … w15].
* Long-term log hist.csv (same values, one row per point), cut from 12 MB to 6 MB.
  Any past day can be read back from it.
* Daily archive hist_daily.json: one record per day (production, consumption,
  grid import, feed-in), with CSV import.
* Smoothing of the live values for display (sl_avg_s).
"""

from __future__ import annotations

import logging
import os
import shutil
from collections import deque
from datetime import date, datetime, timedelta

from .clock import CLOCK
from .const import HISTORY_MAX, HISTORY_SAMPLE_S, MAX_SHELLY
from .solarlog import SolarData
from .storage import load_json, save_json

_LOGGER = logging.getLogger(__name__)

# sh<i>_on: 0 = off, 1 = on, 2 = on and the device runs (self-regulating plugs)
DATALOG_HDR = ("epoch,prod_w,cons_w,grid_w,batt_w,soc,"
               + ",".join(f"sh{i}_w" for i in range(MAX_SHELLY)) + ","
               + ",".join(f"sh{i}_on" for i in range(MAX_SHELLY)))
DATALOG_COLS = 6 + 2 * MAX_SHELLY
PT_LEN = 7 + MAX_SHELLY
DATALOG_MAX_BYTES = 12 * 1024 * 1024
DATALOG_KEEP_BYTES = 6 * 1024 * 1024
SOLAVG_MAX = 720
HDI_MAX_ROWS = 5000
HDF_IMPORTED = 0x01
HDF_LOW_CONFIDENCE = 0x02


def _clamp16(v: float) -> int:
    return int(max(-32000, min(32000, round(v))))


class History:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        self.ring_path = os.path.join(data_dir, "history.json")
        self.datalog_path = os.path.join(data_dir, "hist.csv")
        self.daily_path = os.path.join(data_dir, "hist_daily.json")
        self.pts: deque[list] = deque(maxlen=HISTORY_MAX)
        self.yday = -1
        self.werr = False
        self._energy_cache: dict[int, dict] = {}
        self._energy_cache_day: date | None = None
        # Live values + smoothing
        self.live = SolarData()
        self.live_t: float = 0.0
        self.next_t: float = 0.0
        self._avg: deque[tuple] = deque(maxlen=SOLAVG_MAX)
        # Accumulators for the next history point
        self._acc = [0.0, 0.0, 0.0, 0.0, 0]
        self._acc_sh = [[0.0, 0] for _ in range(MAX_SHELLY)]
        self.last_soc = 0

    # ── Live values ─────────────────────────────────────────────────────────
    def set_solar(self, d: SolarData) -> None:
        self.live = d
        self.live_t = CLOCK.mono()
        self._avg.append((self.live_t, d.production_w, d.consumption_w, d.grid_w,
                          d.battery_w if d.has_battery else 0.0))
        self._acc[0] += d.production_w
        self._acc[1] += d.consumption_w
        self._acc[2] += d.grid_w
        self._acc[3] += d.battery_w if d.has_battery else 0.0
        self._acc[4] += 1
        self.last_soc = d.battery_soc if d.has_battery else 0

    def solar_avg(self, window_s: float) -> SolarData:
        sd = SolarData(**self.live.__dict__)
        if window_s > 0 and len(self._avg) > 1 and sd.valid:
            now = CLOCK.mono()
            p = c = g = b = 0.0
            n = 0
            for s in reversed(self._avg):
                if now - s[0] > window_s:
                    break
                p += s[1]
                c += s[2]
                g += s[3]
                b += s[4]
                n += 1
            if n > 1:
                sd.production_w = p / n
                sd.consumption_w = c / n
                sd.grid_w = g / n
                sd.surplus_w = sd.production_w - sd.consumption_w
                if sd.has_battery:
                    sd.battery_w = b / n
        return sd

    def accum_shelly(self, states) -> None:
        for i, st in enumerate(states[:MAX_SHELLY]):
            if st.reachable:
                self._acc_sh[i][0] += st.apower_w
                self._acc_sh[i][1] += 1

    # ── Day curve ───────────────────────────────────────────────────────────
    @staticmethod
    def _upgrade_point(p: list) -> list | None:
        """Point of the current format; converts the 0.0.5 format (four plugs)."""
        if len(p) == PT_LEN:
            return p
        if len(p) == 9:
            mask = int(p[8])
            return p[:4] + [0, mask & 0xF, (mask >> 4) & 0xF] + p[4:8] + [0] * (MAX_SHELLY - 4)
        return None

    def load(self) -> None:
        data = load_json(self.ring_path, None)
        if isinstance(data, dict) and isinstance(data.get("pts"), list):
            today = CLOCK.now().timetuple().tm_yday
            if data.get("yday") == today:
                for p in data["pts"][-HISTORY_MAX:]:
                    if isinstance(p, list):
                        q = self._upgrade_point(p)
                        if q is not None:
                            self.pts.append(q)
                self.yday = today
        self._migrate_datalog()

    def record(self, states, self_regulated, is_running) -> bool:
        """Append a history point (called every 15 min, at :00/:15/:30/:45)."""
        if not self.live.valid:
            return False
        a = self._acc
        n = a[4]
        prod = a[0] / n if n else self.live.production_w
        cons = a[1] / n if n else self.live.consumption_w
        grid = a[2] / n if n else self.live.grid_w
        batt = a[3] / n if n else (self.live.battery_w if self.live.has_battery else 0.0)
        self._acc = [0.0, 0.0, 0.0, 0.0, 0]
        now = CLOCK.now()
        on_mask = run_mask = 0
        watts = []
        for i in range(MAX_SHELLY):
            s, k = self._acc_sh[i]
            st = states[i]
            watts.append(_clamp16(s / k if k else st.apower_w))
            if st.real_on and st.reachable:
                on_mask |= 1 << i
            if self_regulated(i) and is_running(i):
                run_mask |= 1 << i
        self._acc_sh = [[0.0, 0] for _ in range(MAX_SHELLY)]
        p = [int(CLOCK.time()), _clamp16(prod), _clamp16(cons), _clamp16(grid), _clamp16(batt),
             on_mask, run_mask] + watts
        yday = now.timetuple().tm_yday
        if self.yday != yday:
            self.pts.clear()
        self.yday = yday
        self.pts.append(p)
        save_json(self.ring_path, {"yday": self.yday, "pts": list(self.pts)})
        self._datalog_append(p)
        return True

    @staticmethod
    def _shape(pts, shc: int) -> list[list]:
        """Points for the interface: only the configured plugs."""
        return [p[:7] + p[7:7 + shc] for p in pts]

    def build(self, cfg: dict, names: list[str], pts: list[list] | None = None) -> dict:
        shc = 0
        while shc < min(cfg["sh_count"], MAX_SHELLY) and cfg["shelly"][shc]["ip"]:
            shc += 1
        return {
            "t": int(CLOCK.time()), "n": shc, "dev": names[:shc],
            "rw": [1 if cfg["shelly"][i]["rw"] > 0 else 0 for i in range(shc)],
            "pts": self._shape(self.pts if pts is None else pts, shc),
        }

    def day_points(self, day: int) -> list[list]:
        """All points of one local day (YYYYMMDD): today from memory, else from hist.csv."""
        today = CLOCK.now()
        if day == today.year * 10000 + today.month * 100 + today.day:
            # Until the first point after midnight the buffer still holds yesterday.
            return list(self.pts) if self.yday == today.timetuple().tm_yday else []
        start = datetime(day // 10000, day // 100 % 100, day % 100, tzinfo=CLOCK.tz)
        t0 = int(start.timestamp())
        t1 = int((start + timedelta(days=1)).timestamp())
        out: list[list] = []
        if not self.datalog_exists():
            return out
        with open(self.datalog_path, encoding="utf-8") as f:
            f.readline()
            for line in f:
                head = line.split(",", 1)[0]
                try:
                    ep = int(head)
                except ValueError:
                    continue
                if ep < t0:
                    continue
                if ep >= t1:
                    break
                p = self._csv_point(line)
                if p is not None:
                    out.append(p)
        return out

    @staticmethod
    def _csv_point(line: str) -> list | None:
        parts = line.strip().split(",")
        if len(parts) < DATALOG_COLS:
            return None
        try:
            v = [int(x or 0) for x in parts[:DATALOG_COLS]]
        except ValueError:
            return None
        on_mask = run_mask = 0
        for i in range(MAX_SHELLY):
            st = v[6 + MAX_SHELLY + i]
            if st:
                on_mask |= 1 << i
            if st == 2:
                run_mask |= 1 << i
        return v[:5] + [on_mask, run_mask] + v[6:6 + MAX_SHELLY]

    def days_available(self) -> list[int]:
        """Days (YYYYMMDD) that hist.csv has points for."""
        days: set[int] = set()
        if self.datalog_exists():
            with open(self.datalog_path, encoding="utf-8") as f:
                f.readline()
                last = -1
                for line in f:
                    try:
                        ep = int(line.split(",", 1)[0])
                    except ValueError:
                        continue
                    k = ep // 3600
                    if k == last:
                        continue
                    last = k
                    d = CLOCK.local(ep)
                    days.add(d.year * 10000 + d.month * 100 + d.day)
        t = CLOCK.now()
        if self.pts:
            days.add(t.year * 10000 + t.month * 100 + t.day)
        return sorted(days)

    # ── hist.csv ────────────────────────────────────────────────────────────
    def _migrate_datalog(self) -> None:
        """Brings an older hist.csv (four plugs, no battery) to the current format."""
        path = self.datalog_path
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                hdr = f.readline().strip()
                if hdr == DATALOG_HDR:
                    return
                rest = f.read()
            lines = rest.splitlines() if hdr.startswith("epoch") else [hdr] + rest.splitlines()
            tmp = path + ".tmp"
            pad = MAX_SHELLY - 4
            with open(tmp, "w", encoding="utf-8") as out:
                out.write(DATALOG_HDR + "\n")
                for line in lines:
                    v = line.strip().split(",")
                    if not v or not v[0]:
                        continue
                    v += [""] * max(0, 12 - len(v))
                    row = v[:4] + ["0", "0"] + v[4:8] + ["0"] * pad + [x or "0" for x in v[8:12]] + ["0"] * pad
                    out.write(",".join(row) + "\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, path)
            _LOGGER.info("[DataLog] Converted hist.csv to the format with %d plugs", MAX_SHELLY)
        except OSError as err:
            _LOGGER.warning("[DataLog] Format conversion failed: %s", err)

    def _datalog_append(self, p: list) -> None:
        path = self.datalog_path
        try:
            is_new = not os.path.exists(path)
            with open(path, "a", encoding="utf-8") as f:
                if is_new:
                    f.write(DATALOG_HDR + "\n")
                on_mask, run_mask = p[5], p[6]
                states = [((on_mask >> i) & 1) + ((run_mask >> i) & 1) for i in range(MAX_SHELLY)]
                f.write(",".join(str(v) for v in p[:5]) + f",{self.last_soc},"
                        + ",".join(str(v) for v in p[7:7 + MAX_SHELLY]) + ","
                        + ",".join(str(x) for x in states) + "\n")
            self.werr = False
            self._energy_cache.clear()
            if os.path.getsize(path) > DATALOG_MAX_BYTES:
                self._datalog_rotate()
        except OSError as err:
            self.werr = True
            _LOGGER.error("[DataLog] Write failed: %s", err)

    def _datalog_rotate(self) -> None:
        path = self.datalog_path
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(size - DATALOG_KEEP_BYTES)
            f.readline()
            rest = f.read()
        tmp = path + ".tmp"
        with open(tmp, "wb") as out:
            out.write((DATALOG_HDR + "\n").encode())
            out.write(rest)
        os.replace(tmp, path)
        self._energy_cache.clear()
        _LOGGER.info("[DataLog] Cut: %d -> %d bytes", size, os.path.getsize(path))

    def datalog_exists(self) -> bool:
        return os.path.exists(self.datalog_path)

    def datalog_clear(self) -> None:
        try:
            os.remove(self.datalog_path)
        except FileNotFoundError:
            pass
        self._energy_cache.clear()

    def stats(self) -> dict:
        exists = self.datalog_exists()
        first = 0
        if exists:
            with open(self.datalog_path, encoding="utf-8") as f:
                f.readline()
                try:
                    first = int(f.readline().split(",", 1)[0])
                except ValueError:
                    first = 0
        du = shutil.disk_usage(self.data_dir)
        return {
            "exists": exists,
            "bytes": os.path.getsize(self.datalog_path) if exists else 0,
            "first": first,
            "fs_total": du.total,
            "fs_used": du.used,
            "werr": self.werr,
            "max": DATALOG_MAX_BYTES,
        }

    def energy(self, rng: int) -> dict:
        """Week/month/year bars from hist.csv."""
        now = CLOCK.now()
        today = now.date()
        if self._energy_cache_day != today:
            self._energy_cache.clear()
            self._energy_cache_day = today
        if rng in self._energy_cache:
            return self._energy_cache[rng]
        monthly = rng == 3
        n = 12 if monthly else (30 if rng == 2 else 7)
        e_prod = [0.0] * n
        e_cons = [0.0] * n
        e_feed = [0.0] * n
        e_imp = [0.0] * n
        s_prod = [0.0] * n
        s_cons = [0.0] * n
        cnt = [0] * n
        this_mon = now.year * 12 + now.month - 1
        nom_dt = HISTORY_SAMPLE_S
        prev_ep = 0
        if self.datalog_exists():
            with open(self.datalog_path, encoding="utf-8") as f:
                f.readline()
                for line in f:
                    parts = line.split(",", 4)
                    if len(parts) < 4:
                        continue
                    try:
                        ep = int(parts[0])
                        prod, cons, grid = int(parts[1]), int(parts[2]), int(parts[3])
                    except ValueError:
                        continue
                    if ep < 1_000_000_000:
                        continue
                    dt = nom_dt
                    if prev_ep and ep > prev_ep:
                        dt = ep - prev_ep
                        if dt > 2 * nom_dt:
                            dt = nom_dt
                    prev_ep = ep
                    t = CLOCK.local(ep)
                    if monthly:
                        diff = this_mon - (t.year * 12 + t.month - 1)
                    else:
                        diff = (today - t.date()).days
                    if diff < 0 or diff >= n:
                        continue
                    b = n - 1 - diff
                    q = dt / 3600.0
                    e_prod[b] += prod * q
                    e_cons[b] += cons * q
                    if grid > 0:
                        e_imp[b] += grid * q
                    else:
                        e_feed[b] += -grid * q
                    s_prod[b] += prod
                    s_cons[b] += cons
                    cnt[b] += 1
        midnight = datetime(now.year, now.month, now.day, tzinfo=CLOCK.tz)
        buckets = []
        for i in range(n):
            if monthly:
                back = n - 1 - i
                mon = now.month - 1 - back
                yr = now.year
                while mon < 0:
                    mon += 12
                    yr -= 1
                ts = int(datetime(yr, mon + 1, 1, 12, tzinfo=CLOCK.tz).timestamp())
            else:
                ts = int((midnight - timedelta(days=n - 1 - i)).timestamp())
            c = cnt[i] or 1
            buckets.append({
                "t": ts, "pe": int(e_prod[i] + 0.5), "ce": int(e_cons[i] + 0.5),
                "fe": int(e_feed[i] + 0.5), "ie": int(e_imp[i] + 0.5),
                "pw": int(s_prod[i] / c + 0.5), "cw": int(s_cons[i] / c + 0.5),
            })
        out = {"range": rng, "monthly": monthly, "n": n, "buckets": buckets}
        self._energy_cache[rng] = out
        return out

    # ── Daily archive ───────────────────────────────────────────────────────
    def _daily_load(self) -> list[list[int]]:
        rows = load_json(self.daily_path, [])
        return [r for r in rows if isinstance(r, list) and len(r) == 6]

    def daily_append(self, d: int, prod: float, cons: float, gin: float, gout: float) -> None:
        rec = [d, int(round(max(0, prod))), int(round(max(0, cons))),
               int(round(max(0, gin))), int(round(max(0, gout))), 0]
        rows = [r for r in self._daily_load() if r[0] != d]
        rows.append(rec)
        rows.sort(key=lambda r: r[0])
        save_json(self.daily_path, rows)
        _LOGGER.info("[HistDaily] Day %d added (%d days in total)", d, len(rows))

    def daily_json(self) -> dict:
        return {"days": self._daily_load()}

    def daily_import(self, text: str, overwrite: bool) -> tuple[bool, str]:
        rows: list[list[int]] = []
        first = True
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if first:
                first = False
                if line.startswith("date"):
                    continue
            parts = line.split(",")
            if len(parts) < 5:
                continue
            ds = parts[0]
            if len(ds) != 10 or ds[4] != "-" or ds[7] != "-":
                continue
            try:
                y, m, dd = int(ds[:4]), int(ds[5:7]), int(ds[8:10])
            except ValueError:
                continue
            if not (2000 <= y <= 2100 and 1 <= m <= 12 and 1 <= dd <= 31):
                continue
            if len(rows) >= HDI_MAX_ROWS:
                return False, f"Zu viele Zeilen (Limit {HDI_MAX_ROWS})"

            def num(s: str) -> int:
                try:
                    return max(0, int(float(s)))
                except ValueError:
                    return 0

            flags = HDF_IMPORTED
            if len(parts) > 5 and num(parts[5]) != 0:
                flags |= HDF_LOW_CONFIDENCE
            rows.append([y * 10000 + m * 100 + dd, num(parts[1]), num(parts[2]),
                         num(parts[3]), num(parts[4]), flags])
        if not rows:
            return False, "Keine gültigen Datenzeilen gefunden"
        dedup: dict[int, list[int]] = {}
        for r in sorted(rows, key=lambda r: r[0]):
            dedup[r[0]] = r
        merged = {r[0]: r for r in self._daily_load()}
        for d, r in dedup.items():
            if overwrite or d not in merged:
                merged[d] = r
        out = [merged[d] for d in sorted(merged)]
        if not save_json(self.daily_path, out):
            return False, "Schreiben fehlgeschlagen"
        msg = (f"{len(dedup)} Tage importiert" + (" (bestehende überschrieben)" if overwrite else "")
               + f", {len(out)} Tage gesamt gespeichert")
        return True, msg
