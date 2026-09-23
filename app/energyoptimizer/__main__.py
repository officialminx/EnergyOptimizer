"""Startpunkt: python -m energyoptimizer"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from aiohttp import web

from . import __version__
from .app import EnergyOptimizer
from .web import WebApi


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("EO_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log = logging.getLogger("energyoptimizer")
    data_dir = os.environ.get("EO_DATA_DIR", "/data")
    port = int(os.environ.get("EO_PORT", "8080"))

    eo = EnergyOptimizer(data_dir)
    await eo.start()
    api = WebApi(eo)
    runner = web.AppRunner(api.build(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=os.environ.get("EO_BIND", "0.0.0.0"), port=port)
    await site.start()
    log.info("EnergyOptimizer %s läuft auf Port %d (Daten: %s)", __version__, port, data_dir)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    log.info("Beende – speichere Zählerstände und Protokoll")
    await runner.cleanup()
    await eo.stop()


if __name__ == "__main__":
    asyncio.run(main())
