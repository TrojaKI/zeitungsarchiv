"""Extract structured book recommendations from OCR text."""

import json
import logging
import urllib.parse
import urllib.request

from json_repair import repair_json

from app.llm.provider import chat_json

log = logging.getLogger(__name__)

_OL_BASE = "https://openlibrary.org"
_OL_TIMEOUT = 5  # seconds


def lookup_book_url(book: dict) -> str | None:
    """Query Open Library for a book URL (no API key required).

    Strategy:
      1. ISBN lookup — most reliable, instant redirect.
      2. Title + author search — fallback when ISBN is missing.

    Returns the Open Library URL or None on failure / no match.
    """
    isbn = (book.get("isbn") or "").replace("-", "").replace(" ", "")
    if isbn:
        url = f"{_OL_BASE}/isbn/{isbn}"
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=_OL_TIMEOUT) as resp:
                if resp.status == 200:
                    return url
        except Exception:
            pass  # fall through to title search

    title = book.get("title")
    if not title:
        return None

    params = {"title": title, "limit": "1"}
    author = book.get("author")
    if author:
        params["author"] = author
    search_url = f"{_OL_BASE}/search.json?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(
            search_url,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_OL_TIMEOUT) as resp:
            data = json.loads(resp.read())
        docs = data.get("docs") or []
        if docs and docs[0].get("key"):
            return f"{_OL_BASE}{docs[0]['key']}"
    except Exception as exc:
        log.debug("Open Library lookup failed for '%s': %s", title, exc)

    return None

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
- url: Link zum Buch beim Verlag oder Buchshop falls im Artikel erwähnt, sonst leer

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

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.debug("extract_books: strict JSON parse failed, attempting repair")
            data = json.loads(repair_json(raw))

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

        # Enrich missing URLs via Open Library
        for b in unique:
            if not b.get("url"):
                found = lookup_book_url(b)
                if found:
                    b["url"] = found
                    log.debug("Open Library URL found for '%s': %s", b.get("title"), found)

        return unique

    except Exception as exc:
        log.error("extract_books failed: %s", exc)
        return []


def _clean(b: dict) -> dict:
    """Ensure all expected fields are present, strip whitespace."""
    fields = ("title", "author", "publisher", "year", "pages", "price", "isbn", "description", "url")
    return {f: (str(b[f]).strip() if b.get(f) else None) for f in fields}
