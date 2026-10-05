from datetime import datetime

from energyoptimizer import control
from energyoptimizer.clock import CLOCK
from energyoptimizer.const import ER_BATTERY, ER_DEFICIT, ER_SURPLUS, MAX_SHELLY
from energyoptimizer.settings import defaults

P = control.Params(on_margin=150, off_margin=200, min_on_s=420, min_off_s=300, need_on=3, need_off=3)


def ld(i, pri, pw=1000, **kw):
    return control.Load(kind="shelly", idx=i, pri=pri, auto=True, pw=pw, freed=pw, **kw)


def test_switch_on_needs_hysteresis_and_priority_order():
    loads = [ld(0, 2), ld(1, 1)]
    for t in range(2):
        assert control.plan(loads, 1300, False, P, 1000 + t * 60) == []
    acts = control.plan(loads, 1300, False, P, 1120)
    # Only one fits; priority 1 (slot 1) wins.
    assert [(a.idx, a.on, a.reason) for a in acts] == [(1, True, ER_SURPLUS)]
    assert loads[1].on and loads[1].auto_active and loads[0].on_ticks == 0


def test_force_eval_skips_hysteresis_and_min_times():
    loads = [ld(0, 1, force_eval=True, last_off=990)]
    acts = control.plan(loads, 1300, False, P, 1000)
    assert acts and acts[0].on and not loads[0].force_eval


def test_switch_off_lowest_priority_first_and_stop_when_enough():
    loads = [ld(0, 1, on=True, last_on=0), ld(1, 2, on=True, last_on=0)]
    acts = []
    for t in range(3):
        acts += control.plan(loads, -500, False, P, 1000 + t * 60)
    # Freeing 1000 W of the priority-2 load brings the surplus above zero: stop there.
    assert [(a.idx, a.on, a.reason) for a in acts] == [(1, False, ER_DEFICIT)]
    assert loads[0].on


def test_min_on_time_blocks_switch_off():
    loads = [ld(0, 1, on=True, last_on=900)]
    for t in range(5):
        assert control.plan(loads, -2000, False, P, 1000 + t * 30) == []


def test_battery_guard_switches_off_and_blocks_on():
    loads = [ld(0, 1, on=True, last_on=0), ld(1, 2)]
    acts = []
    for t in range(3):
        acts += control.plan(loads, 5000, True, P, 1000 + t * 60)
    assert [(a.idx, a.on, a.reason) for a in acts] == [(0, False, ER_BATTERY)]


def test_ext_loads_get_what_is_left():
    sh = ld(0, 1, pw=1000)
    ex = control.Load(kind="ext", idx=0, pri=1, auto=True, pw=800, freed=800)
    loads = [sh, ex]
    for t in range(3):
        acts = control.plan(loads, 1300, False, P, 1000 + t * 60)
    assert sh.on and not ex.on
    loads2 = [ld(0, 1, pw=1000), control.Load(kind="ext", idx=0, pri=1, auto=True, pw=800, freed=800)]
    for t in range(3):
        acts = control.plan(loads2, 2200, False, P, 1000 + t * 60)
    assert {(a.kind, a.on) for a in acts} == {("shelly", True), ("ext", True)}


def test_window_and_daycap_and_nd_block():
    for kw in ({"window_open": False}, {"daycap": True}, {"nd_block": True}):
        loads = [ld(0, 1, **kw)]
        for t in range(4):
            assert control.plan(loads, 5000, False, P, 1000 + t * 60) == []


def test_window_open_at_over_midnight():
    e = {"ws": 22, "we": 6, "wd": 0x7F}
    assert control.window_open_at(e, datetime(2026, 6, 10, 23, 0))
    assert control.window_open_at(e, datetime(2026, 6, 10, 5, 0))
    assert not control.window_open_at(e, datetime(2026, 6, 10, 12, 0))
    assert not control.window_open_at({"ws": 0, "we": 24, "wd": 1}, datetime(2026, 6, 10, 12, 0))


def _point(t, prod, cons, grid, plug_w=0):
    return [t, prod, cons, grid, 0, 0, 0] + [plug_w] + [0] * (MAX_SHELLY - 1)


def test_simulation_uses_surplus_that_was_fed_in():
    c = defaults()
    c["shelly"][0].update(ip="1.2.3.4", pw=2000, auto=True, pri=1)
    c["sh_count"] = 1
    start = datetime(2026, 6, 10, 8, 0, tzinfo=CLOCK.tz).timestamp()
    pts = []
    for k in range(16):           # 08:00–12:00, 3.5 kW PV, 0.5 kW house, nothing switched
        pts.append(_point(int(start + k * 900), 3500, 500, -3000))
    p = control.params_from_cfg(c, 1, 1)
    res = control.simulate(pts, 1, c, CLOCK.tz, p, {("shelly", 0): "Boiler"})
    load = res["loads"][0]
    assert load["name"] == "Boiler" and load["on"]
    assert load["on"][0][0] == int(start) and load["on"][-1][1] == int(start + 16 * 900)
    assert load["kwh"] == 8.0 and load["pv_kwh"] == 8.0
    assert res["real"]["feed_kwh"] == 12.0 and res["sim"]["feed_kwh"] == 4.0


def test_simulation_takes_measured_plug_power_out_of_consumption():
    c = defaults()
    c["shelly"][0].update(ip="1.2.3.4", pw=1500, auto=True, pri=1)
    c["sh_count"] = 1
    start = datetime(2026, 6, 10, 12, 0, tzinfo=CLOCK.tz).timestamp()
    # The plug really drew 1.5 kW: without it the house used 0.5 kW and 1.5 kW were spare.
    pts = [_point(int(start + k * 900), 2200, 2000, -200, plug_w=1500) for k in range(4)]
    res = control.simulate(pts, 1, c, CLOCK.tz, control.params_from_cfg(c, 1, 1), {})
    assert res["loads"][0]["kwh"] == 1.5
