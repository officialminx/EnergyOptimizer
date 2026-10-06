"""Installiert ein Update direkt aus der Oberfläche.

Der Container spricht dafür über den Docker-Socket (in docker-compose.yml eingebunden)
mit dem Docker-Daemon. Ablauf:

1. Der laufende Container lädt das neue Image (``docker pull``).
2. Aus dem neuen Image startet er einen kurzlebigen Helfer (``apply-update``). Der Helfer
   ist nötig, weil sich ein Container nicht selbst ersetzen kann.
3. Der Helfer stoppt den alten Container, legt ihn mit gleicher Konfiguration (Volumes,
   Netzwerk, Umgebung, Neustart-Regel) aus dem neuen Image neu an und startet ihn. Läuft
   der neue Container nicht, stellt er den alten wieder her.

Ohne Socket bleibt alles beim Hinweis mit dem ``docker compose``-Befehl.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import stat
from typing import Any

import aiohttp

from .updates import UpdateCheck

_LOGGER = logging.getLogger(__name__)

SOCKET = os.environ.get("EO_DOCKER_SOCKET", "/var/run/docker.sock")
API_VERSION = "v1.41"
HELPER_NAME = "energyoptimizer-updater"
STOP_TIMEOUT_S = 30
SETTLE_S = 20
READY_TIMEOUT_S = 120


class DockerError(Exception):
    pass


class DockerApi:
    """Minimaler Docker-Engine-Client über den Unix-Socket (``base`` nur für Tests)."""

    def __init__(self, sock: str = SOCKET, base: str | None = None) -> None:
        self._sock = None if base else sock
        self._base = (base or f"http://docker/{API_VERSION}").rstrip("/")

    async def call(self, method: str, path: str, *, body: Any = None, params: dict | None = None,
                   ok: tuple[int, ...] = (200, 201, 204), timeout: float = 60) -> tuple[int, Any]:
        connector = aiohttp.UnixConnector(path=self._sock) if self._sock else None
        try:
            async with aiohttp.ClientSession(connector=connector,
                                             timeout=aiohttp.ClientTimeout(total=timeout)) as s, \
                    s.request(method, self._base + path, json=body, params=params) as resp:
                raw = await resp.read()
                status = resp.status
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            raise DockerError(f"Docker not reachable: {err}") from err
        if status not in ok:
            try:
                msg = json.loads(raw).get("message", "")
            except (ValueError, AttributeError):
                msg = raw[:200].decode("utf-8", "replace")
            raise DockerError(f"{method} {path}: HTTP {status} {msg}".strip())
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, raw.decode("utf-8", "replace")

    async def inspect_container(self, ref: str) -> dict | None:
        status, doc = await self.call("GET", f"/containers/{ref}/json", ok=(200, 404))
        return doc if status == 200 else None

    async def inspect_image(self, ref: str) -> dict:
        _, doc = await self.call("GET", f"/images/{ref}/json")
        return doc

    async def pull(self, name: str, tag: str) -> None:
        _, raw = await self.call("POST", "/images/create", params={"fromImage": name, "tag": tag}, timeout=900)
        for line in str(raw).splitlines():
            try:
                err = json.loads(line).get("error")
            except (ValueError, AttributeError):
                continue
            if err:
                raise DockerError(f"Pull failed: {err}")


def socket_present(path: str = SOCKET) -> bool:
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def split_ref(ref: str) -> tuple[str, str]:
    if "@" in ref:
        raise DockerError("The image is pinned by digest; update it manually")
    name, sep, tag = ref.rpartition(":")
    if not sep or "/" in tag:
        return ref, "latest"
    return name, tag


def own_candidates() -> list[str]:
    ids: list[str] = []
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                m = re.search(r"/containers/([0-9a-f]{64})/", line)
                if m:
                    ids.append(m.group(1))
                    break
    except OSError:
        pass
    ids += [x for x in (os.environ.get("EO_CONTAINER"), socket.gethostname(), "energyoptimizer") if x]
    return ids


async def find_self(api: DockerApi, candidates: list[str] | None = None) -> dict:
    for ref in candidates if candidates is not None else own_candidates():
        doc = await api.inspect_container(ref)
        if doc:
            return doc
    raise DockerError("This container was not found through the Docker socket")


def clone_config(old: dict, image_cfg: dict, ref: str) -> dict:
    """Konfiguration des alten Containers für den neuen, ohne Vorgaben des alten Images.

    Umgebung, Labels, Befehl usw., die nur aus dem alten Image stammen (z. B. EO_BUILD),
    werden weggelassen, damit die des neuen Images gelten.
    """
    cfg: dict[str, Any] = {}
    for key, val in old["Config"].items():
        base = image_cfg.get(key)
        if key == "Env":
            val = [e for e in val or [] if e not in (base or [])]
        elif key == "Labels":
            val = {k: v for k, v in (val or {}).items() if (base or {}).get(k) != v}
        elif val == base:
            continue
        if val in (None, "", [], {}):
            continue
        cfg[key] = val
    cfg["Image"] = ref
    if cfg.get("Hostname") == old["Id"][:12]:
        del cfg["Hostname"]
    cfg["HostConfig"] = old["HostConfig"]
    return cfg


def endpoints(old: dict) -> dict[str, dict]:
    mode = old["HostConfig"].get("NetworkMode", "")
    if mode in ("host", "none") or mode.startswith("container:"):
        return {}
    out: dict[str, dict] = {}
    for name, ep in (old.get("NetworkSettings", {}).get("Networks") or {}).items():
        conf: dict[str, Any] = {"Aliases": [a for a in ep.get("Aliases") or [] if a != old["Id"][:12]]}
        if ep.get("IPAMConfig"):
            conf["IPAMConfig"] = ep["IPAMConfig"]
        out[name] = conf
    return out


async def prepare(api: DockerApi) -> str:
    """Lädt das neue Image und startet den Helfer. Gibt den Namen des neuen Images zurück."""
    me = await find_self(api)
    name, tag = split_ref(me["Config"]["Image"])
    ref = f"{name}:{tag}"
    await api.pull(name, tag)
    new = await api.inspect_image(ref)
    if new["Id"] == me["Image"]:
        raise DockerError("The newest image is already installed")
    src = next((m["Source"] for m in me.get("Mounts", []) if m.get("Destination") == SOCKET), SOCKET)
    old_helper = await api.inspect_container(HELPER_NAME)
    if old_helper:
        await api.call("DELETE", f"/containers/{old_helper['Id']}", params={"force": "true"})
    _, made = await api.call("POST", "/containers/create", params={"name": HELPER_NAME}, body={
        "Image": ref, "Cmd": ["python", "-m", "energyoptimizer", "apply-update", me["Id"]],
        "Env": [f"EO_DOCKER_SOCKET={SOCKET}"], "Healthcheck": {"Test": ["NONE"]},
        "Labels": {"energyoptimizer.updater": "1"},
        "HostConfig": {"Binds": [f"{src}:{SOCKET}"], "NetworkMode": "none"},
    })
    await api.call("POST", f"/containers/{made['Id']}/start", ok=(204, 304))
    return ref


class Installer:
    """Zustand der Installation für die Oberfläche."""

    def __init__(self, updates: UpdateCheck, api: DockerApi | None = None) -> None:
        self.updates = updates
        self.api = api
        self.phase = "idle"   # idle | pulling | starting | error
        self.error = ""
        self.target = ""
        self._task: asyncio.Task | None = None

    @property
    def supported(self) -> bool:
        return self.api is not None or socket_present()

    @property
    def busy(self) -> bool:
        return self.phase in ("pulling", "starting")

    def state(self) -> dict:
        return {"supported": self.supported, "phase": self.phase, "error": self.error, "target": self.target}

    def start(self) -> bool:
        if self.busy or not self.supported or not self.updates.available:
            return False
        self.phase, self.error, self.target = "pulling", "", self.updates.latest
        self._task = asyncio.get_running_loop().create_task(self._run(), name="update-install")
        return True

    async def _run(self) -> None:
        try:
            await prepare(self.api or DockerApi())
            self.phase = "starting"
            _LOGGER.info("[Update] Installing %s, the container is replaced shortly", self.target)
        except DockerError as err:
            self.phase, self.error = "error", str(err)
            _LOGGER.warning("[Update] Installation failed: %s", err)
        except Exception as err:
            self.phase, self.error = "error", str(err) or type(err).__name__
            _LOGGER.exception("[Update] Installation failed")


async def _wait_ready(api: DockerApi, cid: str, log) -> None:
    """Bereit, wenn der Container SETTLE_S durchgehend läuft und nicht als „unhealthy“ gilt."""
    loop = asyncio.get_running_loop()
    end = loop.time() + READY_TIMEOUT_S
    since: float | None = None
    while loop.time() < end:
        st = (await api.inspect_container(cid) or {}).get("State", {})
        if st.get("Running") and not st.get("Restarting") and st.get("Health", {}).get("Status") != "unhealthy":
            since = since if since is not None else loop.time()
            if loop.time() - since >= SETTLE_S:
                return
        else:
            since = None
            if st.get("Status") in ("exited", "dead"):
                raise DockerError("The new container stopped right after starting")
        await asyncio.sleep(2)
    raise DockerError("The new container did not become ready in time")


async def apply_update(target: str, api: DockerApi | None = None, log=print) -> None:
    """Ersetzt den Container ``target`` durch einen aus dem aktuellen Image (läuft im Helfer)."""
    api = api or DockerApi()
    old = await api.inspect_container(target)
    if not old:
        raise DockerError(f"Container {target} not found")
    name = old["Name"].lstrip("/")
    ref = old["Config"]["Image"]
    image_cfg = (await api.inspect_image(old["Image"])).get("Config") or {}
    cfg = clone_config(old, image_cfg, ref)
    nets = endpoints(old)
    mode = old["HostConfig"].get("NetworkMode")
    first = mode if mode in nets else next(iter(nets), "")
    if first:
        cfg["NetworkingConfig"] = {"EndpointsConfig": {first: nets[first]}}
    backup = f"{name}-old"
    stale = await api.inspect_container(backup)
    if stale:
        await api.call("DELETE", f"/containers/{stale['Id']}", params={"force": "true"})
    log(f"Stopping {name}")
    await api.call("POST", f"/containers/{old['Id']}/stop", params={"t": str(STOP_TIMEOUT_S)},
                   ok=(204, 304), timeout=STOP_TIMEOUT_S + 30)
    await api.call("POST", f"/containers/{old['Id']}/rename", params={"name": backup})
    new_id = ""
    try:
        _, made = await api.call("POST", "/containers/create", params={"name": name}, body=cfg)
        new_id = made["Id"]
        for net, conf in nets.items():
            if net != first:
                await api.call("POST", f"/networks/{net}/connect", body={"Container": new_id, "EndpointConfig": conf})
        log(f"Starting {name} from {ref}")
        await api.call("POST", f"/containers/{new_id}/start", ok=(204, 304))
        await _wait_ready(api, new_id, log)
    except Exception:
        log("The new container failed, restoring the old one")
        if new_id:
            await api.call("DELETE", f"/containers/{new_id}", params={"force": "true"}, ok=(204, 404))
        await api.call("POST", f"/containers/{old['Id']}/rename", params={"name": name})
        await api.call("POST", f"/containers/{old['Id']}/start", ok=(204, 304))
        raise
    await api.call("DELETE", f"/containers/{old['Id']}", ok=(204, 404))
    log("Update installed")
    # Der Helfer räumt sich selbst weg; nur nach Fehlern bleibt er für `docker logs` stehen.
    await api.call("DELETE", f"/containers/{socket.gethostname()}", params={"force": "true"}, ok=(204, 404, 409))
