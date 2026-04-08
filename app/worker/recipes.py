"""Extract structured recipes from OCR text."""

import json
import logging

from json_repair import repair_json

from app.llm.provider import chat_json

log = logging.getLogger(__name__)

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

Gib IMMER ein JSON-Array zurück — auch wenn nur ein Rezept gefunden wird: [{{...}}]
Jeder Eintrag hat diese Felder (null wenn nicht vorhanden):
- name: Name des Rezepts oder Rubriktitel (z.B. "Koch-Inspirationen", "Eiweißbrot")
- category: Kategorie (z.B. "Brot", "Hauptgericht", "Dessert", "Snack", "Eingemachtes")
- servings: Portionen oder Menge (z.B. "1 Laib", "4 Personen")
- prep_time: Zubereitungszeit (z.B. "30 Minuten", "1 Stunde")
- ingredients: alle Zutaten als zusammenhängender Text, eine Zutat pro Zeile
- instructions: Zubereitungsschritte als zusammenhängender Text

Wenn mehrere Rezepte im Text stehen, extrahiere ALLE als separate Einträge im Array.
Wenn keine vollständigen Rezepte im Text enthalten sind, gib [] zurück.
Antworte NUR mit validem JSON-Array ohne Markdown-Backticks. Kein einzelnes Objekt — immer ein Array.

OCR-Text:
{ocr_text}
"""


def extract_recipes(ocr_text: str) -> list[dict]:
    """
    Extract structured recipes from article OCR text via the configured LLM provider.

    Returns a list of recipe dicts. Returns [] on empty text or any error.
    """
    if not ocr_text.strip():
        return []

    try:
        raw = chat_json(_PROMPT.format(ocr_text=ocr_text), fallback_on_empty=True).strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.debug("extract_recipes: strict JSON parse failed, attempting repair")
            data = json.loads(repair_json(raw))

        # Model sometimes wraps the array in an object — find the first list value
        if isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list)]
            if lists:
                data = lists[0]
            elif any(k in data for k in ("name", "ingredients", "instructions")):
                # Single recipe object returned without wrapper — wrap it
                data = [data]
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
