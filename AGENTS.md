# AGENTS.md — Agent-Team für das Zeitungsarchiv

> Dieses Dokument definiert das Agent-Team, das Claude Code beim Aufbau
> und Betrieb des Zeitungsarchivs orchestriert. Jeder Agent hat eine
> klar abgegrenzte Verantwortung und kommuniziert über definierte
> Schnittstellen mit den anderen.

---

## Übersicht: Das Agent-Team

```
┌─────────────────────────────────────────────────────────┐
│                  ORCHESTRATOR                           │
│   Koordiniert alle Agenten, verwaltet den Gesamtplan   │
└──────┬──────────┬──────────┬──────────┬────────────────┘
       │          │          │          │
       ▼          ▼          ▼          ▼
  [Scanner]  [Ingestion] [Metadata]  [WebApp]
   Agent      Agent       Agent       Agent
```

---

## Agent 1: Orchestrator

**Rolle:** Projektleitung und Koordination  
**Wann aktiv:** Bei neuen Features, Phasenwechsel, Fehlern in der Pipeline

### Verantwortlichkeiten
- Liest `CLAUDE.md` zu Beginn jeder Session
- Delegiert Aufgaben an Spezialagenten
- Prüft ob Skills vorhanden sind, bevor Code geschrieben wird
- Verwaltet Implementierungsphasen (Phase 1–4 aus CLAUDE.md)
- Meldet Konflikte zwischen Agenten und löst sie auf

### Entscheidungsregeln
```
WENN neue Aufgabe:
  1. Prüfe CLAUDE.md → aktuelle Phase?
  2. Existiert passender Skill in skills/?
     JA  → Skill lesen, dann delegieren
     NEIN → Skill zuerst erstellen (skill-creator Pattern)
  3. Welcher Agent ist zuständig?
  4. Ergebnis in CLAUDE.md Phase-Checkliste abhaken
```

### Kommunikation
- **Input:** Nutzeranfragen, Fehlerberichte anderer Agenten
- **Output:** Aufgaben-Delegation mit klarem Kontext + Erwartungen
- **Protokoll:** Zusammenfassung nach jeder abgeschlossenen Phase

---

## Agent 2: Scanner-Agent

**Rolle:** VueScan-Konfiguration und Scan-Qualität  
**Skill:** `skills/vuescan/SKILL.md`

### Verantwortlichkeiten
- Generiert und optimiert `config/vuescan.ini`
- Definiert optimale Scan-Parameter für Zeitungsartikel
- Dokumentiert manuellen Scan-Workflow für den Nutzer
- Testet Scan-Qualität anhand von Beispiel-TIFFs

### VueScan-Parameter (Zielwerte)
```ini
; Optimiert für CanoScan 8800F + Zeitungsartikel
[Scanner]
scanner-vendor=Canon
scanner-model=CanoScan 8800F

[Input]
input-resolution=400          ; DPI — optimal für OCR
input-color=gray              ; Graustufen reicht für Text
input-bit-depth=8             ; 8-bit Graustufen

[Output]
output-tiff=yes               ; Master: verlustfrei
output-tiff-filename=~/zeitungsarchiv/inbox/{date}-{time}.tif
output-jpeg=no                ; JPG erst in der App
output-auto-rotate=yes        ; Ausrichtung automatisch

[Filter]
filter-descreen=yes           ; Druckraster entfernen
filter-sharpen=medium         ; Schrift schärfen
filter-grain-reduction=light  ; Rauschen reduzieren
```

### Aufgaben-Trigger
- "vuescan.ini erstellen"
- "Scan-Qualität verbessern"
- "Scanner konfigurieren"
- Neue Scanner-Hardware

### Output
- `config/vuescan.ini` (bereit zum Kopieren in VueScan-Verzeichnis)
- Kurzanleitung: Wo VueScan die INI erwartet (Linux vs. macOS)

---

## Agent 3: Ingestion-Agent

**Rolle:** OCR-Pipeline und Bildverarbeitung  
**Skills:** `skills/ocr/SKILL.md`, `skills/vuescan/SKILL.md`

### Verantwortlichkeiten
- Implementiert `app/worker/preprocess.py`
- Implementiert `app/worker/ocr.py`
- Implementiert `app/worker/watcher.py`
- Verwaltet den Übergang inbox/ → archive/
- Misst und protokolliert OCR-Konfidenz

