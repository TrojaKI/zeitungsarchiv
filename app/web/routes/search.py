"""Search route: GET / and GET /search."""

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.db.database import get_filter_options, get_review_count, search_full
from app.web.templating import templates as _templates

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


def _ctx(request: Request, **kwargs) -> dict:
    """Build a base template context with review badge count."""
    return {"request": request, "review_count": get_review_count(_DB), **kwargs}


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    opts = get_filter_options(_DB)
    # Pre-load all articles so the list is visible without requiring a search
    results = search_full(limit=20, db_path=_DB)
    return _templates.TemplateResponse(
        "index.html",
        _ctx(request, results=results, q="", newspaper="", category="",
             date_from="", date_to="", **opts),
    )


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = "",
    newspaper: str = "",
    category: str = "",
    date_from: str = "",
    date_to: str = "",
    offset: int = 0,
):
    results = search_full(
        query=q,
        newspaper=newspaper,
        category=category,
        date_from=date_from,
        date_to=date_to,
        limit=20,
        offset=offset,
        db_path=_DB,
    )
    opts = get_filter_options(_DB)
    ctx = _ctx(
        request,
        results=results,
        q=q,
        newspaper=newspaper,
        category=category,
        date_from=date_from,
        date_to=date_to,
        offset=offset,
        **opts,
    )
    # HTMX partial request: return only the results fragment
    if request.headers.get("hx-request"):
        return _templates.TemplateResponse("search_results.html", ctx)
    return _templates.TemplateResponse("index.html", ctx)
