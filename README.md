# EnergyOptimizer

Der EnergyOptimizer schaltet Shelly-Steckdosen (und externe Schalter über MQTT) nach dem
PV-Überschuss des Solar-Log. Dieses Repo enthält die App als Docker-Container, damit sie
ohne ESP32 direkt auf einem Raspberry Pi läuft. Weboberfläche, Einstellungen und
Automatik entsprechen der ESP32-P4-Firmware.

## Solar-Log-Abfrage

Die Abfrage des Solar-Log stammt aus der Home-Assistant-Integration
(`ha-advanced-solarlog`), weil die JSON-Abfrage mit Passwort nur so funktioniert:

- `POST /getjp` mit `Content-Type: text/html` und `X-SL-CSRF-PROTECTION: 1`
- Login über `POST /login` (`u=…&p=…`); probiert werden die Konten `user`, `installer`,
  `installateur` und `pm`
- meldet der Solar-Log „Password was wrong“, wird das Passwort als bcrypt-Hash mit dem
  Salt des Geräts gesendet (neuere Firmware)
- das Session-Cookie `SolarLog` wird bei jeder Abfrage mitgeschickt; bei
  `ACCESS DENIED` meldet sich die App neu an

Unter **Einstellungen → SolarLog → Diagnose (Rohantwort)** zeigt die Weboberfläche die
Antwort des Solar-Log und welches Konto sich angemeldet hat.

## Installation auf dem Raspberry Pi

Voraussetzung ist ein **64-Bit Raspberry Pi OS** (Pi 3, 4 oder 5) mit Docker:

```sh
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # danach neu anmelden
```

Dann in einem leeren Ordner die `docker-compose.yml` aus diesem Repo ablegen und starten:

```sh
mkdir -p ~/energyoptimizer && cd ~/energyoptimizer
curl -fsSLO https://raw.githubusercontent.com/officialminx/energyoptimizer/main/docker-compose.yml
docker compose up -d
```

Die Weboberfläche läuft auf `http://<IP-des-Pi>:8080`. Solange kein Web-Passwort
gesetzt ist, ist die Oberfläche ohne Anmeldung offen. Unter **Einstellungen** die IP des
Solar-Log, dessen Passwort und ein eigenes Web-Passwort eintragen.

Das Image `ghcr.io/officialminx/energyoptimizer` baut GitHub Actions für `arm64` und
`amd64`. Ist das Paket auf GitHub privat, vorher `docker login ghcr.io` ausführen oder
das Paket unter *Package settings* auf öffentlich stellen. Alternativ baut der Pi das
Image selbst: Repo klonen und `docker compose up -d --build`.

### Update

```sh
docker compose pull && docker compose up -d
```

Das Firmware-Update (OTA) der Weboberfläche gibt es im Container nicht; „Neu starten“
startet den Container neu.

## Daten

Alles liegt im Ordner `./data` neben der `docker-compose.yml`: Einstellungen,
Energiezähler, Verlauf, Tagesarchiv, Ereignisse und Ideen. Ein Backup ist eine Kopie
dieses Ordners.

Von einem ESP32 umziehen: dort unter **Einstellungen → System & Wartung → Konfiguration
sichern** die Datei exportieren und im Container an derselben Stelle importieren.
Passwörter und das ntfy-Topic werden dabei nicht übernommen, die trägt man einmal neu ein.

## Umgebungsvariablen

| Variable | Standard | Bedeutung |
|---|---|---|
| `TZ` | `Europe/Zurich` | Zeitzone für Zeitfenster, Zeitschaltuhr und Tageswechsel |
| `EO_PORT` | `8080` | Port der Weboberfläche |
| `EO_BIND` | `0.0.0.0` | Adresse, auf der der Webserver lauscht |
| `EO_DATA_DIR` | `/data` | Datenordner im Container |
| `EO_LOG_LEVEL` | `INFO` | `DEBUG` zeigt jede Solar-Log-Abfrage |
| `EO_SCAN_SUBNET` | Subnetz des Pi | Subnetz für die Shelly-Suche, z. B. `192.168.1.0/24` |
| `EO_HOST_IP` | automatisch | IP, die die Oberfläche als eigene Adresse anzeigt |
| `EO_SOLARLOG_HOST` | – | Solar-Log-IP, nur beim allerersten Start übernommen |
| `EO_SOLARLOG_PORT` | – | Solar-Log-Port, nur beim allerersten Start übernommen |
| `EO_SOLARLOG_PASSWORD` | – | Solar-Log-Passwort, nur beim allerersten Start übernommen |
| `EO_WEB_PASSWORD` | – | Web-Passwort, nur beim allerersten Start übernommen |

## Netzwerk

Die `docker-compose.yml` nutzt `network_mode: host`. So findet die Shelly-Suche Geräte im
Heimnetz, und MQTT sowie ntfy funktionieren ohne Portfreigaben. Port 80 ist absichtlich
nicht der Standard, weil dafür Root-Rechte oder eine freie Portbelegung auf dem Pi nötig
sind. Wer ihn trotzdem will, setzt `EO_PORT: "80"`.

## Entwicklung

```sh
cd app
pip install -r requirements-dev.txt
python -m pytest
EO_DATA_DIR=/tmp/eo python -m energyoptimizer
```

Die Tests laufen gegen einen nachgebauten Solar-Log, der wie ein echter Solar-Log nur
mit Cookie-Login, CSRF-Header und bcrypt-Passwort antwortet.
