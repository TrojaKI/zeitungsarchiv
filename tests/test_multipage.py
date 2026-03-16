"""Tests for multi-page article support (_pNN convention).

Covers:
  1. test_page_part_detected          — _pNN regex matches, group + page_number extracted
  2. test_page_part_not_raw_stitch    — _is_raw_part() returns False for _pNN files
  3. test_page_part_not_grouped       — group_multipart_scans treats _pNN as standalone
  4. test_ingest_sets_article_group   — ingest() passes article_group/page_number to insert_article
  5. test_get_group_articles          — DB helper returns pages ordered by page_number
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from app.worker.ingestion import _PAGE_RE, group_multipart_scans, ingest
from app.worker.watcher import _is_raw_part


# ---------------------------------------------------------------------------
# 1. _PAGE_RE regex
# ---------------------------------------------------------------------------

class TestPageRegex:
    @pytest.mark.parametrize("stem,group,page", [
        ("sternlicht_oase_p01", "sternlicht_oase", 1),
        ("sternlicht_oase_p02", "sternlicht_oase", 2),
        ("sternlicht_oase_p10", "sternlicht_oase", 10),
        ("artikel_P03", "artikel", 3),   # case-insensitive
        ("some_deep_name_p99", "some_deep_name", 99),
    ])
    def test_matches(self, stem, group, page):
        m = _PAGE_RE.match(stem)
        assert m is not None, f"Expected match for {stem!r}"
        assert m.group(1) == group
        assert int(m.group(2)) == page

    @pytest.mark.parametrize("stem", [
        "sternlicht_oase_01",   # stitch part, not page part
        "sternlicht_oase_00",   # stitched output
        "artikel",              # no suffix
        "artikel_p",            # missing digits
    ])
    def test_no_match(self, stem):
        assert _PAGE_RE.match(stem) is None, f"Expected no match for {stem!r}"


# ---------------------------------------------------------------------------
# 2. _is_raw_part does NOT match _pNN files
# ---------------------------------------------------------------------------

class TestIsRawPartIgnoresPNN:
    @pytest.mark.parametrize("name", [
        "sternlicht_oase_p01.tif",
        "sternlicht_oase_p02.tif",
        "artikel_p10.tiff",
    ])
    def test_pnn_not_raw_part(self, name):
        assert _is_raw_part(Path(name)) is False

    @pytest.mark.parametrize("name", [
        "artikel_01.tif",
        "artikel_02.tiff",
    ])
    def test_nn_is_raw_part(self, name):
        assert _is_raw_part(Path(name)) is True


# ---------------------------------------------------------------------------
# 3. group_multipart_scans treats _pNN files as standalone
# ---------------------------------------------------------------------------

class TestGroupMultipartIgnoresPNN:
    def test_pnn_files_are_standalone(self, tmp_path):
        tiffs = []
        for name in ["article_p01.tif", "article_p02.tif", "article_p03.tif"]:
            p = tmp_path / name
            p.touch()
            tiffs.append(p)

        standalone, groups = group_multipart_scans(tiffs)

        assert groups == [], "_pNN files must not be scheduled for stitching"
        assert sorted(p.name for p in standalone) == [
            "article_p01.tif", "article_p02.tif", "article_p03.tif"
        ]

    def test_pnn_and_normal_standalone_together(self, tmp_path):
        """Mix of _pNN and plain files — all treated as standalone."""
        tiffs = []
        for name in ["report.tif", "report_p01.tif", "report_p02.tif"]:
            p = tmp_path / name
            p.touch()
            tiffs.append(p)

        standalone, groups = group_multipart_scans(tiffs)

        assert groups == []
        assert len(standalone) == 3


# ---------------------------------------------------------------------------
# 4. ingest() sets article_group and page_number for _pNN files
# ---------------------------------------------------------------------------

class TestIngestSetsArticleGroup:
    def _run_ingest(self, tmp_path, filename):
        """Run ingest() with all side-effectful functions mocked out."""
        tiff = tmp_path / filename
        tiff.touch()
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        db = tmp_path / "archive.db"

        fake_ocr = {
            "full_text": "Testtext",
            "image_path": "img.webp",
            "thumb_path": "thumb.jpg",
            "ocr_confidence": 85.0,
            "needs_review": False,
            "meta_source": "auto",
        }
        fake_meta = {
            "newspaper": "Testzeitschrift",
            "article_date": "2024-01-01",
            "headline": "Testschlagzeile",
            "summary": "Zusammenfassung.",
            "category": "Test",
            "tags": ["tag1"],
            "page": "1",
        }

        captured = {}

        def fake_insert(article, db_path):
            captured.update(article)
            return 42

        with (
            patch("app.worker.ingestion.process_scan", return_value=fake_ocr),
            patch("app.worker.ingestion.extract_metadata", return_value=fake_meta),
            patch("app.worker.ingestion.extract_places", return_value=[]),
            patch("app.worker.ingestion.insert_article", side_effect=fake_insert),
            patch("app.worker.ingestion.insert_places"),
            patch("app.worker.ingestion.shutil.move"),
        ):
            result = ingest(tiff, archive_dir, db)

        return result, captured

    def test_pnn_sets_group_and_page(self, tmp_path):
        result, captured = self._run_ingest(tmp_path, "sternlicht_oase_p02.tif")
        assert result == 42
        assert captured["article_group"] == "sternlicht_oase"
        assert captured["page_number"] == 2

    def test_plain_file_has_no_group(self, tmp_path):
        result, captured = self._run_ingest(tmp_path, "artikel.tif")
        assert result == 42
        assert captured["article_group"] is None
        assert captured["page_number"] is None

    def test_stitch_output_has_no_group(self, tmp_path):
        """Stitched panorama files (_00) are not multi-page articles."""
        result, captured = self._run_ingest(tmp_path, "panorama_00.tif")
        assert captured["article_group"] is None
        assert captured["page_number"] is None


# ---------------------------------------------------------------------------
# 5. get_group_articles DB helper
# ---------------------------------------------------------------------------

class TestGetGroupArticles:
    def test_returns_pages_in_order(self, tmp_path):
        from app.db.database import get_group_articles, init_db, insert_article

        db = tmp_path / "test.db"
        init_db(db)

        base = {
            "filename": "x.tif",
            "scan_date": "2024-01-01",
            "newspaper": "TestZeitung",
            "article_date": "2024-01-01",
            "page": None,
            "headline": "Headline",
            "summary": "Summary",
            "category": "Test",
            "tags": [],
            "full_text": "text",
            "image_path": "img.webp",
            "thumb_path": "thumb.jpg",
            "ocr_confidence": 90.0,
            "needs_review": 0,
            "meta_source": "auto",
        }

        # Insert pages out of order to verify ORDER BY
        insert_article({**base, "filename": "g_p03.tif", "article_group": "g", "page_number": 3}, db)
        insert_article({**base, "filename": "g_p01.tif", "article_group": "g", "page_number": 1}, db)
        insert_article({**base, "filename": "g_p02.tif", "article_group": "g", "page_number": 2}, db)
        # Unrelated article
        insert_article({**base, "filename": "other.tif", "article_group": None, "page_number": None}, db)

        pages = get_group_articles("g", db)

        assert len(pages) == 3
        assert [p["page_number"] for p in pages] == [1, 2, 3]
        assert [p["filename"] for p in pages] == ["g_p01.tif", "g_p02.tif", "g_p03.tif"]

    def test_empty_for_unknown_group(self, tmp_path):
        from app.db.database import get_group_articles, init_db

        db = tmp_path / "test.db"
        init_db(db)
        assert get_group_articles("nonexistent", db) == []
