---
name: metadata-extraction
description: >
  Extrahiere strukturierte Metadaten (Zeitung, Datum, Schlagzeile, Kategorie,
  Tags) aus OCR-Text deutscher Zeitungsartikel via Claude API. Verwende diesen
  Skill bei Metadaten-Extraktion, Prompt-Optimierung, API-Kosten, und wenn
  Zeitung/Datum/Kategorie falsch erkannt werden.
---

# Skill: Metadaten-Extraktion via Claude API

## Abhängigkeiten

```bash
pip install anthropic
```

## Extraktions-Funktion

```python
# app/worker/metadata.py
import anthropic
import json

client = anthropic.Anthropic()  # liest ANTHROPIC_API_KEY aus Umgebung

EXTRACTION_PROMPT = """Du analysierst den OCR-Text eines eingescannten \
deutschen Zeitungsartikels.
Extrahiere folgende Metadaten als JSON. Falls ein Feld nicht sicher \
erkennbar ist, setze null — NICHT raten.

Felder:
- newspaper: Name der Zeitung (z.B. "Süddeutsche Zeitung", "Die Zeit", \
  "FAZ"). null wenn unklar.
- article_date: Erscheinungsdatum im Format YYYY-MM-DD. null wenn nicht \
  erkennbar.
- page: Seitenangabe als String (z.B. "3", "Wirtschaft 7"). null wenn fehlt.
- headline: Hauptschlagzeile des Artikels. Pflichtfeld.
- summary: Zusammenfassung in 2-3 deutschen Sätzen. Pflichtfeld.
- category: Eines von exakt: Politik, Wirtschaft, Kultur, Sport, \
  Wissenschaft, Lokales, International, Sonstiges
- tags: Array mit 3-5 relevanten deutschen Stichwörtern

Antworte NUR mit validem JSON ohne Markdown-Backticks oder Erklärungen.

OCR-Text (erste 3000 Zeichen):
{ocr_text}
"""

def extract_metadata(ocr_text: str) -> dict:
    """Extrahiert Metadaten aus OCR-Text via Claude API."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": EXTRACTION_PROMPT.format(ocr_text=ocr_text[:3000])
        }]
    )

    raw = response.content[0].text.strip()

    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: Minimale Metadaten
        return {
            "newspaper": None,
            "article_date": None,
            "page": None,
            "headline": "Unbekannt",
            "summary": "",
            "category": "Sonstiges",
            "tags": [],
        }

    return validate_metadata(metadata)


def validate_metadata(data: dict) -> dict:
    """Validiert und bereinigt API-Antwort."""
    VALID_CATEGORIES = {
        "Politik", "Wirtschaft", "Kultur", "Sport",
        "Wissenschaft", "Lokales", "International", "Sonstiges"
    }

    # Kategorie validieren
    if data.get("category") not in VALID_CATEGORIES:
        data["category"] = "Sonstiges"

    # Tags als Liste sicherstellen
    if not isinstance(data.get("tags"), list):
        data["tags"] = []
    data["tags"] = data["tags"][:5]   # max. 5 Tags

    # Datum-Format prüfen (YYYY-MM-DD)
    date = data.get("article_date")
    if date and not _is_valid_date(date):
        data["article_date"] = None

    return data
```

## Kosten-Management

- Nur `ocr_text[:3000]` senden (erste 3000 Zeichen genügen)
- Modell: `claude-sonnet-4` (optimal Preis/Leistung)
- Bereits verarbeitete Artikel nicht erneut senden (DB-Check)
- API-Aufrufe im Log zählen

## Prompt-Optimierung

Wenn Metadaten falsch erkannt werden:

1. **Zeitung nicht erkannt:** Zeige dem Prompt typische Zeitungsnamen
   aus dem Archiv als Referenz mit.
2. **Datum fehlt:** Datum steht oft im Kopf oder Fuß — OCR-Text prüfen,
   ggf. regulären Ausdruck vorschalten.
3. **Kategorie falsch:** Liste der Kategorien im Prompt konkretisieren
   mit je einem Beispiel.

## Felder die manuell ergänzt werden sollten

Wenn nach Extraktion noch `null`-Felder existieren, `needs_review=1` setzen.
Folgende Felder sind Pflicht für eine gute Suche:
- `headline` (immer extrahierbar)
- `article_date` (wichtig für Zeitfilter)
- `category` (wichtig für Kategorie-Filter)
