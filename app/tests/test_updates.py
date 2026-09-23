import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from energyoptimizer.updates import UpdateCheck, is_newer, parse_version


def test_version_compare():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2") is None
    assert is_newer("v0.0.2", "0.0.1")
    assert is_newer("0.1.0", "0.0.9")
    assert not is_newer("v0.0.1", "0.0.1")
    assert not is_newer("latest", "0.0.1")


async def _check(tag: str | None, current: str = "0.0.1") -> UpdateCheck:
    async def latest(req):
        if tag is None:
            return web.json_response({"message": "Not Found"}, status=404)
        return web.json_response({"tag_name": tag, "html_url": "https://example.test/r"})

    app = web.Application()
    app.router.add_get("/repos/o/r/releases/latest", latest)
    srv = TestServer(app)
    await srv.start_server()
    up = UpdateCheck(current)
    up.repo = "o/r"
    up.api = f"http://{srv.host}:{srv.port}"
    async with aiohttp.ClientSession() as s:
        await up.check(s)
    await srv.close()
    return up


async def test_newer_release_is_reported():
    up = await _check("v0.0.2")
    st = up.state()
    assert st["available"] and st["latest"] == "0.0.2" and st["url"] == "https://example.test/r"


async def test_same_release_and_no_release():
    assert not (await _check("v0.0.1")).available
    up = await _check(None)
    assert not up.available and up.error == ""
