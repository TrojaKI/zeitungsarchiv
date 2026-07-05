"""Route tests for the search UI: result count and load-more pagination."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.db.database import init_db, insert_article
from app.web.routes import search


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db = tmp_path / "test.db"
    init_db(db)
    # 25 articles → two pages at PAGE_SIZE 20
    for i in range(25):
        insert_article({
            "filename": f"s{i}.tif", "scan_date": "2026-07-05", "newspaper": "Kurier",
            "article_date": f"2026-06-{(i % 28) + 1:02d}", "page": None,
            "headline": f"Artikel {i}", "summary": "x", "category": "Lokales",
            "tags": [], "full_text": f"Meldung nummer {i} aus Wien",
            "image_path": f"s{i}/i.webp", "thumb_path": f"s{i}/t.jpg",
            "ocr_confidence": 90.0, "needs_review": 0, "meta_source": "auto",
        }, db)
    monkeypatch.setattr(search, "_DB", db)
    app = FastAPI()
    app.include_router(search.router)
    return TestClient(app)


def test_index_shows_result_count_and_load_more(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "25 Artikel gefunden" in r.text
    assert "Mehr laden" in r.text
    # first page renders exactly PAGE_SIZE cards
    assert r.text.count("result-card") == 20


def test_append_returns_only_items(client: TestClient):
    r = client.get("/search?offset=20&append=1")
    assert r.status_code == 200
    # append fragment must not contain the list wrapper or the search form
    assert "<ul" not in r.text
    assert "search-box" not in r.text
    # remaining 5 cards, and no further load-more button on the last page
    assert r.text.count("result-card") == 5
    assert "Mehr laden" not in r.text


def test_load_more_button_absent_when_all_fit(client: TestClient):
    # A narrow query returning a single article must not offer "load more"
    r = client.get("/search?q=nummer+7")
    assert r.status_code == 200
    assert "1 Artikel gefunden" in r.text
    assert "Mehr laden" not in r.text
