"""Smoke tests for web routes — verify all GET endpoints return HTTP 200.

Regression test for Starlette 0.36+ API change: TemplateResponse signature
changed from (name, context) to (request, name, context). A wrong call order
causes either:
  - HTTP 500 "unhashable type: 'dict'" (strict Starlette versions, e.g. in Docker)
  - DeprecationWarning + silent wrong behavior (backwards-compat Starlette versions)

Both cases are caught here: filterwarnings turns Starlette DeprecationWarnings
into errors so the test fails even when the local Starlette is still lenient.
"""

import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.db.database import init_db
from app.web.routes import admin, articles, books, places, recipes, review, search


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Minimal SQLite DB with schema applied."""
    db = tmp_path / "test.db"
    init_db(db)
    return db


@pytest.fixture
def client(test_db: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with all route _DB vars pointing to the test DB.

    Uses a fresh FastAPI app without StaticFiles mounts so no archive/static
    directories need to exist on disk.
    """
    for mod in (search, articles, places, books, recipes, review, admin):
        monkeypatch.setattr(mod, "_DB", test_db)

    test_app = FastAPI()
    test_app.include_router(search.router)
    test_app.include_router(articles.router)
    test_app.include_router(places.router)
    test_app.include_router(books.router)
    test_app.include_router(recipes.router)
    test_app.include_router(review.router)
    test_app.include_router(admin.router)

    return TestClient(test_app, raise_server_exceptions=True)


@contextmanager
def strict_template_response() -> Generator[None, None, None]:
    """Treat Starlette TemplateResponse deprecation warnings as errors.

    Starlette 0.36+ deprecated the old (name, context) signature in favour of
    (request, name, context).  Some Starlette versions only warn; others raise
    a TypeError. This context manager makes both cases fail the test.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("error", category=DeprecationWarning, module="starlette")
        yield


class TestRoutesSmokeTest:
    """Every GET route that renders a template must return HTTP 200.

    All requests are wrapped in strict_template_response() so that any
    TemplateResponse call with the old (name, context) signature fails the test,
    regardless of whether the current Starlette version raises or only warns.
    """

    def test_index(self, client: TestClient) -> None:
        with strict_template_response():
            assert client.get("/").status_code == 200

    def test_search_empty(self, client: TestClient) -> None:
        with strict_template_response():
            assert client.get("/search").status_code == 200

    def test_search_with_query(self, client: TestClient) -> None:
        with strict_template_response():
            assert client.get("/search?q=test").status_code == 200

    def test_search_htmx_returns_partial(self, client: TestClient) -> None:
        with strict_template_response():
            response = client.get("/search", headers={"hx-request": "true"})
        assert response.status_code == 200

    def test_places(self, client: TestClient) -> None:
        with strict_template_response():
            assert client.get("/places").status_code == 200

    def test_places_htmx_returns_partial(self, client: TestClient) -> None:
        with strict_template_response():
            response = client.get("/places", headers={"hx-request": "true"})
        assert response.status_code == 200

    def test_books(self, client: TestClient) -> None:
        with strict_template_response():
            assert client.get("/books").status_code == 200

    def test_books_htmx_returns_partial(self, client: TestClient) -> None:
        with strict_template_response():
            response = client.get("/books", headers={"hx-request": "true"})
        assert response.status_code == 200

    def test_recipes(self, client: TestClient) -> None:
        with strict_template_response():
            assert client.get("/recipes").status_code == 200

    def test_recipes_htmx_returns_partial(self, client: TestClient) -> None:
        with strict_template_response():
            response = client.get("/recipes", headers={"hx-request": "true"})
        assert response.status_code == 200

    def test_review_queue(self, client: TestClient) -> None:
        with strict_template_response():
            assert client.get("/review").status_code == 200

    def test_stats(self, client: TestClient) -> None:
        with strict_template_response():
            assert client.get("/stats").status_code == 200

    def test_article_detail_not_found(self, client: TestClient) -> None:
        """Non-existent article returns 404, not 500."""
        assert client.get("/articles/999").status_code == 404

    def test_article_edit_not_found(self, client: TestClient) -> None:
        """Non-existent article edit returns 404, not 500."""
        assert client.get("/articles/999/edit").status_code == 404
