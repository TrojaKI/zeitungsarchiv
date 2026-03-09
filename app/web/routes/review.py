"""Review queue route: articles flagged for manual review."""

import json
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.db.database import get_review_count, search_full
from app.web.templating import templates as _templates

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


@router.get("/review", response_class=HTMLResponse)
async def review_queue(request: Request):
    articles = search_full(needs_review=True, limit=50, db_path=_DB)
    # Deserialize tags for each article
    for a in articles:
        if isinstance(a.get("tags"), str):
            try:
                a["tags"] = json.loads(a["tags"])
            except (json.JSONDecodeError, TypeError):
                a["tags"] = []
    return _templates.TemplateResponse(
        "review.html",
        {
            "request": request,
            "articles": articles,
            "review_count": get_review_count(_DB),
        },
    )
