"""Local metadata extraction for newspaper article OCR text via Ollama."""

import json
import logging
import os
import re
from datetime import datetime

import ollama

log = logging.getLogger(__name__)

# Configurable via environment variables
OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")

VALID_CATEGORIES = {
    "Politik", "Wirtschaft", "Kultur", "Sport", "Ernährung",
    "Wissenschaft", "Lokales", "International", "Reise", "Sonstiges",
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
- summary: Zusammenfassung des Artikelinhalts in 2-3 deutschen Sätzen. Pflichtfeld. \
Hinweis: "Von X" am Anfang des Textes bezeichnet den Autor des Zeitungsartikels, \
nicht die Hauptperson oder Buchautor im Artikel.
- category: Eines von exakt: Politik, Wirtschaft, Kultur, Sport, Ernährung, \
Wissenschaft, Lokales, International, Reise, Sonstiges
- tags: Array mit 3-5 relevanten deutschen Stichwörtern
- locations: Array mit allen Ortsnamen, Städten, Regionen und Ländern die im Artikel \
vorkommen. Z.B. ["Wien", "Wachau", "Österreich", "Gardasee", "Italien"]. Leeres Array \
wenn keine Orte erkennbar.
- urls: Array mit allen Websites, URLs und E-Mail-Adressen die im Text vorkommen. \
Z.B. ["www.apfelbauer.at", "info@hotel.com"]. Leeres Array wenn keine vorhanden.

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
    """Validate and sanitize the raw model response dict."""
    if data.get("category") not in VALID_CATEGORIES:
        data["category"] = "Sonstiges"

    if not isinstance(data.get("tags"), list):
        data["tags"] = []
    data["tags"] = [str(t) for t in data["tags"][:5]]

    if not isinstance(data.get("locations"), list):
        data["locations"] = []
    data["locations"] = [str(l) for l in data["locations"]]

    if not isinstance(data.get("urls"), list):
        data["urls"] = []
    data["urls"] = [str(u) for u in data["urls"]]

    date = data.get("article_date")
    if date is not None and not _is_valid_date(str(date)):
        data["article_date"] = None

    if not data.get("headline"):
        data["headline"] = "Unbekannt"

    if not isinstance(data.get("summary"), str):
        data["summary"] = ""

    return data


def _parse_json(raw: str) -> dict | None:
    """Try to parse JSON, stripping accidental markdown fences if present."""
    raw = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` wrappers that some models add
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def extract_metadata(ocr_text: str,
                     model: str | None = None,
                     host: str | None = None) -> dict:
    """
    Extract structured metadata from OCR text using a local Ollama model.

    Uses format='json' to request structured output.
    Falls back to _FALLBACK on any error — never raises.
    """
    if not ocr_text.strip():
        log.warning("extract_metadata: empty OCR text, returning fallback")
        return dict(_FALLBACK)

    _model = model or OLLAMA_MODEL
    _host  = host  or OLLAMA_HOST
    prompt = _PROMPT.format(ocr_text=ocr_text[:3000])

    try:
        client   = ollama.Client(host=_host)
        response = client.chat(
            model=_model,
            messages=[{"role": "user", "content": prompt}],
            format="json",
        )
        raw  = response.message.content
        data = _parse_json(raw)

        if data is None:
            log.error("extract_metadata: invalid JSON from model, returning fallback\nRaw: %s", raw[:200])
            return dict(_FALLBACK)

        log.info("Ollama metadata extracted (model=%s)", _model)
        return _validate(data)

    except Exception as exc:
        log.error("extract_metadata: Ollama error: %s", exc)
        return dict(_FALLBACK)
