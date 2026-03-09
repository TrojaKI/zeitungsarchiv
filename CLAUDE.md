# CLAUDE.md — Zeitungsarchiv Projektbibel

> Dieses Dokument ist die zentrale Referenz für Claude Code beim Aufbau des
> Zeitungsarchiv-Systems. Alle Agenten, Skills und Implementierungsschritte
> beziehen sich auf dieses Dokument.

---

## Projektübersicht

**Ziel:** Ein lokales Archiv-System für eingescannte Zeitungsartikel.
Scans werden automatisch per OCR in Text umgewandelt, mit KI-Metadaten
angereichert und in einer durchsuchbaren Datenbank gespeichert.
Zugriff via WebApp (Docker) und CLI.

**Plattform:** Linux + macOS  
**Sprache der Artikel:** Deutsch  
**Scanner:** CanoScan 8800F + VueScan  
**Artikelanzahl:** ~hunderte (skalierbar)

---

## Verzeichnisstruktur des Projekts

```
zeitungsarchiv/
├── CLAUDE.md                   ← diese Datei
├── AGENTS.md                   ← Agent-Team-Definitionen
├── docker-compose.yml
├── Dockerfile
├── .env.example
│
├── skills/                     ← Wiederverwendbare Skills für Claude Code
│   ├── vuescan/
│   │   └── SKILL.md            ← VueScan INI generieren & optimieren
│   ├── ocr/
│   │   └── SKILL.md            ← Tesseract OCR Pipeline
│   ├── metadata/
│   │   └── SKILL.md            ← Claude API Metadaten-Extraktion
│   └── webapp/
│       └── SKILL.md            ← FastAPI + HTMX WebApp
│
├── config/
│   ├── vuescan.ini             ← generiert von vuescan-skill
│   └── settings.toml           ← App-Konfiguration
│
├── app/                        ← Python-Applikation
│   ├── worker/                 ← Ingestion-Pipeline
│   │   ├── watcher.py          ← Inbox-Verzeichnis beobachten
│   │   ├── preprocess.py       ← Bildoptimierung
│   │   ├── ocr.py              ← Tesseract-Wrapper
│   │   └── metadata.py         ← Claude API Metadaten
│   ├── db/
│   │   ├── schema.sql          ← SQLite Schema + FTS5
│   │   └── database.py         ← DB-Zugriff
│   ├── web/                    ← FastAPI WebApp
│   │   ├── main.py
│   │   ├── routes/
│   │   └── templates/          ← HTMX Templates
│   └── cli/
│       └── main.py             ← Click CLI
│
├── inbox/                      ← Scan-Eingangsverzeichnis (gemountet)
├── archive/                    ← verarbeitete Artikel + Bilder
└── db/                         ← SQLite Datenbankdatei (persistent)
```

---

## Tech-Stack

| Komponente       | Technologie              | Begründung                          |
|------------------|--------------------------|-------------------------------------|
| Sprache          | Python 3.12              | Beste OCR/KI-Bibliotheken           |
| OCR              | Tesseract 5 + pytesseract| Kostenlos, Deutsch (deu), lokal     |
| Bildverarbeitung | Pillow + OpenCV          | Deskew, Kontrast, Rauschen          |
| KI-Metadaten     | Claude API (Sonnet)      | Datum/Quelle/Schlagzeile erkennen   |
| Datenbank        | SQLite + FTS5            | Kein Server, portabel, Volltextsuche|
| Web-Backend      | FastAPI                  | Leichtgewichtig, async              |
| Web-Frontend     | HTMX + Jinja2            | Kein Node.js, minimal JS            |
| CLI              | Click                    | Python-nativ                        |
| Container        | Docker + Compose         | Plattformunabhängig                 |
| Inbox-Watch      | watchdog                 | Cross-platform Dateisystem-Events   |

---

## Datenbankschema

