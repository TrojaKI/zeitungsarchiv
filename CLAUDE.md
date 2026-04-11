# CLAUDE.md — Zeitungsarchiv

Lokales Archiv-System für eingescannte Zeitungsartikel. Scans werden per OCR in Text umgewandelt, mit KI-Metadaten angereichert und in einer durchsuchbaren SQLite-Datenbank gespeichert. Zugriff via WebApp (FastAPI + HTMX) und CLI.

**Plattform:** Linux + macOS | **Scanner:** CanoScan 8800F + VueScan | **Sprache:** Deutsch

---

## Verzeichnisstruktur

```
app/
├── llm/provider.py         ← Multi-provider LLM-Abstraktion (chat_json)
├── worker/
│   ├── watcher.py          ← watchdog Inbox-Observer
│   ├── ingestion.py        ← Ingestion-Pipeline (orchestriert alle Worker)
│   ├── preprocess.py       ← Bildoptimierung (deskew, kontrast, WebP/JPEG)
│   ├── ocr.py              ← Tesseract-Wrapper
│   ├── metadata.py         ← Artikel-Metadaten via LLM
│   ├── places.py           ← Ortsextraktion via LLM
│   ├── books.py            ← Buchextraktion via LLM
│   ├── recipes.py          ← Rezeptextraktion via LLM
│   ├── geocoder.py         ← Nominatim-Geocodierung
│   └── stitch.py           ← OpenCV Scan-Stitching (mehrseitige Artikel)
├── db/
│   ├── schema.sql          ← SQLite Schema + FTS5 + Trigger
│   └── database.py         ← alle DB-Funktionen
├── web/
│   ├── main.py             ← FastAPI App (mountet /static + /archive)
│   ├── templating.py       ← shared Jinja2Templates + from_json Filter
│   └── routes/             ← search, articles, places, books, recipes, review, admin
└── cli/main.py             ← Click CLI
config/settings.toml        ← App-Konfiguration
```

---

## Tech-Stack

| Komponente | Technologie |
|---|---|
| Sprache | Python 3.12 |
| OCR | Tesseract 5 + pytesseract (deu) |
| Bildverarbeitung | Pillow + OpenCV |
| KI-Metadaten | Multi-provider LLM (Ollama / OpenRouter / LangDock) |
| Datenbank | SQLite + FTS5 |
| Web-Backend | FastAPI + Uvicorn |
| Web-Frontend | HTMX + Jinja2 |
| CLI | Click |
| Container | Docker + Compose |
| Inbox-Watch | watchdog |

---

## LLM-Provider (`app/llm/provider.py`)

Einstiegspunkt: `from app.llm.provider import chat_json`

- Provider via `LLM_PROVIDER=ollama|openrouter|langdock` (Default: `ollama`)
- `fallback_on_empty=True` → bidirektionaler Cross-Provider-Fallback:
  - `ollama` → OpenRouter wenn Ollama leer/fehlgeschlagen
  - `openrouter` → Ollama wenn alle OpenRouter-Models erschöpft
- Beide Provider unterstützen kommagetrennte Fallback-Listen:
  - `OLLAMA_MODELS=model1,model2`
  - `OPENROUTER_MODELS=model1,model2`
- OpenRouter: fängt `RateLimitError`, `NotFoundError`, `BadRequestError` → versucht nächstes Model

---

## Wichtige Patterns

- **HTMX-Partials:** `request.headers.get("hx-request")` → Fragment statt Full-Page
- **JSON-Felder:** `tags`, `locations`, `urls` als JSON-Array in SQLite gespeichert
- **`from_json` Filter:** Jinja2-Filter in `templating.py` zum Parsen dieser Felder
- **json-repair:** `places.py`, `books.py`, `recipes.py` nutzen `repair_json()` als Fallback bei fehlerhaftem LLM-JSON
- **Scan-Stitching:** `_01` + `_02` TIFFs → `_00` Panorama via ORB + RANSAC + lineares Blending
- **Deskew:** Winkelberechnung auf Graustufen, Rotation auf Farbbild; 16-bit TIFFs → uint8 via `/ 256`

---

## Designentscheidungen

1. **TIFF als Scan-Master** — verlustfrei für OCR; WebP erst nach der Verarbeitung
2. **needs_review-Flag** — OCR-Konfidenz < 70% → manuelle Prüfung
3. **SQLite statt Postgres** — kein Server, portabel, einfaches Backup
4. **HTMX statt React** — kein Build-Toolchain, kein Node.js
5. **Volumes statt COPY** — `inbox/`, `archive/`, `db/` überleben Container-Rebuilds

---

## Style
Follow @~/.claude/docs/STYLE.md for all coding conventions.