### Bildvorverarbeitung-Pipeline
```python
# Reihenfolge der Bildoperationen (kritisch für OCR-Qualität)
1. Laden (TIFF)
2. Graustufen sicherstellen
3. Deskew (Schräglagenkorrektur) — OpenCV
4. Kontrast normalisieren (CLAHE)
5. Rauschen reduzieren (median filter)
6. Binarisierung (Otsu threshold) — nur für OCR-Input
7. OCR via Tesseract (psm 1: auto mit OSD)
8. Konfidenz messen → needs_review-Flag setzen
9. Archivbild: TIFF → WebP (Qualität 85)
10. Thumbnail: 300px Breite, JPEG
```

### OCR-Qualitätsstufen
```
Konfidenz ≥ 85%  → meta_source='auto',    needs_review=0
Konfidenz 70-84% → meta_source='partial', needs_review=0
Konfidenz < 70%  → meta_source='partial', needs_review=1
                   → Nutzer-Benachrichtigung in WebApp
```

### Fehlerbehandlung
- Korrupte Dateien → quarantine/-Ordner + Log-Eintrag
- Tesseract nicht installiert → Klare Fehlermeldung mit Install-Anleitung
- Zu große Dateien (>50MB) → Warnung, trotzdem verarbeiten

### Aufgaben-Trigger
- "OCR verbessern"
- "Ingestion-Pipeline aufbauen"
- "Bildqualität für Scan X ist schlecht"

---

## Agent 4: Metadata-Agent

**Rolle:** KI-gestützte Metadaten-Extraktion via Claude API  
**Skill:** `skills/metadata/SKILL.md`

### Verantwortlichkeiten
- Implementiert `app/worker/metadata.py`
- Pflegt und optimiert den Extraktions-Prompt
- Validiert API-Antworten (JSON-Schema)
- Erkennt wann manuelle Eingabe nötig ist

### Extraktions-Prompt (Template)
```
Du analysierst den OCR-Text eines eingescannten deutschen Zeitungsartikels.
Extrahiere folgende Metadaten als JSON. Falls ein Feld nicht erkennbar ist,
setze null (NICHT raten).

Felder:
- newspaper: Name der Zeitung (z.B. "Süddeutsche Zeitung", "Die Zeit")
- article_date: Erscheinungsdatum (ISO 8601: YYYY-MM-DD), null wenn unklar
- page: Seitenangabe als String (z.B. "3", "Wirtschaft 7"), null wenn fehlt
- headline: Hauptschlagzeile des Artikels
- summary: Zusammenfassung in 2-3 deutschen Sätzen
- category: Eines von: Politik, Wirtschaft, Kultur, Sport, Wissenschaft,
            Lokales, International, Sonstiges
- tags: Array mit 3-5 relevanten Stichwörtern auf Deutsch

Antworte NUR mit validem JSON, ohne Erklärungen.

OCR-Text:
{ocr_text}
```

### Validierungs-Schema
```python
METADATA_SCHEMA = {
    "newspaper": str | None,
    "article_date": str | None,   # YYYY-MM-DD
    "page": str | None,
    "headline": str,              # Pflichtfeld
    "summary": str,               # Pflichtfeld
    "category": str,              # aus definierter Liste
    "tags": list[str]             # 3-5 Einträge
}
```

### Kosten-Bewusstsein
- Nur `full_text[:3000]` an API senden (erste 3000 Zeichen reichen)
- Modell: claude-sonnet-4 (Kosten/Leistung optimal)
- Caching: Bereits extrahierte Artikel nicht erneut senden
- Logging: API-Aufrufe zählen für Kostentransparenz

### Aufgaben-Trigger
- "Metadaten werden falsch erkannt"
- "Zeitung XY wird nicht erkannt"
- "Prompt optimieren"

---

## Agent 5: WebApp-Agent

**Rolle:** FastAPI Backend + HTMX Frontend  
**Skill:** `skills/webapp/SKILL.md`

### Verantwortlichkeiten
- Implementiert `app/web/` (FastAPI + Jinja2 + HTMX)
- Implementiert `app/cli/` (Click)
- Stellt Suchfunktionen bereit (REST-Endpunkte)
- Baut Review-Interface für needs_review-Artikel

### API-Endpunkte (Konzept)
```
GET  /                    → Startseite / Suchmaske
GET  /search?q=&filter=   → Suchergebnisse (HTMX-Fragment)
GET  /articles/{id}       → Artikel-Detailansicht
GET  /articles/{id}/edit  → Metadaten-Editor
POST /articles/{id}       → Metadaten speichern
GET  /review              → Liste: needs_review=1
GET  /stats               → Archiv-Statistiken
POST /process             → Ingestion manuell triggern
GET  /export?format=csv   → Export
```

