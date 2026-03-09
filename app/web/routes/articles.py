"""Article detail and edit routes."""

import json
import os
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.database import get_article, get_review_count, update_article

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


def _ctx(request: Request, **kwargs) -> dict:
    return {"request": request, "review_count": get_review_count(_DB), **kwargs}


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
    return _templates.TemplateResponse("article.html", _ctx(request, article=article))


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
    return _templates.TemplateResponse("edit.html", _ctx(request, article=article))


@router.post("/articles/{article_id}")
async def article_update(
    article_id: int,
    newspaper: str = Form(""),
    article_date: str = Form(""),
    page: str = Form(""),
    headline: str = Form(""),
    summary: str = Form(""),
    category: str = Form(""),
    tags: str = Form(""),  # comma-separated
):
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    update_article(
        article_id,
        {
            "newspaper": newspaper or None,
            "article_date": article_date or None,
            "page": page or None,
            "headline": headline,
            "summary": summary,
            "category": category,
            "tags": tag_list,
            "meta_source": "manual",
            "needs_review": 0,
        },
    )
    return RedirectResponse(f"/articles/{article_id}", status_code=303)
