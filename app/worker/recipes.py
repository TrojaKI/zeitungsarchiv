"""Extract structured recipes from OCR text."""

import json
import logging
import os

import ollama

log = logging.getLogger(__name__)

OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")

_PROMPT = """\
Du analysierst den OCR-Text eines eingescannten deutschen Zeitungsartikels.
Extrahiere alle Rezepte, die tatsächlich im Text abgedruckt sind — mit Zutaten \
und/oder Zubereitungsschritten.

Wichtig für den Rezeptnamen (name):
- Verwende als name den Rubriktitel oder die Abschnittsüberschrift, die direkt \
über den Zutaten oder der Zubereitung steht (z.B. "Koch-Inspirationen", "Kinderleicht", \
"Eiweißbrot", "Sommersalat").
- Wenn unter einer Überschrift ein konkreter Gerichtname steht, verwende den \
Gerichtnamen als name.
- Nutze niemals generische Texte wie "Rezept 1" oder "Unbekannt".

NICHT extrahieren:
- Bloße Erwähnungen eines Rezeptnamens ohne Zutaten/Zubereitung
- Links zu Rezept-Websites
- Namen von Gerichten ohne Rezeptangaben

Gib ein JSON-Array zurück. Jeder Eintrag hat diese Felder (null wenn nicht vorhanden):
- name: Name des Rezepts oder Rubriktitel (z.B. "Koch-Inspirationen", "Eiweißbrot")
- category: Kategorie (z.B. "Brot", "Hauptgericht", "Dessert", "Snack")
- servings: Portionen oder Menge (z.B. "1 Laib", "4 Personen")
- prep_time: Zubereitungszeit (z.B. "30 Minuten", "1 Stunde")
- ingredients: alle Zutaten als zusammenhängender Text, eine Zutat pro Zeile
- instructions: Zubereitungsschritte als zusammenhängender Text

Wenn keine vollständigen Rezepte im Text enthalten sind, gib [] zurück.
Antworte NUR mit validem JSON ohne Markdown-Backticks.

OCR-Text:
{ocr_text}
"""


def extract_recipes(ocr_text: str,
                    model: str | None = None,
                    host: str | None = None) -> list[dict]:
    """
    Extract structured recipes from article OCR text via Ollama.

    Returns a list of recipe dicts. Returns [] on empty text or any error.
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
        # Model sometimes returns {"recipes": [...]} instead of a bare array
        if isinstance(data, dict):
            for key in ("recipes", "results", "items", "entries"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                return []

        if not isinstance(data, list):
            return []

        return [_clean(r) for r in data if isinstance(r, dict)]

    except Exception as exc:
        log.error("extract_recipes failed: %s", exc)
        return []


def _clean(r: dict) -> dict:
    """Ensure all expected fields are present, strip whitespace."""
    fields = ("name", "category", "servings", "prep_time", "ingredients", "instructions")
    return {f: (str(r[f]).strip() if r.get(f) else None) for f in fields}