### Frontend-Komponenten
```
Suchseite:
├── Suchfeld (Volltext, FTS5)
├── Filter: Zeitung / Zeitraum (von–bis) / Kategorie
├── Ergebnisliste: Thumbnail + Schlagzeile + Snippet + Datum
└── Pagination

Detailansicht:
├── Originalbild (WebP, zoombar)
├── OCR-Text (scrollbar, kopierbar)
├── Metadaten-Panel (lesend)
└── "Bearbeiten"-Button → Editor

Review-Queue:
├── Zahl der offenen Artikel (Badge)
├── Artikel-Karte mit Bild + aktuelle Metadaten
└── Inline-Formular zum Korrigieren

Statistiken:
├── Gesamtanzahl Artikel
├── Artikel pro Zeitung (Balkendiagramm)
├── Zeitliche Verteilung
└── Review-Queue-Status
```

### CLI-Befehle
```bash
zeitungsarchiv process [--dir PATH]   # Scans verarbeiten
zeitungsarchiv search "STICHWORT"     # Volltextsuche
zeitungsarchiv show ID                # Artikel anzeigen
zeitungsarchiv stats                  # Archiv-Übersicht
zeitungsarchiv export --format csv    # Export
zeitungsarchiv serve                  # WebApp starten (ohne Docker)
```

### Aufgaben-Trigger
- "Suche funktioniert nicht"
- "Neues Filter-Kriterium hinzufügen"
- "Export-Format XY"
- UI/UX-Verbesserungen

---

## Agent-Interaktionen & Datenfluss

```
Scanner-Agent
    │  config/vuescan.ini
    │  → Nutzer scannt manuell
    │  → TIFF landet in inbox/
    ▼
Ingestion-Agent
    │  Bildvorverarbeitung + OCR
    │  → ocr_text, ocr_confidence
    ▼
Metadata-Agent
    │  Claude API → JSON-Metadaten
    │  → article-Record komplett
    ▼
Datenbank (SQLite + FTS5)
    │
    ├──▶ WebApp-Agent (Suche, Anzeige, Review)
    └──▶ CLI (Export, Statistiken)
```

---

## Skill-Erstellung: Prioritäten

Die Skills sollten in dieser Reihenfolge erstellt werden:

1. `skills/vuescan/SKILL.md` — Scanner-Agent braucht ihn zuerst
2. `skills/ocr/SKILL.md` — Ingestion-Agent, kritischer Pfad
3. `skills/metadata/SKILL.md` — Metadata-Agent, KI-Kern
4. `skills/webapp/SKILL.md` — WebApp-Agent, letzter Schritt

Jeder Skill enthält:
- YAML-Frontmatter (name, description, triggers)
- Schritt-für-Schritt-Anweisungen
- Beispiel-Code / Konfigurationsschnipsel
- Typische Fehler + Lösungen
- Testfälle

---

## Konventionen für alle Agenten

### Code-Stil
- Python: Black-formatiert, Type Hints, Docstrings
- Fehlerbehandlung: Immer try/except mit sinnvollen Meldungen
- Logging: `logging`-Modul, kein print() in Produktionscode

### Commits (wenn Git verwendet)
```
feat(ocr): Deskew-Algorithmus verbessert
fix(metadata): Datumsformat-Parsing für alte Artikel
docs(agents): Scanner-Agent Konfiguration ergänzt
```

### Testen
- Jede Pipeline-Stufe separat testbar
- Testdaten: 3–5 Beispiel-TIFFs mit bekannten Ergebnissen
- Konfidenz-Schwellwerte empirisch an echten Scans kalibrieren

---

## Nächste Schritte (für Orchestrator)

```
SESSION 1: Scanner-Agent aktivieren
  → vuescan.ini generieren + testen
  → Ersten Scan durchführen

SESSION 2: Ingestion-Agent
  → OCR-Pipeline aufbauen
  → An echtem TIFF testen + Qualität messen

SESSION 3: Metadata-Agent
  → Claude API Extraktion implementieren
  → Prompt an echten Artikeln kalibrieren

SESSION 4: WebApp-Agent
  → CLI + minimale WebApp
  → Docker-Container bauen + testen
```