```sql
-- Haupttabelle: Artikel
CREATE TABLE articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,          -- Originaldateiname
    scan_date       TEXT NOT NULL,          -- ISO 8601: YYYY-MM-DD
    
    -- Metadaten (automatisch oder manuell)
    newspaper       TEXT,                   -- z.B. "Süddeutsche Zeitung"
    article_date    TEXT,                   -- Erscheinungsdatum des Artikels
    page            TEXT,                   -- Seite (optional)
    headline        TEXT,                   -- Schlagzeile
    summary         TEXT,                   -- KI-Zusammenfassung (2-3 Sätze)
    category        TEXT,                   -- Politik, Kultur, Sport, Wirtschaft...
    tags            TEXT,                   -- JSON-Array: '["tag1","tag2"]'
    
    -- Inhalt
    full_text       TEXT,                   -- OCR-Volltext
    image_path      TEXT,                   -- Pfad zum WebP-Archivbild
    thumb_path      TEXT,                   -- Pfad zum Thumbnail
    
    -- Qualität & Status
    ocr_confidence  REAL,                   -- Tesseract Konfidenz 0.0–100.0
    needs_review    INTEGER DEFAULT 0,      -- 1 = manuelle Prüfung nötig
    meta_source     TEXT DEFAULT 'auto',    -- 'auto' | 'manual' | 'partial'
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- Volltextindex (SQLite FTS5) für schnelle Suche
CREATE VIRTUAL TABLE articles_fts USING fts5(
    headline,
    summary,
    full_text,
    tags,
    content='articles',
    content_rowid='id',
    tokenize='unicode61'
);

-- Trigger: FTS synchron halten
CREATE TRIGGER articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, headline, summary, full_text, tags)
    VALUES (new.id, new.headline, new.summary, new.full_text, new.tags);
END;

CREATE TRIGGER articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, headline, summary, full_text, tags)
    VALUES ('delete', old.id, old.headline, old.summary, old.full_text, old.tags);
    INSERT INTO articles_fts(rowid, headline, summary, full_text, tags)
    VALUES (new.id, new.headline, new.summary, new.full_text, new.tags);
END;
```

---

## Ingestion-Workflow (Schritt für Schritt)

```
1. SCAN (manuell, außerhalb der App)
   └─ VueScan + CanoScan 8800F
   └─ Format: TIFF, 400 DPI, Graustufen
   └─ Ziel: ~/zeitungsarchiv/inbox/
   └─ Dateiname: automatisch (Datum+Zeit) oder manuell

2. ERKENNUNG (Worker — automatisch via watchdog)
   └─ Neue Datei in inbox/ erkannt
   └─ Bildvorverarbeitung:
       ├─ Deskew (Schräglagen korrigieren)
       ├─ Kontrast + Helligkeit normalisieren
       ├─ Rauschen reduzieren
       └─ TIFF → WebP (Archiv) + JPEG-Thumbnail
   └─ OCR via Tesseract (Sprache: deu)
       ├─ Konfidenzwert messen
       └─ needs_review = true wenn Konfidenz < 70%
   └─ Metadaten via Claude API:
       ├─ Zeitung, Erscheinungsdatum, Schlagzeile
       ├─ Kategorie, Tags (max. 5)
       ├─ Kurzzusammenfassung (2-3 Sätze)
       └─ meta_source = 'partial' wenn Felder fehlen
   └─ Eintrag in SQLite + FTS5-Index
   └─ Datei verschoben: inbox/ → archive/

3. REVIEW (WebApp — optional)
   └─ Artikel mit needs_review=1 erscheinen markiert
   └─ Seite: Originalbild | OCR-Text | Metadaten-Formular
   └─ Speichern → meta_source='manual', needs_review=0

4. SUCHE (WebApp + CLI)
   WebApp:
   └─ Freitextsuche (FTS5)
   └─ Filter: Zeitung, Zeitraum, Kategorie, Tags
   └─ Ergebnis: Karte mit Thumbnail + Schlagzeile + Snippet
   └─ Klick → Detailansicht mit Originalbild

   CLI:
   └─ zeitungsarchiv search "Stichwort"
   └─ zeitungsarchiv show <id>
   └─ zeitungsarchiv process [--dir ./inbox]
   └─ zeitungsarchiv export --format csv|json
   └─ zeitungsarchiv stats
```

