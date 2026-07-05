"""Route tests for the review workflow: save-and-next and editable OCR text."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.db.database import count_search_results, get_article, init_db, insert_article
from app.web.routes import articles


def _flagged(filename: str, headline: str) -> dict:
    return {
        "filename": filename, "scan_date": "2026-07-05", "newspaper": "Kurier",
        "article_date": "2026-06-01", "page": None, "headline": headline,
        "summary": "x", "category": "Lokales", "tags": [], "full_text": "alter text",
        "image_path": "i", "thumb_path": "t", "ocr_confidence": 50.0,
        "needs_review": 1, "meta_source": "auto",
    }


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "test.db"
    init_db(db)
    monkeypatch.setattr(articles, "_DB", db)
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(articles.router)
    return TestClient(app), db


def test_save_next_redirects_to_next_review(ctx):
    client, db = ctx
    a1 = insert_article(_flagged("a.tif", "A"), db)
    a2 = insert_article(_flagged("b.tif", "B"), db)

    r = client.post(f"/articles/{a1}", data={"headline": "A neu", "save_next": "1"},
                    follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == f"/articles/{a2}/edit"
    assert get_article(a1, db)["needs_review"] == 0


def test_save_next_falls_back_to_review_queue(ctx):
    client, db = ctx
    a1 = insert_article(_flagged("a.tif", "A"), db)

    r = client.post(f"/articles/{a1}", data={"headline": "A", "save_next": "1"},
                    follow_redirects=False)

    assert r.headers["location"] == "/review"


def test_plain_save_redirects_to_detail(ctx):
    client, db = ctx
    a1 = insert_article(_flagged("a.tif", "A"), db)

    r = client.post(f"/articles/{a1}", data={"headline": "A"}, follow_redirects=False)

    assert r.headers["location"] == f"/articles/{a1}"


def test_editing_full_text_updates_search_index(ctx):
    client, db = ctx
    a1 = insert_article(_flagged("a.tif", "A"), db)

    client.post(f"/articles/{a1}",
                data={"headline": "A", "full_text": "voellig neuer inhalt zwetschken"},
                follow_redirects=False)

    assert get_article(a1, db)["full_text"] == "voellig neuer inhalt zwetschken"
    # FTS trigger must have re-indexed the new text
    assert count_search_results(query="zwetschken", db_path=db) == 1


def test_blank_full_text_does_not_wipe_existing(ctx):
    client, db = ctx
    a1 = insert_article(_flagged("a.tif", "A"), db)

    client.post(f"/articles/{a1}", data={"headline": "A", "full_text": "   "},
                follow_redirects=False)

    assert get_article(a1, db)["full_text"] == "alter text"
