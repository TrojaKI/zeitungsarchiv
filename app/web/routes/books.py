"""Books routes: list all books, edit/delete individual book recommendation."""

import os
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db.database import delete_book, get_all_books, get_review_count, update_book
from app.web.templating import templates as _templates

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


def _ctx(request: Request, **kwargs) -> dict:
    return {"request": request, "review_count": get_review_count(_DB), **kwargs}


@router.get("/books", response_class=HTMLResponse)
async def books_list(request: Request, q: str = "", sort: str = "author_asc"):
    books = get_all_books(query=q, sort=sort, db_path=_DB)
    ctx = _ctx(request, books=books, q=q, sort=sort)
    if request.headers.get("hx-request"):
        return _templates.TemplateResponse("books_results.html", ctx)
    return _templates.TemplateResponse("books.html", ctx)


@router.post("/books/{book_id}")
async def book_update(
    book_id: int,
    article_id: int = Form(...),
    title: str = Form(""),
    author: str = Form(""),
    publisher: str = Form(""),
    year: str = Form(""),
    pages: str = Form(""),
    price: str = Form(""),
    isbn: str = Form(""),
    description: str = Form(""),
    url: str = Form(""),
):
    update_book(book_id, {
        "title":       title or None,
        "author":      author or None,
        "publisher":   publisher or None,
        "year":        year or None,
        "pages":       pages or None,
        "price":       price or None,
        "isbn":        isbn or None,
        "description": description or None,
        "url":         url or None,
    }, _DB)
    return RedirectResponse(f"/articles/{article_id}/edit", status_code=303)


@router.post("/books/{book_id}/delete")
async def book_delete(book_id: int, article_id: int = Form(...)):
    delete_book(book_id, _DB)
    return RedirectResponse(f"/articles/{article_id}/edit", status_code=303)
