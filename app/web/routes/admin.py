"""Admin routes: stats, manual ingestion trigger, CSV/JSON export."""

import csv
import io
import json
import logging
import os
import threading
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from markupsafe import escape

log = logging.getLogger(__name__)

_ingest_lock = threading.Lock()
_ingest_status: dict = {"state": "idle", "message": ""}

from app.db.database import (get_places_with_suspect_coords, get_places_without_coords,
                              get_review_count, get_stats, search_full)
from app.web.templating import templates as _templates

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))
_INBOX = Path(os.getenv("INBOX_DIR", "/app/inbox"))
_ARCHIVE = Path(os.getenv("ARCHIVE_DIR", "/app/archive"))


@router.get("/stats", response_class=HTMLResponse)
async def stats(request: Request):
    data = get_stats(_DB)
    ungeocodiert = get_places_without_coords(_DB)
    verdaechtig = get_places_with_suspect_coords(_DB)
    return _templates.TemplateResponse(
        request,
        "stats.html",
        {"request": request, "review_count": get_review_count(_DB),
         "ungeocodiert": ungeocodiert, "verdaechtig": verdaechtig, **data},
    )


def _run_ingest():
    """Background task: ingest all TIFFs in inbox, update status.

    The lock is held for the entire run so a second trigger cannot process
    the same inbox files concurrently.
    """
    global _ingest_status
    if not _ingest_lock.acquire(blocking=False):
        log.warning("Ingestion already running, ignoring duplicate trigger")
        return
    try:
        _ingest_status = {"state": "running", "message": "Verarbeitung läuft..."}
        from app.worker.ingestion import ingest_directory
        ids = ingest_directory(_INBOX, _ARCHIVE, _DB)
        _ingest_status = {"state": "done", "message": f"{len(ids)} Artikel verarbeitet."}
    except Exception as exc:
        log.exception("Ingestion failed: %s", exc)
        _ingest_status = {"state": "error", "message": f"Fehler: {exc}"}
    finally:
        _ingest_lock.release()


@router.post("/process")
async def process_inbox(request: Request, background_tasks: BackgroundTasks):
    """Queue ingestion of all TIFFs in inbox and return immediately."""
    from app.worker.ingestion import ingest_directory

    if _ingest_lock.locked():
        msg = '<p class="process-ok">Verarbeitung läuft bereits...</p>'
        return HTMLResponse(msg) if request.headers.get("hx-request") else JSONResponse({"queued": 0})

    tiffs = list(_INBOX.glob("*.tif")) + list(_INBOX.glob("*.tiff"))
    count = len(tiffs)
    if count == 0:
        msg = '<p class="process-empty">Keine neuen Dateien in der Inbox gefunden.</p>'
        return HTMLResponse(msg) if request.headers.get("hx-request") else JSONResponse({"queued": 0})

    background_tasks.add_task(_run_ingest)
    if request.headers.get("hx-request"):
        msg = (
            f'<p class="process-ok" '
            f'hx-get="/process/status" hx-trigger="every 2s" hx-swap="outerHTML">'
            f'Verarbeitung gestartet ({count} Datei(en))...</p>'
        )
        return HTMLResponse(msg)
    return JSONResponse({"queued": count})


@router.get("/process/status", response_class=HTMLResponse)
async def process_status():
    """Return current ingestion status for HTMX polling."""
    s = _ingest_status
    if s["state"] == "idle":
        return HTMLResponse("")
    if s["state"] == "running":
        return HTMLResponse(
            f'<p class="process-ok" '
            f'hx-get="/process/status" hx-trigger="every 2s" hx-swap="outerHTML">'
            f'{escape(s["message"])}</p>'
        )
    if s["state"] == "done":
        return HTMLResponse(f'<p class="process-ok">&#10003; {escape(s["message"])}</p>')
    # error (message may contain exception text)
    return HTMLResponse(f'<p class="process-empty">{escape(s["message"])}</p>')


@router.post("/geocode")
def geocode_places(request: Request):
    """Geocode all places that are missing coordinates.

    Sync handler on purpose: geocode_all_places() sleeps 1.1s per Nominatim
    request — running it in FastAPI's threadpool keeps the event loop free.
    """
    from app.worker.geocoder import geocode_all_places
    from app.db.database import get_places_without_coords

    pending = len(get_places_without_coords(_DB))
    count = geocode_all_places(_DB, retry_failed=True)
    if request.headers.get("hx-request"):
        if pending == 0:
            msg = '<p class="process-empty">Alle Orte haben bereits Koordinaten.</p>'
        elif count:
            msg = f'<p class="process-ok">✓ {count} von {pending} Ort(en) geocodiert.</p>'
        else:
            msg = f'<p class="process-empty">{pending} Ort(e) ohne Koordinaten — keine davon konnte geocodiert werden (fehlende Adresse/Stadt).</p>'
        return HTMLResponse(msg)
    return JSONResponse({"pending": pending, "geocoded": count})


@router.get("/export")
async def export(fmt: str = "csv"):
    """Export all articles as CSV or JSON."""
    articles = search_full(limit=10_000, db_path=_DB)

    if fmt == "json":
        return JSONResponse(content=articles)

    # Default: CSV
    if not articles:
        return StreamingResponse(iter([""]), media_type="text/csv")

    output = io.StringIO()
    fields = [
        "id", "filename", "scan_date", "newspaper", "article_date", "page",
        "headline", "summary", "category", "tags", "ocr_confidence",
        "needs_review", "meta_source", "created_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(articles)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=zeitungsarchiv.csv"},
    )
