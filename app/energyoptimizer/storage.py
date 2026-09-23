"""Kleine Hilfen zum atomaren Schreiben von JSON-Dateien im Datenverzeichnis."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

_LOGGER = logging.getLogger(__name__)


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as err:
        _LOGGER.warning("%s nicht lesbar (%s) – starte leer", path, err)
        return default


def save_json(path: str, data: Any) -> bool:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except OSError as err:
        _LOGGER.error("%s nicht speicherbar: %s", path, err)
        return False
