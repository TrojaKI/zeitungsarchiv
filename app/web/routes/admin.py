"""Admin routes: stats, manual ingestion trigger, CSV/JSON export."""

import csv
import io
import json
import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from app.db.database import get_places_without_coords, get_review_count, get_stats, search_full
from app.web.templating import templates as _templates

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))
_INBOX = Path(os.getenv("INBOX_DIR", "/app/inbox"))
_ARCHIVE = Path(os.getenv("ARCHIVE_DIR", "/app/archive"))


@router.get("/stats", response_class=HTMLResponse)
async def stats(request: Request):
    data = get_stats(_DB)
    ungeocodiert = get_places_without_coords(_DB)
    return _templates.TemplateResponse(
        "stats.html",
        {"request": request, "review_count": get_review_count(_DB),
         "ungeocodiert": ungeocodiert, **data},
    )


def _run_ingest():
    """Background task: ingest all TIFFs in inbox."""
    from app.worker.ingestion import ingest_directory
    ingest_directory(_INBOX, _ARCHIVE, _DB)


@router.post("/process")
async def process_inbox(request: Request, background_tasks: BackgroundTasks):
    """Queue ingestion of all TIFFs in inbox and return immediately."""
    from app.worker.ingestion import ingest_directory

    tiffs = list(_INBOX.glob("*.tif")) + list(_INBOX.glob("*.tiff"))
    count = len(tiffs)
    if count == 0:
        msg = '<p class="process-empty">Keine neuen Dateien in der Inbox gefunden.</p>'
        return HTMLResponse(msg) if request.headers.get("hx-request") else JSONResponse({"queued": 0})

    background_tasks.add_task(_run_ingest)
    if request.headers.get("hx-request"):
        msg = f'<p class="process-ok">✓ {count} Datei(en) werden im Hintergrund verarbeitet…</p>'
        return HTMLResponse(msg)
    return JSONResponse({"queued": count})


@router.post("/geocode")
async def geocode_places(request: Request):
    """Geocode all places that are missing coordinates."""
    from app.worker.geocoder import geocode_all_places
    from app.db.database import get_places_without_coords

    pending = len(get_places_without_coords(_DB))
    count = geocode_all_places(_DB)
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
