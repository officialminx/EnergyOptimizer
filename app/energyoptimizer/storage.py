"""Crash-safe JSON files in the data folder.

A Raspberry Pi loses power without warning. Every file is therefore written to a
temporary file, flushed to the SD card with fsync and only then moved into place.
The previous version is kept as ``<name>.bak``; if the main file is missing or
damaged, it is read instead.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

_LOGGER = logging.getLogger(__name__)


def _read(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_json(path: str, default: Any) -> Any:
    try:
        return _read(path)
    except FileNotFoundError:
        err: Exception | None = None
    except (OSError, ValueError) as e:
        err = e
    try:
        data = _read(path + ".bak")
    except FileNotFoundError:
        if err is not None:
            _LOGGER.warning("%s unreadable (%s) and no backup – starting empty", path, err)
        return default
    except (OSError, ValueError) as e:
        _LOGGER.warning("%s and its backup are unreadable (%s) – starting empty", path, e)
        return default
    _LOGGER.warning("%s %s – using the backup copy", path, "unreadable" if err else "missing")
    return data


def _fsync_dir(path: str) -> None:
    try:
        fd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def save_json(path: str, data: Any, indent: int | None = None) -> bool:
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            if indent is None:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            # Keep the last good version: if power fails between the two renames,
            # load_json finds the backup.
            os.replace(path, path + ".bak")
        os.replace(tmp, path)
        _fsync_dir(path)
        return True
    except OSError as err:
        _LOGGER.error("%s could not be saved: %s", path, err)
        return False