---

## Docker-Setup

```yaml
# docker-compose.yml
services:
  archiv:
    build: .
    restart: unless-stopped
    ports:
      - "8000:8000"          # WebApp
    volumes:
      - ./inbox:/app/inbox         # Scan-Eingang (Host ↔ Container)
      - ./archive:/app/archive     # Archiv (persistent)
      - ./db:/app/db               # SQLite DB (persistent)
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - OCR_LANG=deu
      - OCR_CONFIDENCE_THRESHOLD=70
      - WATCH_INBOX=true
      - LOG_LEVEL=INFO
```

```dockerfile
# Dockerfile (Konzept)
FROM python:3.12-slim
RUN apt-get install -y tesseract-ocr tesseract-ocr-deu \
    imagemagick libopencv-dev
COPY app/ /app/
RUN pip install -r requirements.txt
CMD ["python", "-m", "app.main"]   # startet Worker + WebServer
```

---

## Konfiguration

```toml
# config/settings.toml
[scan]
inbox_dir = "/app/inbox"
archive_dir = "/app/archive"
watch_interval_seconds = 5

[ocr]
language = "deu"
confidence_threshold = 70       # unter diesem Wert → needs_review
dpi_expected = 400

[claude]
model = "claude-sonnet-4-20250514"
max_tokens = 1000
# Prompt-Template in skills/metadata/SKILL.md definiert

[web]
host = "0.0.0.0"
port = 8000
items_per_page = 20

[export]
formats = ["csv", "json", "pdf"]
```

---

## Implementierungsphasen

### Phase 1 — Fundament
- [ ] VueScan INI generieren (vuescan-skill)
- [ ] Projektstruktur anlegen
- [ ] SQLite Schema + FTS5
- [ ] Bildvorverarbeitung (Pillow + OpenCV)
- [ ] Tesseract OCR Pipeline

### Phase 2 — Intelligenz
- [ ] Claude API Metadaten-Extraktion
- [ ] Inbox-Watcher (watchdog)
- [ ] Vollständige Ingestion-Pipeline

### Phase 3 — Oberflächen
- [ ] CLI (Click)
- [ ] FastAPI Backend
- [ ] HTMX Frontend (Suche, Detailansicht, Review)

### Phase 4 — Docker & Polish
- [ ] Dockerfile + docker-compose.yml
- [ ] Export (CSV, JSON)
- [ ] Logging + Fehlerbehandlung
- [ ] README mit Setup-Anleitung

---

## Wichtige Designentscheidungen

1. **TIFF als Scan-Master** — verlustfreie Qualität für OCR, erst danach
   WebP-Konvertierung für das Archiv.

2. **Tesseract first, Claude second** — OCR lokal und kostenlos,
   Claude API nur für semantische Metadaten (sparsam verwenden).

3. **needs_review-Flag** — statt blindem Vertrauen in KI-Extraktion
   werden unsichere Ergebnisse zur manuellen Prüfung markiert.

4. **SQLite statt Postgres** — bei hunderten Artikeln völlig ausreichend,
   kein separater DB-Server nötig, einfaches Backup (eine Datei).

5. **HTMX statt React** — kein Build-Toolchain, kein Node.js,
   funktioniert direkt im Docker-Container.

6. **Volumes statt COPY** — inbox/, archive/, db/ sind Host-Volumes,
   damit Daten Container-Rebuilds überleben.

---

## Referenzdokumente

- `AGENTS.md` — Agent-Team-Rollen und Koordination
- `skills/vuescan/SKILL.md` — VueScan INI Skill
- `skills/ocr/SKILL.md` — OCR Pipeline Skill
- `skills/metadata/SKILL.md` — Metadaten-Extraktion Skill
- `skills/webapp/SKILL.md` — WebApp Skill
- `examples/README.md` - Beispielscans

