"""Entry point: python -m energyoptimizer [reset-password | apply-update <container>]"""

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
from .installer import DockerError, apply_update
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
        print("Run this in a terminal so the new password can be entered:\n"
              "  docker compose exec energyoptimizer python -m energyoptimizer reset-password",
              file=sys.stderr)
        return 2
    pw = getpass.getpass("New web password: ")
    if not MIN_PASSWORD_LEN <= len(pw) <= MAX_PASSWORD_LEN:
        print(f"The password must be {MIN_PASSWORD_LEN} to {MAX_PASSWORD_LEN} characters long.",
              file=sys.stderr)
        return 1
    if getpass.getpass("Repeat: ") != pw:
        print("The passwords do not match.", file=sys.stderr)
        return 1
    path = os.path.join(data_dir(), RESET_FLAG)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(hash_password(pw) + "\n")
    except OSError as err:
        print(f"{path} could not be written: {err}", file=sys.stderr)
        return 1
    print("New password saved. It applies within a few seconds; sign in with it then.")
    return 0


def run_apply_update(target: str) -> int:
    """Helper container started by the web interface: replaces the app container."""
    try:
        asyncio.run(apply_update(target, log=lambda m: print(m, flush=True)))
    except DockerError as err:
        print(f"Update failed: {err}", file=sys.stderr, flush=True)
        return 1
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
    log.info("EnergyOptimizer %s running on port %d (data: %s)", __version__, port, ddir)
    if not eo.settings.cfg["web_pass"]:
        log.warning("No web password yet – set it when opening the web interface for the first time")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    log.info("Stopping – saving counters and event log")
    await runner.cleanup()
    await eo.stop()


if __name__ == "__main__":
    if sys.argv[1:] == ["reset-password"]:
        sys.exit(reset_password())
    if len(sys.argv) == 3 and sys.argv[1] == "apply-update":
        sys.exit(run_apply_update(sys.argv[2]))
    if sys.argv[1:]:
        print("Usage: python -m energyoptimizer [reset-password]", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main())
