"""Konstanten: Grenzen, Intervalle, Ereignis- und Alarmcodes."""

MAX_SHELLY = 4
MAX_EXT = 4
MAX_SCHED = 4

SHELLY_POLL_S = 60
HISTORY_SAMPLE_S = 900
HISTORY_MAX = 96
SHELLY_CMD_LOCK_S = 5
SHELLY_FAIL_RECOVER = 3
SHELLY_RECOVER_COOLDOWN_S = 300
RUN_START_GRACE_S = 180
ND_RETRY_S = 1800
SCHED_RETRY_S = 60
BATT_GUARD_RELEASE_FACTOR = 0.5
SL_RETRY_S = 30
SL_DEV_POLL_S = 900
SL_MAX_DEV = 12
HOSTNAME = "energyoptimizer"

# ── Ereignistypen (events.h) ────────────────────────────────────────────────
EV_BOOT = 1
EV_SWITCH = 2
EV_REACH = 3
EV_AUTOMODE = 4
EV_SOLARLOG = 5
EV_FAILSAFE = 6
EV_IPMOVE = 7
EV_CONFIG = 8
EV_ERROR = 9
EV_ALARM = 10
EV_AUTHFAIL = 11
EV_RUN = 12

# ── Gründe (events.h, Reihenfolge = Zahlenwert) ─────────────────────────────
ER_NONE = 0
ER_SURPLUS = 1
ER_DEFICIT = 2
ER_MANUAL = 3
ER_MQTT = 4
ER_FORCED = 5
ER_FORCED_END = 6
ER_FAILSAFE = 7
ER_CMD_FAIL = 8
ER_WINDOW = 9
ER_TIMER = 10
ER_BATTERY = 11
ER_NO_DEMAND = 12
ER_SCHEDULE = 18
ER_DAY_CAP = 19

EV_TYPE_TXT = {
    EV_BOOT: "Neustart",
    EV_SWITCH: "Schaltung",
    EV_REACH: "Erreichbarkeit",
    EV_AUTOMODE: "Automatik",
    EV_SOLARLOG: "SolarLog",
    EV_FAILSAFE: "Fail-Safe",
    EV_IPMOVE: "IP-Wechsel",
    EV_CONFIG: "Einstellungen",
    EV_ERROR: "Systemfehler",
    EV_ALARM: "Störung",
    EV_AUTHFAIL: "Fehlanmeldungen",
    EV_RUN: "Betrieb",
    13: "Tesla-Laden",
}

ER_TXT = {
    ER_SURPLUS: "Überschuss reichte",
    ER_DEFICIT: "Defizit über Ausschalt-Puffer",
    ER_MANUAL: "manuell",
    ER_MQTT: "über MQTT",
    ER_FORCED: "Schlechtwetter-Nachlauf",
    ER_FORCED_END: "Nachlauf beendet",
    ER_FAILSAFE: "Fail-Safe",
    ER_CMD_FAIL: "Befehl fehlgeschlagen",
    ER_WINDOW: "ausserhalb des Freigabefensters",
    ER_TIMER: "Befristung abgelaufen",
    ER_BATTERY: "Batterie-Vorrang – Speicher entlädt",
    ER_NO_DEMAND: "Gerät zeigt keinen Bedarf (Eigenregelung)",
    ER_SCHEDULE: "Zeitschaltuhr",
    ER_DAY_CAP: "Tageslimit erreicht",
}

# Startgrund: im Container gibt es nur den normalen Start.
RST_CONTAINER = 3  # "Software-Neustart" in der UI-Tabelle
RST_TXT = "Container-Start"

# ── Alarme (alarms.h) ───────────────────────────────────────────────────────
AL_SOLARLOG = 0
AL_FAILSAFE = 1
AL_NOPROD = 2
AL_INVERTER = 3
AL_NVS = 4
AL_SHELLY0 = 5
AL_COUNT = AL_SHELLY0 + MAX_SHELLY

NS_INFO = 0
NS_WARN = 1
NS_CRIT = 2
