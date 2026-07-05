"""Tests for the database layer: FTS sanitizing, place updates, geocode markers."""

from pathlib import Path

import pytest

from app.db import database as db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


def _article(**overrides) -> dict:
    article = {
        "filename": "scan001.tif",
        "scan_date": "2026-07-05",
        "newspaper": "Kurier",
        "article_date": "2026-07-01",
        "page": "3",
        "headline": "Ein Heuriger in Wien",
        "summary": "Test",
        "category": "Lokales",
        "tags": ["wien"],
        "full_text": "Ein Heuriger in Wien mit Blick auf die Wachau.",
        "image_path": "scan001/image.webp",
        "thumb_path": "scan001/thumb.jpg",
        "ocr_confidence": 90.0,
        "needs_review": 0,
        "meta_source": "auto",
    }
    article.update(overrides)
    return article


class TestFtsSanitize:
    """User input with FTS5 operators must never raise OperationalError."""

    @pytest.mark.parametrize("query", [
        "(wien", "wien AND", "haus*land-", '"unbalanced', "***", "NEAR(", "wien OR",
    ])
    def test_search_full_survives_fts_special_chars(self, db_path: Path, query: str):
        db.insert_article(_article(), db_path)
        results = db.search_full(query=query, db_path=db_path)
        assert isinstance(results, list)

    def test_search_full_finds_plain_term(self, db_path: Path):
        db.insert_article(_article(), db_path)
        results = db.search_full(query="Heuriger", db_path=db_path)
        assert len(results) == 1

    def test_search_full_preserves_prefix_operator(self, db_path: Path):
        db.insert_article(_article(), db_path)
        results = db.search_full(query="Heurig*", db_path=db_path)
        assert len(results) == 1


class TestGeocodeSourcePersistence:
    """geocode_source must survive the field whitelists (was silently dropped)."""

    def test_update_place_persists_geocode_source(self, db_path: Path):
        article_id = db.insert_article(_article(), db_path)
        db.insert_places(article_id, [{"name": "Gasthaus Test", "city": "Wien"}], db_path)
        pa_id = db.get_places(article_id, db_path)[0]["id"]

        db.update_place(pa_id, {"lat": 48.2, "lng": 16.3, "geocode_source": "manual"}, db_path)

        place = db.get_place(pa_id, db_path)
        assert place["geocode_source"] == "manual"

    def test_update_manual_place_persists_geocode_source(self, db_path: Path):
        place_id = db.insert_manual_place({"name": "Café Test", "city": "Wien"}, db_path)

        db.update_manual_place(
            place_id, {"lat": 48.2, "lng": 16.3, "geocode_source": "manual"}, db_path
        )

        place = db.get_manual_place(place_id, db_path)
        assert place["geocode_source"] == "manual"


class TestUpdateManualPlaceKeys:
    """Partial updates must not wipe dedup keys or the NOT NULL name."""

    def test_city_only_update_keeps_name_key(self, db_path: Path):
        place_id = db.insert_manual_place({"name": "Café Prückel", "city": "Wien"}, db_path)

        db.update_manual_place(place_id, {"city": "Linz"}, db_path)

        place = db.get_manual_place(place_id, db_path)
        assert place["name_key"] == "café prückel"
        assert place["city_key"] == "linz"

    def test_empty_name_does_not_overwrite_existing(self, db_path: Path):
        place_id = db.insert_manual_place({"name": "Bestand", "city": "Wien"}, db_path)

        db.update_manual_place(place_id, {"name": None, "city": "Graz"}, db_path)

        place = db.get_manual_place(place_id, db_path)
        assert place["name"] == "Bestand"
        assert place["city"] == "Graz"

    def test_insert_manual_place_normalizes_apostrophes(self, db_path: Path):
        # Curly apostrophe (U+2019) must map to ASCII "'" like article places do
        place_id = db.insert_manual_place({"name": "L’Osteria", "city": "Wien"}, db_path)
        assert db.get_manual_place(place_id, db_path)["name_key"] == "l'osteria"


class TestGeocodeFailedMarker:
    """Places marked 'failed' are excluded from automatic retries only."""

    def _place_without_coords(self, db_path: Path) -> int:
        article_id = db.insert_article(_article(), db_path)
        db.insert_places(article_id, [{"name": "Unbekanntes Wirtshaus"}], db_path)
        return db.get_places(article_id, db_path)[0]["place_id"]

    def test_failed_place_excluded_from_auto_retry(self, db_path: Path):
        place_id = self._place_without_coords(db_path)

        db.mark_geocode_failed(place_id, db_path)

        assert db.get_places_without_coords(db_path, include_failed=False) == []

    def test_failed_place_still_listed_for_manual_retry(self, db_path: Path):
        place_id = self._place_without_coords(db_path)

        db.mark_geocode_failed(place_id, db_path)

        pending = db.get_places_without_coords(db_path, include_failed=True)
        assert [p["id"] for p in pending] == [place_id]

    def test_successful_geocode_clears_failed_marker(self, db_path: Path):
        place_id = self._place_without_coords(db_path)
        db.mark_geocode_failed(place_id, db_path)

        db.update_place_coords(place_id, 48.2, 16.3, source="nominatim", db_path=db_path)

        assert db.get_places_without_coords(db_path, include_failed=False) == []
        pending = db.get_places_without_coords(db_path, include_failed=True)
        assert pending == []
