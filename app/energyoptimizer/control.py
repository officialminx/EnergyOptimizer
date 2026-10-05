"""Surplus control as a pure decision function.

`plan()` decides which loads to switch on or off for one reading of the surplus.
It does no I/O: it works on snapshots of the loads (`Load`), updates their
counters in place and returns the switch actions. `devices.Devices` builds the
snapshots from the live state and carries out the actions; `simulate()` replays a
day of history with the same rules.

Rules (unchanged from the original firmware):

* Switching off goes from the lowest priority up, switching on from the highest
  priority down; within one priority the lower slot comes first.
* A load switches on after `need_on` readings in a row with enough surplus
  (its power plus the switch-on buffer), and off after `need_off` readings in a
  row below minus the switch-off buffer, or while the battery guard holds.
* Minimum on and off times, release windows, daily caps and the no-demand pause
  block a change; `force_eval` skips hysteresis and minimum times once (after
  the automatic was switched on, at the end of a program …).
* As soon as switching a load off brings the surplus back to zero or above,
  the round ends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .const import ER_BATTERY, ER_DEFICIT, ER_SURPLUS, HISTORY_SAMPLE_S


@dataclass
class Load:
    kind: str                 # "shelly" | "ext"
    idx: int
    pri: int
    auto: bool
    pw: float                 # power assumed when switching on
    freed: float              # power that switching off frees
    on: bool = False
    forced_on: bool = False   # bad-weather catch-up holds it on
    sched_on: bool = False    # weekly program holds it on
    force_eval: bool = False
    last_on: float = 0.0
    last_off: float = 0.0
    on_ticks: int = 0
    off_ticks: int = 0
    window_open: bool = True
    daycap: bool = False
    nd_block: bool = False    # paused after "no demand" (self-regulating device)
    auto_active: bool = False


@dataclass
class Params:
    on_margin: float
    off_margin: float
    min_on_s: float
    min_off_s: float
    need_on: int
    need_off: int


@dataclass
class Action:
    kind: str
    idx: int
    on: bool
    reason: int


def params_from_cfg(c: dict, need_on: int, need_off: int) -> Params:
    return Params(on_margin=c["on_margin"], off_margin=c["off_margin"],
                  min_on_s=c["min_on_min"] * 60, min_off_s=c["min_off_min"] * 60,
                  need_on=need_on, need_off=need_off)


def plan_group(loads: list[Load], surplus_w: float, batt_block: bool, p: Params,
               now: float) -> tuple[list[Action], float]:
    """One control round for one group of loads. Returns (actions, power left)."""
    actions: list[Action] = []
    available = surplus_w

    # Switching off: lowest priority first
    for ld in sorted(loads, key=lambda x: (-x.pri, x.idx)):
        if not ld.auto or ld.forced_on or ld.sched_on or not ld.on:
            continue
        force = ld.force_eval
        if not force and now - ld.last_on < p.min_on_s:
            ld.off_ticks = 0
            continue
        if surplus_w < -p.off_margin or batt_block:
            ld.off_ticks += 1
            ld.on_ticks = 0
            if force or ld.off_ticks >= p.need_off:
                actions.append(Action(ld.kind, ld.idx, False, ER_BATTERY if batt_block else ER_DEFICIT))
                ld.on = False
                ld.last_off = now
                ld.auto_active = False
                ld.off_ticks = 0
                ld.force_eval = False
                surplus_w += ld.freed
                available = surplus_w
                if not batt_block and surplus_w >= 0:
                    return actions, available
        else:
            ld.off_ticks = 0
            ld.force_eval = False

    # Switching on: highest priority first
    for ld in sorted(loads, key=lambda x: (x.pri, x.idx)):
        if not ld.auto or not ld.window_open or ld.daycap:
            continue
        if ld.forced_on or ld.on:
            continue
        if ld.nd_block:
            ld.on_ticks = 0
            ld.force_eval = False
            continue
        force = ld.force_eval
        if not force and ld.last_off and now - ld.last_off < p.min_off_s:
            ld.on_ticks = 0
            continue
        if not batt_block and available >= ld.pw + p.on_margin:
            ld.on_ticks += 1
            ld.off_ticks = 0
            if force or ld.on_ticks >= p.need_on:
                actions.append(Action(ld.kind, ld.idx, True, ER_SURPLUS))
                ld.on = True
                ld.last_on = now
                ld.auto_active = True
                ld.on_ticks = 0
                ld.force_eval = False
                available -= ld.pw
        else:
            ld.on_ticks = 0
            ld.force_eval = False
    return actions, available


def plan(loads: list[Load], surplus_w: float, batt_block: bool, p: Params,
         now: float) -> list[Action]:
    """Shelly plugs first; external switches get what is left."""
    acts, remain = plan_group([x for x in loads if x.kind == "shelly"], surplus_w, batt_block, p, now)
    acts2, _ = plan_group([x for x in loads if x.kind == "ext"], remain, batt_block, p, now)
    return acts + acts2


# ── Simulation ──────────────────────────────────────────────────────────────

def window_open_at(e: dict, t: datetime) -> bool:
    """Release window of a load (same rule as Devices.window_open)."""
    days = e["wd"] or 0x7F
    anytime = e["ws"] == e["we"] or (e["ws"] == 0 and e["we"] == 24)
    if anytime and days == 0x7F:
        return True
    if not days & (1 << t.weekday()):
        return False
    if anytime:
        return True
    m = t.hour * 60 + t.minute
    s, en = e["ws"] * 60, e["we"] * 60
    if s < en:
        return s <= m < en
    return m >= s or m < en


@dataclass
class SimLoad:
    kind: str
    idx: int
    name: str
    pw: float
    intervals: list[list[int]] = field(default_factory=list)
    wh: float = 0.0
    pv_wh: float = 0.0


def simulate(points: list[list], n_shelly: int, cfg: dict, tz, params: Params,
             names: dict[tuple[str, int], str]) -> dict:
    """Replays one day of 15-minute history points with the current rules.

    A point is [epoch, prod, cons, grid, batt, on_mask, run_mask, w0 … wN]. The
    measured power of the simulated plugs is taken out of the consumption and the
    grid, and they are added again with their configured power; plugs that are not
    simulated (manual, no automatic) and the battery stay as they were measured.
    External switches have no measurement, so what they really drew stays in. With 15-minute steps this is an
    estimate: hysteresis and minimum times shorter than a step pass in one step.
    """
    step = HISTORY_SAMPLE_S
    loads: list[Load] = []
    sims: dict[tuple[str, int], SimLoad] = {}
    for kind, count in (("shelly", cfg["sh_count"]), ("ext", cfg["ex_count"])):
        for i in range(count):
            e = cfg[kind][i]
            if kind == "shelly" and not e["ip"]:
                continue
            if not e["auto"] or e["pw"] <= 0:
                continue
            loads.append(Load(kind=kind, idx=i, pri=e["pri"], auto=True, pw=float(e["pw"]),
                              freed=float(e["pw"])))
            sims[(kind, i)] = SimLoad(kind, i, names.get((kind, i), ""), float(e["pw"]))
    guard = cfg["batt_grd"]
    batt_block = False
    runtime: dict[tuple[str, int], int] = {k: 0 for k in sims}
    real_feed = real_import = sim_feed = sim_import = 0.0
    prod_sum = 0.0
    for pt in points:
        t, prod, cons, grid = float(pt[0]), float(pt[1]), float(pt[2]), float(pt[3])
        batt = float(pt[4])
        plug_w = sum(max(0.0, float(pt[7 + ld.idx])) for ld in loads
                      if ld.kind == "shelly" and ld.idx < n_shelly and 7 + ld.idx < len(pt))
        base = max(0.0, cons - plug_w)
        dt = datetime.fromtimestamp(t, tz)
        for ld in loads:
            e = cfg[ld.kind][ld.idx]
            ld.window_open = window_open_at(e, dt)
            ld.daycap = e["mx"] > 0 and runtime[(ld.kind, ld.idx)] >= e["mx"] * 60
        if guard > 0:
            discharge = -batt if batt < 0 else 0.0
            batt_block = discharge > guard * 0.5 if batt_block else discharge >= guard
        sim_on = sum(ld.pw for ld in loads if ld.on)
        surplus = prod - base - sim_on
        for a in plan(loads, surplus, batt_block, params, t):
            sl = sims[(a.kind, a.idx)]
            if a.on:
                sl.intervals.append([int(t), int(t + step)])
        # A load that the window or the daily cap stops goes off as well.
        for ld in loads:
            if ld.on and (not ld.window_open or ld.daycap):
                ld.on = False
                ld.last_off = t
        load_w = 0.0
        for ld in loads:
            if not ld.on:
                continue
            sl = sims[(ld.kind, ld.idx)]
            if sl.intervals and sl.intervals[-1][1] >= int(t):
                sl.intervals[-1][1] = int(t + step)
            else:
                sl.intervals.append([int(t), int(t + step)])
            load_w += ld.pw
            runtime[(ld.kind, ld.idx)] += step
        h = step / 3600.0
        free = max(0.0, prod - base)
        pv_left = free
        for ld in sorted((x for x in loads if x.on), key=lambda x: (x.pri, x.idx)):
            sl = sims[(ld.kind, ld.idx)]
            sl.wh += ld.pw * h
            used = min(pv_left, ld.pw)
            sl.pv_wh += used * h
            pv_left -= used
        sim_grid = grid - plug_w + load_w
        prod_sum += prod * h
        if grid < 0:
            real_feed += -grid * h
        else:
            real_import += grid * h
        if sim_grid < 0:
            sim_feed += -sim_grid * h
        else:
            sim_import += sim_grid * h
    out_loads = []
    for sl in sims.values():
        out_loads.append({"kind": sl.kind, "idx": sl.idx, "name": sl.name, "pw": sl.pw,
                          "on": sl.intervals, "kwh": round(sl.wh / 1000, 2),
                          "pv_kwh": round(sl.pv_wh / 1000, 2)})
    return {
        "loads": out_loads, "points": len(points), "step": step,
        "prod_kwh": round(prod_sum / 1000, 2),
        "real": {"feed_kwh": round(real_feed / 1000, 2), "import_kwh": round(real_import / 1000, 2)},
        "sim": {"feed_kwh": round(sim_feed / 1000, 2), "import_kwh": round(sim_import / 1000, 2)},
    }


def day_bounds(day: int, tz) -> tuple[float, float]:
    """Epoch range of a local day given as YYYYMMDD."""
    start = datetime(day // 10000, day // 100 % 100, day % 100, tzinfo=tz)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()
