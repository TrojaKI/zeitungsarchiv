"""Extract structured book recommendations from OCR text."""

import json
import logging
import os

import ollama

log = logging.getLogger(__name__)

OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")

_PROMPT = """\
Du analysierst den OCR-Text eines eingescannten deutschen Zeitungsartikels.
Extrahiere alle Buchtipps, Buchempfehlungen oder vorgestellten Bücher aus dem Text.

Gib ein JSON-Array zurück. Jeder Eintrag hat diese Felder \
(null wenn nicht vorhanden):
- title: Buchtitel
- author: Autor(en)
- publisher: Verlag
- year: Erscheinungsjahr
- pages: Seitenanzahl
- price: Preis (z.B. "19,90 Euro")
- isbn: ISBN-Nummer
- description: kurze Beschreibung oder Bewertung aus dem Artikel (1-2 Sätze)

Wenn keine Bücher im Text empfohlen oder vorgestellt werden, gib ein leeres Array [] zurück.
Antworte NUR mit validem JSON ohne Markdown-Backticks.

OCR-Text:
{ocr_text}
"""


def extract_books(ocr_text: str,
                  model: str | None = None,
                  host: str | None = None) -> list[dict]:
    """
    Extract structured book recommendations from article OCR text via Ollama.

    Returns a list of book dicts. Returns [] on empty text or any error.
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
        # Model sometimes returns {"books": [...]} instead of a bare array
        if isinstance(data, dict):
            for key in ("books", "results", "items", "entries"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                return []

        if not isinstance(data, list):
            return []

        return [_clean(b) for b in data if isinstance(b, dict)]

    except Exception as exc:
        log.error("extract_books failed: %s", exc)
        return []


def _clean(b: dict) -> dict:
    """Ensure all expected fields are present, strip whitespace."""
    fields = ("title", "author", "publisher", "year", "pages", "price", "isbn", "description")
    return {f: (str(b[f]).strip() if b.get(f) else None) for f in fields}
