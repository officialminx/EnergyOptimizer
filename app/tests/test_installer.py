import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from energyoptimizer import installer
from energyoptimizer.installer import DockerApi, DockerError, Installer, apply_update, clone_config, split_ref
from energyoptimizer.updates import UpdateCheck

OLD_ID = "a" * 64
IMG_OLD, IMG_NEW = "sha256:old", "sha256:new"
IMAGE_CFG = {"Env": ["PATH=/usr/bin", "EO_BUILD=oldsha", "EO_PORT=80"], "Cmd": ["python", "-m", "energyoptimizer"],
             "Labels": {"org.opencontainers.image.version": "0.1.0"}}


def old_container() -> dict:
    return {
        "Id": OLD_ID, "Name": "/energyoptimizer", "Image": IMG_OLD,
        "Config": {"Image": "ghcr.io/o/energyoptimizer:latest", "Hostname": OLD_ID[:12],
                   "Env": ["PATH=/usr/bin", "EO_BUILD=oldsha", "EO_PORT=8080", "TZ=Europe/Zurich"],
                   "Cmd": ["python", "-m", "energyoptimizer"],
                   "Labels": {"org.opencontainers.image.version": "0.1.0", "com.docker.compose.service": "eo"}},
        "HostConfig": {"NetworkMode": "host", "Binds": ["/srv/data:/data"],
                       "RestartPolicy": {"Name": "unless-stopped"}},
        "NetworkSettings": {"Networks": {"host": {}}},
        "Mounts": [{"Source": "/run/docker.sock", "Destination": installer.SOCKET}],
        "State": {"Running": True},
    }


class FakeDocker:
    def __init__(self, new_runs=True, pull_error=""):
        self.calls: list[tuple[str, str]] = []
        self.created: list[dict] = []
        self.names = {"energyoptimizer": OLD_ID}
        self.new_runs = new_runs
        self.pull_error = pull_error
        self.new_image = IMG_NEW
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self.server = TestServer(app)

    async def api(self) -> DockerApi:
        await self.server.start_server()
        return DockerApi(base=f"http://{self.server.host}:{self.server.port}")

    async def handle(self, req):
        path = req.path
        self.calls.append((req.method, path))
        if req.method == "GET" and path.startswith("/containers/"):
            ref = path.split("/")[2]
            if ref in (OLD_ID, "energyoptimizer") or self.names.get(ref) == OLD_ID:
                return web.json_response(old_container())
            if ref == "newid":
                state = {"Running": self.new_runs, "Status": "running" if self.new_runs else "exited"}
                return web.json_response({"Id": "newid", "State": state})
            return web.json_response({"message": "no such container"}, status=404)
        if req.method == "GET" and path.startswith("/images/"):
            ref = path.split("/")[2]
            if ref == IMG_OLD:
                return web.json_response({"Id": IMG_OLD, "Config": IMAGE_CFG})
            return web.json_response({"Id": self.new_image, "Config": IMAGE_CFG})
        if path == "/images/create":
            line = json.dumps({"error": self.pull_error} if self.pull_error else {"status": "Downloaded"})
            return web.Response(text='{"status":"Pulling"}\n' + line + "\n")
        if path == "/containers/create":
            body = await req.json()
            self.created.append({"name": req.query.get("name"), **body})
            made = "newid" if req.query.get("name") == "energyoptimizer" else "helperid"
            return web.json_response({"Id": made}, status=201)
        return web.Response(status=204)

    def did(self, method, path):
        return (method, path) in self.calls


def test_split_ref():
    assert split_ref("ghcr.io/o/e:0.1.0") == ("ghcr.io/o/e", "0.1.0")
    assert split_ref("ghcr.io/o/e") == ("ghcr.io/o/e", "latest")
    assert split_ref("localhost:5000/e") == ("localhost:5000/e", "latest")
    with pytest.raises(DockerError):
        split_ref("e@sha256:abc")


