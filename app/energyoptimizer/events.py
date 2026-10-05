"""Event log: ring buffer with 1500 entries.

An entry is [epoch, uptime_s, type, dev, reason, flags, surplus_w, text];
flags bit0 = ON, bit1 = surplus value valid, bit2 = dry run (nothing switched).
"""

from __future__ import annotations

import csv
import io
import os
from collections import deque

from .clock import CLOCK
from .const import ER_TXT, EV_ALARM, EV_BOOT, EV_TYPE_TXT, RST_TXT
from .storage import load_json, save_json

EV_MAX = 1500
EV_TEXT = 40
EVF_ON = 0x01
EVF_SURP = 0x02
EVF_DRY = 0x04


class EventLog:
    def __init__(self, data_dir: str) -> None:
        self.path = os.path.join(data_dir, "events.json")
        self.ev: deque[list] = deque(maxlen=EV_MAX)
        self.dirty = False

    def load(self) -> None:
        for r in load_json(self.path, [])[-EV_MAX:]:
            if isinstance(r, list) and len(r) == 8:
                self.ev.append(r)

    def log(self, type_: int, dev: int, reason: int, flag: bool, text: str | None,
            surplus_w: float | None = None, dry: bool = False) -> None:
        flags = (EVF_ON if flag else 0) | (EVF_DRY if dry else 0)
        surplus = 0
        if surplus_w is not None:
            surplus = int(round(max(-32000.0, min(32000.0, surplus_w))))
            flags |= EVF_SURP
        dev = dev if -1 <= dev < 127 else -1
        self.ev.append([int(CLOCK.time()), int(CLOCK.mono()), type_, dev, reason, flags,
                        surplus, (text or "")[:EV_TEXT]])
        self.dirty = True

    def flush(self) -> None:
        if self.dirty:
            self.dirty = False
            save_json(self.path, list(self.ev))

    def count(self) -> int:
        return len(self.ev)

    def build(self, max_n: int, dev_filter: int) -> dict:
        max_n = max(1, min(400, max_n))
        out: list[list] = []
        for r in reversed(self.ev):
            if len(out) >= max_n:
                break
            if dev_filter == -1 and r[3] >= 0:
                continue
            if dev_filter >= 0 and r[3] != dev_filter:
                continue
            out.append(r)
        return {"n": len(self.ev), "ev": out}

    def clear(self) -> bool:
        self.ev.clear()
        self.dirty = True
        self.flush()
        return True

    def csv(self) -> str:
        buf = io.StringIO()
        buf.write("datetime,epoch,uptime_s,type_id,type,device,name,state,reason_id,reason,surplus_w,dry_run\n")
        w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        for r in self.ev:
            epoch, up, typ, dev, reason, flags, surplus, text = r
            when = CLOCK.local(epoch).strftime("%Y-%m-%d %H:%M:%S") if epoch else ""
            if typ == EV_BOOT:
                rtxt = RST_TXT
            elif typ == EV_ALARM:
                rtxt = ""
            else:
                rtxt = ER_TXT.get(reason, "")
            w.writerow([when, epoch, up, typ, EV_TYPE_TXT.get(typ, "unbekannt"), dev, text,
                        1 if flags & EVF_ON else 0, reason, rtxt,
                        surplus if flags & EVF_SURP else "", 1 if flags & EVF_DRY else 0])
        return buf.getvalue()
