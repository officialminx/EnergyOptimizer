"""Translation of server-side messages.

The source texts are German. `tr()` looks a text up in the English table when
the interface language is English and fills in the named placeholders.
"""

from __future__ import annotations

EN: dict[str, str] = {
    # Anmeldung und Einrichtung
    "Mindestens {n} Zeichen": "At least {n} characters",
    "Höchstens {n} Zeichen": "At most {n} characters",
    "Ungültige Adresse": "Invalid address",
    "Der Solar-Log hat das Passwort abgelehnt.": "The Solar-Log rejected the password.",
    "Keine Antwort vom Solar-Log unter {host}. Adresse und Port prüfen.":
        "No answer from the Solar-Log at {host}. Check the address and port.",
    "Verbunden: {prod} W Produktion, {cons} W Verbrauch.":
        "Connected: {prod} W production, {cons} W consumption.",
}


def tr(text: str, lang: str = "de", **kw) -> str:
    out = EN.get(text, text) if lang == "en" else text
    return out.format(**kw) if kw else out
