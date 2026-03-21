"""Extract structured place listings (restaurants, hotels, shops) from OCR text."""

import json
import logging
import os

import ollama

log = logging.getLogger(__name__)

OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")

_PROMPT = """\
Du analysierst den OCR-Text eines eingescannten deutschen Zeitungsartikels.
Extrahiere alle konkreten Orte, Lokale, Hotels, Restaurants, Geschäfte oder \
Sehenswürdigkeiten die im Text mit Adressdaten, Telefonnummer, Öffnungszeiten \
oder Website genannt werden.

Gib ein JSON-Array zurück. Jeder Eintrag hat diese Felder \
(null wenn nicht vorhanden):
- name: Name des Betriebs oder Ortes
- description: kurze Beschreibung aus dem Artikel (1-2 Sätze)
- address: Straße und Hausnummer
- postal_code: Postleitzahl
- city: Ort/Stadt — lies direkt aus der Adresse im Text ab; leite die Stadt NICHT \
aus dem Namen des Betriebs ab (z.B. "Stuttnerhof" liegt in "Bisamberg", nicht in Stuttgart)
- country: Land (z.B. "Österreich", "Italien", "Deutschland", "Ungarn")
- phone: Telefonnummer
- hours: Öffnungszeiten
- url: Website-Adresse
- rating: Bewertung des Betriebs — leite sie aus dem Ton des Textes ab. Das +/- \
Symbol wird oft als farbige Grafik gedruckt und fehlt im OCR. Mögliche Werte:
  "+" = klar positiv (nur Lob, Weiterempfehlung, guter Gesamteindruck)
  "-" = klar negativ (nur Kritik, Enttäuschung, nicht empfehlenswert)
  "+/-" = gemischt (sowohl Lobendes als auch Kritisches im gleichen Bericht — \
z.B. gutes Essen aber schlechter Service, oder "Nachbessern wäre gut")
  null = kein Urteil erkennbar (rein informativer Text ohne Wertung)

Wenn keine solchen Einträge vorhanden sind, gib ein leeres Array [] zurück.
Antworte NUR mit validem JSON ohne Markdown-Backticks.

OCR-Text:
{ocr_text}
"""


def extract_places(ocr_text: str,
                   model: str | None = None,
                   host: str | None = None) -> list[dict]:
    """
    Extract structured place listings from article OCR text via Ollama.

    Returns a list of place dicts. Returns [] on empty text or any error.
    """
    if not ocr_text.strip():
        return []

    _model = model or OLLAMA_MODEL
    _host  = host  or OLLAMA_HOST

    try:
        client   = ollama.Client(host=_host)
        response = client.chat(
            model=_model,
            messages=[{"role": "user", "content": _PROMPT.format(ocr_text=ocr_text)}],
            format="json",
        )
        raw = response.message.content.strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

        data = json.loads(raw)
        # Model sometimes wraps the array in an object — find the first list value
        if isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list)]
            if lists:
                data = lists[0]
            else:
                return []

        if not isinstance(data, list):
            return []

        return [_clean(p) for p in data if isinstance(p, dict)]

    except Exception as exc:
        log.error("extract_places failed: %s", exc)
        return []


def _clean(p: dict) -> dict:
    """Ensure all expected fields are present, strip whitespace."""
    fields = ("name", "description", "address", "postal_code",
              "city", "country", "phone", "hours", "url", "rating")
    return {f: (str(p[f]).strip() if p.get(f) else None) for f in fields}
