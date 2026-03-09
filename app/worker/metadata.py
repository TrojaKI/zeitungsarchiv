"""Claude API metadata extraction for newspaper article OCR text."""

import json
import logging
import re
from datetime import datetime

import anthropic

log = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None

VALID_CATEGORIES = {
    "Politik", "Wirtschaft", "Kultur", "Sport",
    "Wissenschaft", "Lokales", "International", "Sonstiges",
}

_PROMPT = """\
Du analysierst den OCR-Text eines eingescannten deutschen Zeitungsartikels.
Extrahiere folgende Metadaten als JSON. Falls ein Feld nicht sicher erkennbar ist, \
setze null — NICHT raten.

Felder:
- newspaper: Name der Zeitung (z.B. "Kurier", "Süddeutsche Zeitung", "Die Zeit"). \
null wenn unklar.
- article_date: Erscheinungsdatum im Format YYYY-MM-DD. null wenn nicht erkennbar.
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

_FALLBACK: dict = {
    "newspaper": None,
    "article_date": None,
    "page": None,
    "headline": "Unbekannt",
    "summary": "",
    "category": "Sonstiges",
    "tags": [],
}


def _get_client() -> anthropic.Anthropic:
    """Return a shared Anthropic client (lazy init)."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _client


def _is_valid_date(value: str) -> bool:
    """Return True if value matches YYYY-MM-DD and is a real calendar date."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _validate(data: dict) -> dict:
    """Validate and sanitize the raw API response dict."""
    # Category must be from the allowed set
    if data.get("category") not in VALID_CATEGORIES:
        data["category"] = "Sonstiges"

    # Tags must be a list capped at 5 entries
    if not isinstance(data.get("tags"), list):
        data["tags"] = []
    data["tags"] = [str(t) for t in data["tags"][:5]]

    # Date must be YYYY-MM-DD and a valid calendar date
    date = data.get("article_date")
    if date is not None and not _is_valid_date(str(date)):
        data["article_date"] = None

    # Headline must be a non-empty string
    if not data.get("headline"):
        data["headline"] = "Unbekannt"

    # Summary must be a string
    if not isinstance(data.get("summary"), str):
        data["summary"] = ""

    return data


def extract_metadata(ocr_text: str, model: str = "claude-sonnet-4-20250514") -> dict:
    """
    Extract structured metadata from OCR text via Claude API.

    Sends only the first 3000 characters to keep API costs low.
    Returns a validated metadata dict. On any error returns _FALLBACK.
    """
    if not ocr_text.strip():
        log.warning("extract_metadata: empty OCR text, returning fallback")
        return dict(_FALLBACK)

    prompt = _PROMPT.format(ocr_text=ocr_text[:3000])

    try:
        response = _get_client().messages.create(
            model=model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        log.info("Claude API call: model=%s input_tokens=%s output_tokens=%s",
                 model,
                 response.usage.input_tokens,
                 response.usage.output_tokens)

        raw = response.content[0].text.strip()
        data = json.loads(raw)
        return _validate(data)

    except json.JSONDecodeError:
        log.error("extract_metadata: invalid JSON from API, returning fallback")
        return dict(_FALLBACK)
    except anthropic.APIError as exc:
        log.error("extract_metadata: API error: %s", exc)
        return dict(_FALLBACK)
