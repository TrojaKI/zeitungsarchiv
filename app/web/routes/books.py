"""Books routes: edit/delete individual book recommendation."""

import os
from pathlib import Path

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from app.db.database import delete_book, update_book

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


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
    }, _DB)
    return RedirectResponse(f"/articles/{article_id}/edit", status_code=303)


@router.post("/books/{book_id}/delete")
async def book_delete(book_id: int, article_id: int = Form(...)):
    delete_book(book_id, _DB)
    return RedirectResponse(f"/articles/{article_id}/edit", status_code=303)
