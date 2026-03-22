"""Search route: GET / and GET /search."""

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.db.database import get_filter_options, get_group_articles, get_review_count, search_full
from app.web.templating import templates as _templates

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


def _add_display_headlines(results: list[dict], db_path: Path) -> list[dict]:
    """Add display_headline field: sub-pages get '{page1 headline} - Seite N'."""
    groups = {r["article_group"] for r in results
              if r.get("article_group") and (r.get("page_number") or 0) > 1}
    group_headline: dict[str, str] = {}
    for group in groups:
        pages = get_group_articles(group, db_path)
        page1 = next((p for p in pages if p.get("page_number") == 1), None) or (pages[0] if pages else None)
        if page1:
            group_headline[group] = page1.get("headline") or ""
    for r in results:
        if r.get("article_group") and (r.get("page_number") or 0) > 1:
            base = group_headline.get(r["article_group"]) or r.get("headline") or ""
            r["display_headline"] = f"{base} - Seite {r['page_number']}" if base else r.get("headline")
        else:
            r["display_headline"] = r.get("headline")
    return results


def _ctx(request: Request, **kwargs) -> dict:
    """Build a base template context with review badge count."""
    return {"request": request, "review_count": get_review_count(_DB), **kwargs}


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    opts = get_filter_options(_DB)
    results = _add_display_headlines(search_full(limit=20, db_path=_DB), _DB)
    return _templates.TemplateResponse(
        "index.html",
        _ctx(request, results=results, q="", newspaper="", category="",
             section="", date_from="", date_to="", location="", sort="date_desc", **opts),
    )


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = "",
    newspaper: str = "",
    category: str = "",
    section: str = "",
    date_from: str = "",
    date_to: str = "",
    location: str = "",
    sort: str = "date_desc",
    offset: int = 0,
):
    results = _add_display_headlines(search_full(
        query=q,
        newspaper=newspaper,
        category=category,
        section=section,
        date_from=date_from,
        date_to=date_to,
        location=location,
        sort=sort,
        limit=20,
        offset=offset,
        db_path=_DB,
    ), _DB)
    opts = get_filter_options(_DB)
    ctx = _ctx(
        request,
        results=results,
        q=q,
        newspaper=newspaper,
        category=category,
        section=section,
        date_from=date_from,
        date_to=date_to,
        location=location,
        sort=sort,
        offset=offset,
        **opts,
    )
    if request.headers.get("hx-request"):
        return _templates.TemplateResponse("search_results.html", ctx)
    return _templates.TemplateResponse("index.html", ctx)
