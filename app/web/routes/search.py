"""Search route: GET / and GET /search."""

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.db.database import (count_search_results, get_filter_options, get_group_articles,
                             get_review_count, search_full)
from app.web.templating import templates as _templates

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))
_PAGE_SIZE = 20


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
    results = _add_display_headlines(search_full(limit=_PAGE_SIZE, db_path=_DB), _DB)
    total = count_search_results(db_path=_DB)
    return _templates.TemplateResponse(
        request,
        "index.html",
        _ctx(request, results=results, q="", newspaper="", category="",
             section="", date_from="", date_to="", location="", country="",
             sort="date_desc", offset=0, total=total,
             has_more=_PAGE_SIZE < total, next_offset=_PAGE_SIZE, **opts),
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
    country: str = "",
    sort: str = "date_desc",
    offset: int = 0,
    append: int = 0,
):
    filters = dict(
        query=q, newspaper=newspaper, category=category, section=section,
        date_from=date_from, date_to=date_to, location=location, country=country,
        db_path=_DB,
    )
    results = _add_display_headlines(
        search_full(sort=sort, limit=_PAGE_SIZE, offset=offset, **filters), _DB
    )
    total = count_search_results(**filters)
    next_offset = offset + _PAGE_SIZE
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
        country=country,
        sort=sort,
        offset=offset,
        total=total,
        has_more=next_offset < total,
        next_offset=next_offset,
        **get_filter_options(_DB),
    )
    # "Mehr laden" click: return only the new items + fresh load-more button
    if append:
        return _templates.TemplateResponse(request, "search_results_items.html", ctx)
    if request.headers.get("hx-request"):
        return _templates.TemplateResponse(request, "search_results.html", ctx)
    return _templates.TemplateResponse(request, "index.html", ctx)
