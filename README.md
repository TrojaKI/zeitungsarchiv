# Zeitungsarchiv

Lokales Archiv-System für eingescannte Zeitungsartikel.
Scans werden per OCR in Text umgewandelt, mit KI-Metadaten angereichert und in einer durchsuchbaren Datenbank gespeichert. Zugriff via WebApp und CLI.

---

## Inhalt

- [Features](#features)
- [Systemvoraussetzungen](#systemvoraussetzungen)
- [Installation](#installation)
  - [Linux (Debian/Ubuntu)](#linux-debianubuntu)
  - [macOS](#macos)
- [Schnellstart](#schnellstart)
  - [Mit Docker](#mit-docker)
  - [Ohne Docker (lokal)](#ohne-docker-lokal)
- [CLI-Befehle](#cli-befehle)
- [Mehrseitige Scans zusammenfügen](#mehrseitige-scans-zusammenfügen)
- [Konfiguration](#konfiguration)
- [Tests](#tests)

---

## Features

- **Automatische OCR** via Tesseract (Deutsch)
- **KI-Metadatenextraktion** (Zeitung, Datum, Schlagzeile, Kategorie, Tags) via lokalem Ollama-Modell — kein Cloud-API-Key erforderlich
- **Mehrseitige Scans** werden mit Hugin automatisch zu einem Panorama zusammengeführt (`_01` + `_02` → `_00`)
- **Volltextsuche** mit SQLite FTS5
- **WebApp** (FastAPI + HTMX) mit Suche, Detailansicht und manuellem Review
- **CLI** für Batch-Import, Suche, Export und Statistiken
- **Docker-Support** für plattformunabhängigen Betrieb

---

## Systemvoraussetzungen

| Komponente | Version | Zweck |
|---|---|---|
| Python | ≥ 3.12 | Laufzeitumgebung |
| Tesseract | ≥ 5 + Sprachpaket `deu` | OCR |
| OpenCV | ≥ 4.9 | Bildvorverarbeitung |
| Ollama | aktuell | Lokale KI-Metadaten |
| Hugin-Tools | aktuell | Mehrseitige Scans (optional) |
| Docker + Compose | aktuell | Containerisierter Betrieb (optional) |

---

## Installation

### Linux (Debian/Ubuntu)

#### 1. Systemabhängigkeiten

```bash
sudo apt-get update
sudo apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-deu \
    libopencv-dev \
    python3-opencv \
    python3-venv \
    python3-pip
```

Für mehrseitige Scans (optional):

```bash
sudo apt-get install -y hugin-tools
```

#### 2. Ollama installieren

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5vl:3b
```

#### 3. Projekt einrichten

```bash
git clone https://github.com/TrojaKI/zeitungsarchiv.git
cd zeitungsarchiv

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -e .
```

#### 4. Konfiguration

```bash
cp .env.example .env
# .env nach Bedarf anpassen (Ollama-URL, Modell, Log-Level)
```

---

### macOS

#### 1. Homebrew installieren (falls nicht vorhanden)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

#### 2. Systemabhängigkeiten

```bash
brew install tesseract tesseract-lang opencv python@3.13
```

Für mehrseitige Scans (optional):

```bash
brew install hugin
```

#### 3. Ollama installieren

```bash
brew install ollama
ollama serve &          # Dienst im Hintergrund starten
ollama pull qwen2.5vl:3b
```

Alternativ: [ollama.com/download](https://ollama.com/download) → macOS-App herunterladen.

#### 4. Projekt einrichten

```bash
git clone https://github.com/TrojaKI/zeitungsarchiv.git
cd zeitungsarchiv

python3.13 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -e .
```

#### 5. Konfiguration

```bash
cp .env.example .env
# Ollama läuft auf macOS standardmäßig auf http://localhost:11434 — keine Änderung nötig
```

---

## Schnellstart

### Mit Docker

```bash
# Verzeichnisse anlegen
mkdir -p inbox archive db

# Starten
docker compose up -d

# Logs verfolgen
docker compose logs -f
```

Die WebApp ist dann unter **http://localhost:8000** erreichbar.

Scans einfach in `inbox/` ablegen — der Watcher verarbeitet sie automatisch.

### Ohne Docker (lokal)

```bash
source .venv/bin/activate

# Umgebungsvariablen setzen
export ARCHIVE_DIR=$(pwd)/archive
export DB_PATH=$(pwd)/db/archive.db
export INBOX_DIR=$(pwd)/inbox

mkdir -p inbox archive db

# WebApp starten
uvicorn app.web.main:app --host 0.0.0.0 --port 8000 &

# In einem zweiten Terminal: Scans verarbeiten
zeitungsarchiv process
```

---

## CLI-Befehle

```bash
# Alle TIFF-Dateien im Inbox-Verzeichnis verarbeiten (OCR + Metadaten + DB)
zeitungsarchiv process
zeitungsarchiv process --dir /pfad/zum/ordner

# Volltextsuche
zeitungsarchiv search "Stichwort"
zeitungsarchiv search "Stichwort" --newspaper "Kurier" --category "Politik"

# Artikel anzeigen
zeitungsarchiv show 42

# Statistiken
zeitungsarchiv stats

# Export
zeitungsarchiv export --format csv --output archiv.csv
zeitungsarchiv export --format json --output archiv.json

# WebApp starten (ohne Docker)
zeitungsarchiv serve
zeitungsarchiv serve --host 127.0.0.1 --port 9000
```

---

## Mehrseitige Scans zusammenfügen

Artikel, die über mehrere Seiten gehen, folgen der `_NN`-Konvention:

| Dateiname | Bedeutung |
|---|---|
| `artikel_01.tif` | Erste Seite (Rohdatei) |
| `artikel_02.tif` | Zweite Seite (Rohdatei) |
| `artikel_00.tif` | Fertig zusammengeführtes Panorama |

**Ablauf:**

1. Seiten einscannen → `inbox/artikel_01.tif`, `inbox/artikel_02.tif`
2. `zeitungsarchiv process` ausführen
3. Hugin stitcht die Seiten automatisch zu `artikel_00.tif`
4. Nur `_00` wird importiert — Rohseiten landen in `archive/artikel_00/parts/`

Ist `_00` bereits vorhanden, wird das Stitching übersprungen.

Der Inbox-Watcher ignoriert `_01`/`_02`-Dateien und wartet auf `_00`
(bzw. bis `process` ausgeführt wird).

> **Voraussetzung:** `hugin-tools` muss installiert sein (siehe oben).

---

## Konfiguration

Alle Einstellungen können über Umgebungsvariablen gesetzt werden:

| Variable | Standard | Beschreibung |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama-Server-URL |
| `OLLAMA_MODEL` | `qwen2.5vl:3b` | Ollama-Modell für Metadaten |
| `INBOX_DIR` | `./inbox` | Eingangsverzeichnis für Scans |
| `ARCHIVE_DIR` | `./archive` | Archiv (verarbeitete Artikel + Bilder) |
| `DB_PATH` | `./db/archive.db` | SQLite-Datenbankdatei |
| `OCR_LANG` | `deu` | Tesseract-Sprachcode |
| `OCR_CONFIDENCE_THRESHOLD` | `70` | Unter diesem Wert → `needs_review = true` |
| `WATCH_INBOX` | `true` | Inbox automatisch überwachen |
| `LOG_LEVEL` | `INFO` | Log-Level (`DEBUG`, `INFO`, `WARNING`, …) |

In Docker: Werte in `docker-compose.yml` oder `.env` eintragen.
Lokal: `export VARIABLE=wert` oder `.env`-Datei anlegen.

---

## Tests

```bash
source .venv/bin/activate
pip install pytest   # falls noch nicht installiert

# Unit-Tests (schnell, keine externen Abhängigkeiten)
python -m pytest tests/test_stitch_pipeline.py -v

# Integrations-Tests (benötigt hugin-tools + Beispieldateien in examples/)
python -m pytest tests/test_stitch_integration.py -v

# Alle Tests
python -m pytest tests/ -v
```

Die Integrations-Tests werden automatisch übersprungen, wenn `hugin-tools`
nicht installiert oder die Beispieldateien nicht vorhanden sind.
