import asyncio
from datetime import datetime

import pytest

from energyoptimizer import sched
from energyoptimizer.const import ER_SURPLUS, EV_SWITCH

from .fakes import FakeClock, FakeShelly, make_app


@pytest.fixture
async def env(tmp_path, monkeypatch):
    clock = FakeClock(monkeypatch, datetime(2026, 6, 10, 12, 0))  # Mittwoch, Mittag
    eo = await make_app(tmp_path)
    shellies = [FakeShelly("Boiler"), FakeShelly("Pumpe")]
    c = eo.settings.cfg
    for i, sh in enumerate(shellies):
        c["shelly"][i].update(ip=await sh.start(), name=sh.name, pw=1000, pri=i + 1, auto=True)
    c["sh_count"] = 2
    yield eo, clock, shellies
    for sh in shellies:
        await sh.close()
    await eo.session.close()


async def test_switches_on_after_hysteresis_and_respects_priority(env):
    eo, clock, (boiler, pumpe) = env
    dv = eo.devices
    # 1300 W Überschuss reicht für EIN Gerät (1000 W + 150 W Puffer), nicht für zwei.
    for tick in range(3):
        await dv.distribute(1300)
        clock.advance(60)
    assert boiler.cmds == [True]
    assert pumpe.cmds == []
    assert dv.st["shelly"][0].on and dv.st["shelly"][0].auto_active
    ev = [e for e in eo.events.ev if e[2] == EV_SWITCH]
    assert ev[-1][4] == ER_SURPLUS and ev[-1][6] == 1300


async def test_switches_off_after_deficit_but_not_within_min_on(env):
    eo, clock, (boiler, _) = env
    dv = eo.devices
    for _ in range(3):
        await dv.distribute(2000)
        clock.advance(60)
    assert boiler.on
    # Defizit direkt nach dem Einschalten: Mindest-EIN-Zeit (7 min) sperrt
    for _ in range(3):
        await dv.distribute(-500)
        clock.advance(60)
    assert boiler.on
    clock.advance(5 * 60)
    for _ in range(3):
        await dv.distribute(-500)
        clock.advance(60)
    assert not boiler.on
    assert boiler.cmds == [True, False]


async def test_battery_guard_blocks_and_switches_off(env):
    eo, clock, (boiler, _) = env
    dv = eo.devices
    dv.batt_update(True, -150)  # Speicher entlädt 150 W ≥ 100 W Schwelle
    assert dv.batt_block
    for _ in range(5):
        await dv.distribute(3000)
        clock.advance(60)
    assert boiler.cmds == []
    dv.batt_update(True, -80)  # Freigabe erst unter 50 W
    assert dv.batt_block
    dv.batt_update(True, -40)
    assert not dv.batt_block


async def test_schedule_runs_without_surplus_and_manual_off_skips(env):
    eo, clock, (boiler, _) = env
    dv = eo.devices
    e = eo.settings.cfg["shelly"][0]
    e["auto"] = False
    e["sch"] = sched.from_str("660,780,127,1")  # täglich 11:00–13:00
    await dv.sched_tick("shelly")
    assert boiler.on and dv.st["shelly"][0].sched_on
    assert dv.apply_command("shelly", 0, "off")
    await __import__("asyncio").sleep(0.05)
    assert not boiler.on
    await dv.sched_tick("shelly")
    assert not boiler.on  # ausgesetzt bis zum Ende dieses Programms


async def test_day_cap_switches_off(env):
    eo, clock, (boiler, _) = env
    dv = eo.devices
    eo.settings.cfg["shelly"][0]["mx"] = 1  # Tageslimit 1 min
    for _ in range(3):
        await dv.distribute(2000)
        clock.advance(60)
    assert boiler.on
    await dv.schedule_tick("shelly")
    clock.advance(61)
    await dv.schedule_tick("shelly")
    assert not boiler.on
    assert dv.daycap_reached("shelly", 0)


async def test_window_closed_switches_auto_device_off(env):
    eo, clock, (boiler, _) = env
    dv = eo.devices
    for _ in range(3):
        await dv.distribute(2000)
        clock.advance(60)
    assert boiler.on
    eo.settings.cfg["shelly"][0].update(ws=6, we=10)  # Fenster 6–10 Uhr, jetzt ist Mittag
    await dv.schedule_tick("shelly")
    assert not boiler.on


async def test_poll_reads_status_and_learns_id(env):
    eo, clock, (boiler, _) = env
    boiler.on = True
    boiler.apower = 950
    await eo.devices.poll_all()
    s = eo.devices.st["shelly"][0]
    assert s.reachable and s.on and s.apower_w == 950 and s.temp_c == 40.2
    assert eo.settings.cfg["shelly"][0]["id"] == "shellyplus1pm-boiler"


async def test_hysteresis_ticks():
    from energyoptimizer.settings import hyst_ticks
    assert hyst_ticks(180, 1) == 3
    assert hyst_ticks(0, 1) == 1
    assert hyst_ticks(3600, 1) == 60
    assert hyst_ticks(100, 5) == 1


def test_schedule_over_midnight_uses_start_day():
    sl = sched.from_str("1320,360,1,1")  # Mo 22:00 – Di 06:00
    assert sched.active(sl, datetime(2026, 6, 8, 23, 0)) == 0   # Montag 23 Uhr
    assert sched.active(sl, datetime(2026, 6, 9, 5, 0)) == 0    # Dienstag 5 Uhr
    assert sched.active(sl, datetime(2026, 6, 10, 5, 0)) == -1  # Mittwoch 5 Uhr
    assert sched.to_str(sl) == "1320,360,1,1"
    assert sched.from_str("60,120,3")[0] == [60, 120, 3, True]  # altes Format ohne Flag


async def test_no_command_when_plug_already_in_target_state(env):
    eo, clock, (boiler, _) = env
    dv = eo.devices
    await dv.poll_all()                     # the plug reports: off
    assert await dv.set("shelly", 0, False)
    assert boiler.cmds == []                # already off, nothing sent
    boiler.on = True                        # switched on at the device itself
    await dv.poll_all()
    assert await dv.set("shelly", 0, True)
    assert boiler.cmds == []
    assert await dv.set("shelly", 0, False)
    assert boiler.cmds == [False]
    # A manual command always goes out, even if the state looks the same.
    dv.apply_command("shelly", 0, "off")
    for _ in range(20):
        if len(boiler.cmds) == 2:
            break
        await asyncio.sleep(0.01)
    assert boiler.cmds == [False, False]


def test_mqtt_publishes_only_changes(tmp_path):
    from energyoptimizer.mqtt import Mqtt

    class Client:
        def __init__(self):
            self.sent = []

        def publish(self, topic, payload, retain=False):
            self.sent.append((topic, payload))

    class App:
        pass

    m = Mqtt(App())
    m.client = Client()
    m._pub("eo/a", {"w": 1})
    m._pub("eo/a", {"w": 1})
    m._pub("eo/a", {"w": 2})
    m._pub("eo/b", "ON")
    m._pub("eo/b", "ON")
    assert m.client.sent == [("eo/a", '{"w": 1}'), ("eo/a", '{"w": 2}'), ("eo/b", "ON")]
