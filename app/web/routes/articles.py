"""Article detail and edit routes."""

import json
import os
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.db.database import (get_article, get_books, get_group_articles, get_places,
                              get_recipes, get_review_count, update_article)
from app.web.templating import templates as _templates

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


def _ctx(request: Request, **kwargs) -> dict:
    return {"request": request, "review_count": get_review_count(_DB), **kwargs}


@router.get("/articles/group/{group}", response_class=HTMLResponse)
async def article_group_view(group: str, request: Request):
    """Redirect to the first page of a multi-page article group."""
    pages = get_group_articles(group, _DB)
    if not pages:
        return HTMLResponse("Article group not found", status_code=404)
    return RedirectResponse(f"/articles/{pages[0]['id']}", status_code=302)


@router.get("/articles/{article_id}", response_class=HTMLResponse)
async def article_detail(request: Request, article_id: int):
    article = get_article(article_id, _DB)
    if article is None:
        return HTMLResponse("Article not found", status_code=404)
    # Deserialize tags JSON string to list for template rendering
    if isinstance(article.get("tags"), str):
        try:
            article["tags"] = json.loads(article["tags"])
        except (json.JSONDecodeError, TypeError):
            article["tags"] = []
    places  = get_places(article_id, _DB)
    books   = get_books(article_id, _DB)
    recipes = get_recipes(article_id, _DB)
    # Fetch sibling pages for multi-page articles
    group_pages = (
        get_group_articles(article["article_group"], _DB)
        if article.get("article_group")
        else []
    )
    return _templates.TemplateResponse(
        "article.html",
        _ctx(request, article=article, places=places, books=books,
             recipes=recipes, group_pages=group_pages),
    )


@router.get("/articles/{article_id}/edit", response_class=HTMLResponse)
async def article_edit(request: Request, article_id: int):
    article = get_article(article_id, _DB)
    if article is None:
        return HTMLResponse("Article not found", status_code=404)
    if isinstance(article.get("tags"), str):
        try:
            article["tags"] = json.loads(article["tags"])
        except (json.JSONDecodeError, TypeError):
            article["tags"] = []
    places  = get_places(article_id, _DB)
    books   = get_books(article_id, _DB)
    recipes = get_recipes(article_id, _DB)
    return _templates.TemplateResponse(
        "edit.html",
        _ctx(request, article=article, places=places, books=books, recipes=recipes),
    )


@router.post("/articles/{article_id}")
async def article_update(
    article_id: int,
    newspaper: str = Form(""),
    article_date: str = Form(""),
    page: str = Form(""),
    headline: str = Form(""),
    summary: str = Form(""),
    category: str = Form(""),
    tags: str = Form(""),       # comma-separated
    locations: str = Form(""),  # comma-separated
    urls: str = Form(""),       # comma-separated
):
    def split_csv(s: str) -> list[str]:
        return [x.strip() for x in s.split(",") if x.strip()]

    update_article(
        article_id,
        {
            "newspaper": newspaper or None,
            "article_date": article_date or None,
            "page": page or None,
            "headline": headline,
            "summary": summary,
            "category": category,
            "tags": split_csv(tags),
            "locations": split_csv(locations),
            "urls": split_csv(urls),
            "meta_source": "manual",
            "needs_review": 0,
        },
    )
    return RedirectResponse(f"/articles/{article_id}", status_code=303)