def test_clone_config_drops_old_image_defaults():
    cfg = clone_config(old_container(), IMAGE_CFG, "ghcr.io/o/energyoptimizer:latest")
    assert cfg["Env"] == ["EO_PORT=8080", "TZ=Europe/Zurich"]   # PATH / EO_BUILD come from the new image
    assert "Cmd" not in cfg and "Hostname" not in cfg
    assert cfg["Labels"] == {"com.docker.compose.service": "eo"}
    assert cfg["HostConfig"]["Binds"] == ["/srv/data:/data"]


async def test_prepare_pulls_and_starts_helper():
    fake = FakeDocker()
    api = await fake.api()
    try:
        ref = await installer.prepare(api)
    finally:
        await fake.server.close()
    assert ref == "ghcr.io/o/energyoptimizer:latest"
    helper = fake.created[0]
    assert helper["name"] == installer.HELPER_NAME
    assert helper["Cmd"] == ["python", "-m", "energyoptimizer", "apply-update", OLD_ID]
    assert helper["HostConfig"]["Binds"] == [f"/run/docker.sock:{installer.SOCKET}"]
    assert fake.did("POST", "/containers/helperid/start")


async def test_prepare_errors():
    fake = FakeDocker(pull_error="denied")
    api = await fake.api()
    try:
        with pytest.raises(DockerError, match="denied"):
            await installer.prepare(api)
        fake.pull_error, fake.new_image = "", IMG_OLD
        with pytest.raises(DockerError, match="already installed"):
            await installer.prepare(api)
    finally:
        await fake.server.close()


async def test_apply_update_replaces_container(monkeypatch):
    monkeypatch.setattr(installer, "SETTLE_S", 0)
    fake = FakeDocker()
    api = await fake.api()
    try:
        await apply_update(OLD_ID, api, log=lambda m: None)
    finally:
        await fake.server.close()
    new = fake.created[0]
    assert new["name"] == "energyoptimizer" and new["Image"] == "ghcr.io/o/energyoptimizer:latest"
    assert new["HostConfig"]["Binds"] == ["/srv/data:/data"]
    order = [c for c in fake.calls if c[0] != "GET"]
    assert order[:3] == [("POST", f"/containers/{OLD_ID}/stop"), ("POST", f"/containers/{OLD_ID}/rename"),
                         ("POST", "/containers/create")]
    assert fake.did("POST", "/containers/newid/start") and fake.did("DELETE", f"/containers/{OLD_ID}")


async def test_apply_update_rolls_back_when_new_container_dies(monkeypatch):
    monkeypatch.setattr(installer, "SETTLE_S", 0)
    fake = FakeDocker(new_runs=False)
    api = await fake.api()
    try:
        with pytest.raises(DockerError):
            await apply_update(OLD_ID, api, log=lambda m: None)
    finally:
        await fake.server.close()
    assert fake.did("DELETE", "/containers/newid")
    assert fake.calls.count(("POST", f"/containers/{OLD_ID}/start")) == 1
    assert not fake.did("DELETE", f"/containers/{OLD_ID}")


async def test_installer_state_machine(monkeypatch):
    up = UpdateCheck("0.1.0")
    inst = Installer(up, api=DockerApi(base="http://127.0.0.1:1"))
    assert not inst.start()          # no update available
    up.latest = "0.1.1"
    seen = []

    async def fake_prepare(api):
        seen.append(inst.phase)
        return "x"

    monkeypatch.setattr(installer, "prepare", fake_prepare)
    assert inst.start() and not inst.start()    # second click while running is refused
    await inst._task
    assert seen == ["pulling"] and inst.phase == "starting"

    async def failing(api):
        raise DockerError("boom")

    monkeypatch.setattr(installer, "prepare", failing)
    inst.phase = "idle"
    assert inst.start()
    await inst._task
    assert inst.phase == "error" and inst.error == "boom"
