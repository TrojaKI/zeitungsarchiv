"""Admin routes: stats, manual ingestion trigger, CSV/JSON export."""

import csv
import io
import json
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.db.database import get_review_count, get_stats, search_full

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))
_INBOX = Path(os.getenv("INBOX_DIR", "/app/inbox"))
_ARCHIVE = Path(os.getenv("ARCHIVE_DIR", "/app/archive"))


@router.get("/stats", response_class=HTMLResponse)
async def stats(request: Request):
    data = get_stats(_DB)
    return _templates.TemplateResponse(
        "stats.html",
        {"request": request, "review_count": get_review_count(_DB), **data},
    )


@router.post("/process", response_class=JSONResponse)
async def process_inbox():
    """Manually trigger ingestion of all TIFFs currently in the inbox."""
    from app.worker.ingestion import ingest_directory

    ids = ingest_directory(_INBOX, _ARCHIVE, _DB)
    return {"processed": len(ids), "ids": ids}


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
