"""Verbesserungs-Notizen: höchstens 30 Einträge, als Markdown exportierbar."""

from __future__ import annotations

import os

from .clock import CLOCK
from .storage import load_json, save_json

IDEA_MAX = 30
IDEA_TITLE = 89
IDEA_TEXT = 699
STATUS_TXT = ["offen", "eingeplant", "umgesetzt", "verworfen"]
PRIO_TXT = ["niedrig", "normal", "hoch"]
AREA_TXT = ["Allgemein", "Dashboard", "Verlauf & Statistik", "Geräte & Steuerung",
            "Meldungen & Alarme", "MQTT / Home Assistant", "System & Wartung",
            "Bedienung / Web-Oberfläche"]


def _cut(s: str, max_bytes: int) -> str:
    return s.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


class Ideas:
    def __init__(self, data_dir: str) -> None:
        self.path = os.path.join(data_dir, "ideas.json")
        self.next_id = 1
        self.items: list[dict] = []

    def load(self) -> None:
        data = load_json(self.path, {})
        if isinstance(data, dict):
            self.next_id = int(data.get("next_id", 1))
            self.items = [i for i in data.get("ideas", []) if isinstance(i, dict)]

    def _save(self) -> bool:
        return save_json(self.path, {"next_id": self.next_id, "ideas": self.items})

    def upsert(self, doc: dict) -> tuple[bool, int, str, bool]:
        """Rückgabe: (ok, id, Fehler, nicht_gefunden)."""
        def geti(k: str) -> int:
            v = doc.get(k)
            return v if isinstance(v, int) and not isinstance(v, bool) else -1

        status, prio, area = geti("status"), geti("prio"), geti("area")
        if status >= len(STATUS_TXT):
            return False, 0, "Ungültiger Status", False
        if prio >= len(PRIO_TXT):
            return False, 0, "Ungültige Priorität", False
        if area >= len(AREA_TXT):
            return False, 0, "Ungültiger Bereich", False
        iid = doc.get("id") if isinstance(doc.get("id"), int) else 0
        now = int(CLOCK.time())
        if iid:
            rec = next((i for i in self.items if i["id"] == iid), None)
            if rec is None:
                return False, 0, "Notiz nicht gefunden", True
            rec = dict(rec)
        else:
            if len(self.items) >= IDEA_MAX:
                return False, 0, (f"Kein Platz mehr – es sind bereits {IDEA_MAX} Notizen "
                                  "gespeichert. Erledigte löschen."), False
            rec = {"id": self.next_id, "created": now, "status": 0, "prio": 1, "area": 0,
                   "title": "", "text": ""}
        if isinstance(doc.get("title"), str):
            rec["title"] = _cut(doc["title"], IDEA_TITLE)
        if isinstance(doc.get("text"), str):
            rec["text"] = _cut(doc["text"], IDEA_TEXT)
        for k, v in (("status", status), ("prio", prio), ("area", area)):
            if v >= 0:
                rec[k] = v
        rec["updated"] = now
        if not rec["title"].strip():
            return False, 0, "Bitte einen Titel angeben", False
        if iid:
            self.items = [rec if i["id"] == iid else i for i in self.items]
        else:
            self.items.append(rec)
            self.next_id += 1
        if not self._save():
            return False, 0, "Schreiben fehlgeschlagen", False
        return True, rec["id"], "", False

    def delete(self, iid: int) -> bool:
        before = len(self.items)
        self.items = [i for i in self.items if i["id"] != iid]
        if len(self.items) == before:
            return False
        self._save()
        return True

    def build(self) -> dict:
        out = []
        for r in sorted(self.items, key=lambda i: i["id"]):
            out.append({
                "id": r["id"], "status": r["status"], "prio": r["prio"], "area": r["area"],
                "created": r.get("created", 0), "updated": r.get("updated", 0),
                "status_txt": STATUS_TXT[r["status"]], "prio_txt": PRIO_TXT[r["prio"]],
                "area_txt": AREA_TXT[r["area"]], "title": r["title"], "text": r["text"],
            })
        return {"max": IDEA_MAX, "n": len(out), "ideas": out}

    def markdown(self) -> str:
        def when(ep: int) -> str:
            return CLOCK.local(ep).strftime("%Y-%m-%d %H:%M") if ep else "unbekannt"

        items = sorted(self.items, key=lambda i: i["id"])
        per = [sum(1 for i in items if i["status"] == s) for s in range(4)]
        out = [
            "# Verbesserungen – EnergyOptimizer\n\n",
            f"Exportiert am {when(int(CLOCK.time()))} ({len(items)} von {IDEA_MAX} Notizen belegt).\n",
            f"Offen: {per[0]} · Eingeplant: {per[1]} · Umgesetzt: {per[2]} · Verworfen: {per[3]}\n\n",
            "Bearbeitet werden die Notizen in der Web-Oberfläche unter\n"
            "Einstellungen → Verbesserungen. Status per API umstellen:\n"
            "`POST /api/ideas` mit `{\"id\":<nr>,\"status\":2}` (0=offen, 1=eingeplant,\n"
            "2=umgesetzt, 3=verworfen).\n\n---\n\n",
        ]
        for r in items:
            out.append(
                f"## [#{r['id']}] {r['title']}\n\n"
                f"- Status: {STATUS_TXT[r['status']]}\n- Priorität: {PRIO_TXT[r['prio']]}\n"
                f"- Bereich: {AREA_TXT[r['area']]}\n"
                f"- Angelegt: {when(r.get('created', 0))} · Geändert: {when(r.get('updated', 0))}\n\n"
                f"{r['text'] or '_(keine weitere Beschreibung)_'}\n\n---\n\n")
        return "".join(out)
