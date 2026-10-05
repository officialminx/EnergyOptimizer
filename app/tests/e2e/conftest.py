"""Browser tests of the web interface in demo mode (?demo=1).

They start a real EnergyOptimizer on a free port with an empty data folder, sign
in and drive the page with Playwright. Playwright's own event loop does not mix
with pytest-asyncio, so they only run on request, in a session of their own:

    pip install -r requirements-dev.txt pytest-playwright
    python -m playwright install chromium
    EO_E2E=1 python -m pytest tests/e2e
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

if os.environ.get("EO_E2E") != "1":
    pytest.skip("browser tests run with EO_E2E=1", allow_module_level=True)
pytest.importorskip("playwright")

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PASSWORD = "e2e-test-pw"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("eo-data")
    sys.path.insert(0, APP_DIR)
    from energyoptimizer.passwords import hash_password
    (data / "settings.json").write_text(json.dumps({"web_pass": hash_password(PASSWORD), "wizard_done": True}))
    port = _free_port()
    env = dict(os.environ, EO_DATA_DIR=str(data), EO_PORT=str(port), EO_BIND="127.0.0.1", EO_MDNS="0",
               EO_UPDATE_CHECK="0")
    proc = subprocess.Popen([sys.executable, "-m", "energyoptimizer"], cwd=APP_DIR, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/favicon.ico", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("EnergyOptimizer did not start")
    yield base
    proc.terminate()
    proc.wait(10)


@pytest.fixture
def app(page, server):
    """Signed-in page on the demo dashboard; fails on any script error."""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server + "/")
    page.fill("input[type=password]", PASSWORD)
    page.keyboard.press("Enter")
    # The sign-in page lives at "/" as well: wait for the main page itself.
    page.wait_for_selector(".btm-nav")
    page.evaluate("try{localStorage.clear()}catch(e){}")
    page.goto(server + "/?demo=1")
    page.wait_for_selector("#opt-list .opt-row", timeout=10000)
    yield page
    assert errors == [], errors
