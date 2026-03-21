"""Extract structured book recommendations from OCR text."""

import json
import logging

from app.llm.provider import chat_json

log = logging.getLogger(__name__)

_PROMPT = """\
Du analysierst den OCR-Text eines eingescannten deutschen Zeitungsartikels.
Extrahiere ausschließlich physisch erschienene Bücher, die im Artikel vorgestellt, \
rezensiert oder empfohlen werden (z.B. "Buch zur Woche", Buchkritiken, Buchvorstellungen).

Ein Buch muss mindestens eines dieser Merkmale aufweisen: Verlag, Seitenanzahl, Preis oder ISBN.

NICHT als Buch zählen:
- Rezepte oder Kochtipps (auch wenn sie einen Namen haben)
- Websites, Online-Shops oder Apps
- Kochkurse, Workshops oder Veranstaltungen
- Reiseführer-Markennamen ohne Buchrezension

Gib ein JSON-Array zurück. Jeder Eintrag hat diese Felder (null wenn nicht vorhanden):
- title: Buchtitel
- author: Autor(en)
- publisher: Verlag
- year: Erscheinungsjahr
- pages: Seitenanzahl als Zahl-String (z.B. "432")
- price: Preis (z.B. "19,90 Euro")
- isbn: ISBN-Nummer
- description: kurze Beschreibung oder Bewertung aus dem Artikel (1-2 Sätze)

Wenn keine Bücher vorgestellt oder rezensiert werden, gib [] zurück.
Antworte NUR mit validem JSON ohne Markdown-Backticks.

OCR-Text:
{ocr_text}
"""


_BOOK_SECTION_PATTERNS = [
    "BUCH ZUR WOCHE", "BUCH DER WOCHE", "BUCHTIPP", "BUCHTIPPS",
    "LESETIPP", "LESETIPPS", "BUCHEMPFEHLUNG", "NEUE BÜCHER",
]


def _extract_book_sections(text: str) -> str:
    """
    Extract paragraphs likely to contain book info.

    Looks for known section headers (e.g. "BUCH ZUR WOCHE") and returns
    the surrounding context. Falls back to the last 1500 chars of the text
    where box-style book info often appears in scanned layout.
    """
    upper = text.upper()
    for marker in _BOOK_SECTION_PATTERNS:
        pos = upper.find(marker)
        if pos != -1:
            # Return from the marker to end of text (book info follows)
            return text[pos:]
    # No explicit section found — return last 1500 chars as fallback
    return text[-1500:] if len(text) > 1500 else text


def extract_books(ocr_text: str) -> list[dict]:
    """
    Extract structured book recommendations from article OCR text via the configured LLM provider.

    Returns a list of book dicts. Returns [] on empty text or any error.
    """
    if not ocr_text.strip():
        return []

    ocr_text = _extract_book_sections(ocr_text)

    try:
        raw = chat_json(_PROMPT.format(ocr_text=ocr_text)).strip()
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

        books = [_clean(b) for b in data if isinstance(b, dict)]
        # Deduplicate by title to guard against model hallucinating duplicate entries
        seen: set[str] = set()
        unique = []
        for b in books:
            key = (b.get("title") or "").lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(b)
        return unique

    except Exception as exc:
        log.error("extract_books failed: %s", exc)
        return []


def _clean(b: dict) -> dict:
    """Ensure all expected fields are present, strip whitespace."""
    fields = ("title", "author", "publisher", "year", "pages", "price", "isbn", "description")
    return {f: (str(b[f]).strip() if b.get(f) else None) for f in fields}
