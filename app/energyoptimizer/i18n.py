"""Translation of server-side messages.

The source texts are German. `tr()` looks a text up in the English table when
the interface language is English and fills in the named placeholders. The
table is shared with the web pages (static/i18n-en.json).
"""

from __future__ import annotations

import json
from pathlib import Path

# Server-only texts; everything else comes from the dictionary the web pages use.
_SERVER: dict[str, str] = {
    # Anmeldung und Einrichtung
    "Mindestens {n} Zeichen": "At least {n} characters",
    "Höchstens {n} Zeichen": "At most {n} characters",
    "Ungültige Adresse": "Invalid address",
    "Der Solar-Log hat das Passwort abgelehnt.": "The Solar-Log rejected the password.",
    "Keine Antwort vom Solar-Log unter {host}. Adresse und Port prüfen.":
        "No answer from the Solar-Log at {host}. Check the address and port.",
    "Verbunden: {prod} W Produktion, {cons} W Verbrauch.":
        "Connected: {prod} W production, {cons} W consumption.",
    # Export der Verbesserungsnotizen
    "# Verbesserungen – EnergyOptimizer": "# Improvements – EnergyOptimizer",
    "Exportiert am {when} ({n} von {max} Notizen belegt).": "Exported on {when} ({n} of {max} notes used).",
    "Offen: {a} · Eingeplant: {b} · Umgesetzt: {c} · Verworfen: {d}":
        "Open: {a} · Planned: {b} · Done: {c} · Rejected: {d}",
    "Bearbeitet werden die Notizen in der Web-Oberfläche unter Einstellungen → Verbesserungen. "
    "Status per API umstellen:":
        "The notes are edited in the web interface under Settings → Improvements. To change a status via the API:",
    "mit": "with",
    "Angelegt": "Created",
    "Geändert": "Changed",
    "_(keine weitere Beschreibung)_": "_(no further description)_",
}



def _load() -> dict[str, str]:
    try:
        shared = json.loads((Path(__file__).parent / "static" / "i18n-en.json").read_text("utf-8"))
    except (OSError, ValueError):
        shared = {}
    return {**shared, **_SERVER}


EN: dict[str, str] = _load()


def tr(text: str, lang: str = "de", **kw) -> str:
    out = EN.get(text, text) if lang == "en" else text
    return out.format(**kw) if kw else out
