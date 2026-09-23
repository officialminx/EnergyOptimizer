"""Ankündigung im Heimnetz per mDNS: http://<name>.local.

Registriert einen _http._tcp-Dienst, dessen Server-Name <name>.local auf die
LAN-Adresse des Hosts zeigt. Das funktioniert nur mit network_mode: host, weil
mDNS Multicast im Heimnetz braucht. Ändert sich der Name in den Einstellungen oder
die IP-Adresse (DHCP), wird die Ankündigung erneuert.
"""

from __future__ import annotations

import logging
import os
import socket

from .devices import local_ipv4

_LOGGER = logging.getLogger(__name__)

try:
    from zeroconf import NonUniqueNameException, ServiceInfo
    from zeroconf.asyncio import AsyncZeroconf
except ImportError:  # pragma: no cover - zeroconf ist in requirements.txt
    AsyncZeroconf = None  # type: ignore[assignment,misc]


def mdns_enabled() -> bool:
    return os.environ.get("EO_MDNS", "1").strip().lower() not in ("0", "false", "no", "off")


class Mdns:
    def __init__(self, port: int) -> None:
        self.port = port
        self.enabled = mdns_enabled() and AsyncZeroconf is not None
        self._zc = None
        self._info = None
        self._key: tuple[str, str] | None = None
        self.error = ""

    @staticmethod
    def host_ip() -> str:
        return os.environ.get("EO_HOST_IP") or local_ipv4()

    async def update(self, hostname: str) -> None:
        """Idempotent: kündigt <hostname>.local an, sofern sich Name oder IP geändert haben."""
        if not self.enabled:
            return
        ip = self.host_ip()
        if ip.startswith("127."):
            return
        key = (hostname, ip)
        if key == self._key:
            return
        await self._unregister()
        try:
            if self._zc is None:
                self._zc = AsyncZeroconf()
            info = ServiceInfo(
                "_http._tcp.local.",
                f"EnergyOptimizer ({hostname})._http._tcp.local.",
                addresses=[socket.inet_aton(ip)],
                port=self.port,
                properties={"path": "/"},
                server=f"{hostname}.local.",
            )
            await self._zc.async_register_service(info, allow_name_change=True)
        except NonUniqueNameException:
            self.error = f"{hostname}.local ist im Netz schon vergeben"
            _LOGGER.warning("[mDNS] %s", self.error)
            self._key = key
            return
        except Exception as err:  # noqa: BLE001 - mDNS darf den Start nie verhindern
            self.error = str(err) or type(err).__name__
            _LOGGER.warning("[mDNS] Ankündigung fehlgeschlagen: %s", self.error)
            return
        self._info = info
        self._key = key
        self.error = ""
        suffix = "" if self.port == 80 else f":{self.port}"
        _LOGGER.info("[mDNS] Erreichbar als http://%s.local%s (%s)", hostname, suffix, ip)

    async def _unregister(self) -> None:
        if self._zc is not None and self._info is not None:
            try:
                await self._zc.async_unregister_service(self._info)
            except Exception:  # noqa: BLE001
                pass
        self._info = None

    async def close(self) -> None:
        await self._unregister()
        if self._zc is not None:
            try:
                await self._zc.async_close()
            except Exception:  # noqa: BLE001
                pass
            self._zc = None
        self._key = None
