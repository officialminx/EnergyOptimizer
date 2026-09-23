"""Startpunkt: python -m energyoptimizer [reset-password]"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from aiohttp import web

from . import __version__
from .app import RESET_FLAG, EnergyOptimizer
from .web import WebApi


def data_dir() -> str:
    return os.environ.get("EO_DATA_DIR", "/data")


def reset_password() -> int:
    """Legt die Reset-Markierung ab; die laufende App löscht das Passwort binnen Sekunden."""
    path = os.path.join(data_dir(), RESET_FLAG)
    try:
        with open(path, "w", encoding="ascii") as f:
            f.write("reset\n")
    except OSError as err:
        print(f"{path} konnte nicht geschrieben werden: {err}", file=sys.stderr)
        return 1
    print("Passwort-Reset angefordert. Die Oberfläche in ein paar Sekunden neu laden "
          "und ein neues Passwort festlegen.")
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
