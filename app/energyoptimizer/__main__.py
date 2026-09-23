"""Startpunkt: python -m energyoptimizer [reset-password]"""

from __future__ import annotations

import asyncio
import getpass
import logging
import os
import signal
import sys

from aiohttp import web

from . import __version__
from .app import RESET_FLAG, EnergyOptimizer
from .passwords import hash_password
from .settings import MAX_PASSWORD_LEN, MIN_PASSWORD_LEN
from .web import WebApi


def data_dir() -> str:
    return os.environ.get("EO_DATA_DIR", "/data")


def reset_password() -> int:
    """Asks for a new password in the terminal and hands its hash to the running
    app, which switches to it within seconds. The web interface stays protected
    the whole time: there is no moment without a password."""
    if not sys.stdin.isatty():
        print("Bitte im Terminal ausführen, damit das neue Passwort abgefragt werden kann:\n"
              "  docker compose exec energyoptimizer python -m energyoptimizer reset-password",
              file=sys.stderr)
        return 2
    pw = getpass.getpass("Neues Web-Passwort: ")
    if not MIN_PASSWORD_LEN <= len(pw) <= MAX_PASSWORD_LEN:
        print(f"Das Passwort muss {MIN_PASSWORD_LEN} bis {MAX_PASSWORD_LEN} Zeichen lang sein.",
              file=sys.stderr)
        return 1
    if getpass.getpass("Wiederholen: ") != pw:
        print("Die Passwörter stimmen nicht überein.", file=sys.stderr)
        return 1
    path = os.path.join(data_dir(), RESET_FLAG)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(hash_password(pw) + "\n")
    except OSError as err:
        print(f"{path} konnte nicht geschrieben werden: {err}", file=sys.stderr)
        return 1
    print("Neues Passwort gespeichert. Es gilt in ein paar Sekunden; danach damit anmelden.")
    return 0


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("EO_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log = logging.getLogger("energyoptimizer")
    ddir = data_dir()
    port = int(os.environ.get("EO_PORT", "80"))

    eo = EnergyOptimizer(ddir, port)
    await eo.start()
    api = WebApi(eo)
    runner = web.AppRunner(api.build(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=os.environ.get("EO_BIND", "0.0.0.0"), port=port)
    await site.start()
    log.info("EnergyOptimizer %s läuft auf Port %d (Daten: %s)", __version__, port, ddir)
    if not eo.settings.cfg["web_pass"]:
        log.warning("Noch kein Web-Passwort gesetzt – beim ersten Aufruf der Oberfläche festlegen")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    log.info("Beende – speichere Zählerstände und Protokoll")
    await runner.cleanup()
    await eo.stop()


if __name__ == "__main__":
    if sys.argv[1:] == ["reset-password"]:
        sys.exit(reset_password())
    if sys.argv[1:]:
        print("Aufruf: python -m energyoptimizer [reset-password]", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main())
