"""Tests for the Hugin stitching pipeline and multi-part scan routing.

Verification scenarios from the plan:
  1. _01 + _02 (no _00) → stitch → _00 imported, parts moved to archive
  2. _00 already present with _01/_02 → only _00 imported, no stitching
  3. _00 alone → imported normally
  4. no suffix pattern → imported normally
  5. Watcher: _01 dropped → no import, log message emitted
"""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from app.worker.ingestion import group_multipart_scans, ingest_directory
from app.worker.watcher import _TiffHandler, _is_raw_part


# ---------------------------------------------------------------------------
# group_multipart_scans — pure logic, no I/O
# ---------------------------------------------------------------------------


def _paths(tmp_path: Path, names: list[str]) -> list[Path]:
    """Create empty stub files and return their paths."""
    paths = []
    for name in names:
        p = tmp_path / name
        p.touch()
        paths.append(p)
    return paths


class TestGroupMultipartScans:
    def test_parts_only_scheduled_for_stitching(self, tmp_path):
        tiffs = _paths(tmp_path, ["test_01.tif", "test_02.tif"])
        standalone, groups = group_multipart_scans(tiffs)
        assert standalone == []
        assert len(groups) == 1
        parts, output = groups[0]
        assert sorted(p.name for p in parts) == ["test_01.tif", "test_02.tif"]
        assert output.name == "test_00.tif"

    def test_existing_00_bypasses_stitching(self, tmp_path):
        tiffs = _paths(tmp_path, ["test_00.tif", "test_01.tif", "test_02.tif"])
        standalone, groups = group_multipart_scans(tiffs)
        assert groups == []
        assert len(standalone) == 1
        assert standalone[0].name == "test_00.tif"

    def test_00_alone_is_standalone(self, tmp_path):
        tiffs = _paths(tmp_path, ["test_00.tif"])
        standalone, groups = group_multipart_scans(tiffs)
        assert groups == []
        assert [p.name for p in standalone] == ["test_00.tif"]

    def test_no_suffix_pattern_is_standalone(self, tmp_path):
        tiffs = _paths(tmp_path, ["artikel.tif", "bericht.tiff"])
        standalone, groups = group_multipart_scans(tiffs)
        assert groups == []
        assert sorted(p.name for p in standalone) == ["artikel.tif", "bericht.tiff"]

    def test_multiple_groups_independent(self, tmp_path):
        tiffs = _paths(
            tmp_path,
            ["alpha_01.tif", "alpha_02.tif", "beta_01.tif", "beta_02.tif"],
        )
        standalone, groups = group_multipart_scans(tiffs)
        assert standalone == []
        assert len(groups) == 2
        outputs = sorted(g[1].name for g in groups)
        assert outputs == ["alpha_00.tif", "beta_00.tif"]


# ---------------------------------------------------------------------------
# ingest_directory — mocks stitch_multipart, ingest, init_db
# ---------------------------------------------------------------------------


@pytest.fixture()
def dirs(tmp_path):
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    inbox.mkdir()
    archive.mkdir()
    db = tmp_path / "archive.db"
    return inbox, archive, db


class TestIngestDirectory:
    """Verification test 1: _01 + _02 only → stitch → one article, parts archived."""

    def test_parts_only_stitched_and_imported(self, dirs):
        inbox, archive, db = dirs

        # Create raw part files in inbox
        part1 = inbox / "test_01.tif"
        part2 = inbox / "test_02.tif"
        part1.touch()
        part2.touch()

        def fake_stitch(parts, output):
            # Simulate Hugin writing the merged file
            output.touch()
            return output

        with (
            patch("app.worker.ingestion.init_db"),
            patch("app.worker.ingestion.stitch_multipart", side_effect=fake_stitch) as mock_stitch,
            patch("app.worker.ingestion.ingest", return_value=42) as mock_ingest,
        ):
            ids = ingest_directory(inbox, archive, db)

        # One article imported
        assert ids == [42]

        # stitch_multipart called with both parts and _00 output path
        assert mock_stitch.call_count == 1
        call_parts, call_output = mock_stitch.call_args[0]
        assert sorted(p.name for p in call_parts) == ["test_01.tif", "test_02.tif"]
        assert call_output.name == "test_00.tif"

        # ingest called only for _00
        assert mock_ingest.call_count == 1
        assert mock_ingest.call_args[0][0].name == "test_00.tif"

        # Parts moved to archive/<stem>/parts/
        parts_dir = archive / "test_00" / "parts"
        assert (parts_dir / "test_01.tif").exists()
        assert (parts_dir / "test_02.tif").exists()

        # Parts no longer in inbox
        assert not part1.exists()
        assert not part2.exists()

    """Verification test 2: _00 already present → no stitching, only _00 imported."""

    def test_existing_00_skips_stitching(self, dirs):
        inbox, archive, db = dirs

        (inbox / "test_00.tif").touch()
        (inbox / "test_01.tif").touch()
        (inbox / "test_02.tif").touch()

        with (
            patch("app.worker.ingestion.init_db"),
            patch("app.worker.ingestion.stitch_multipart") as mock_stitch,
            patch("app.worker.ingestion.ingest", return_value=7) as mock_ingest,
        ):
            ids = ingest_directory(inbox, archive, db)

        assert ids == [7]
        mock_stitch.assert_not_called()
        assert mock_ingest.call_count == 1
        assert mock_ingest.call_args[0][0].name == "test_00.tif"

    def test_standalone_tiff_imported_directly(self, dirs):
        inbox, archive, db = dirs
        (inbox / "artikel.tif").touch()

        with (
            patch("app.worker.ingestion.init_db"),
            patch("app.worker.ingestion.stitch_multipart") as mock_stitch,
            patch("app.worker.ingestion.ingest", return_value=1) as mock_ingest,
        ):
            ids = ingest_directory(inbox, archive, db)

        assert ids == [1]
        mock_stitch.assert_not_called()
        assert mock_ingest.call_args[0][0].name == "artikel.tif"

    def test_stitching_failure_leaves_parts_in_inbox(self, dirs):
        inbox, archive, db = dirs
        part1 = inbox / "test_01.tif"
        part2 = inbox / "test_02.tif"
        part1.touch()
        part2.touch()

        with (
            patch("app.worker.ingestion.init_db"),
            patch("app.worker.ingestion.stitch_multipart", side_effect=RuntimeError("Hugin failed")),
            patch("app.worker.ingestion.ingest") as mock_ingest,
        ):
            ids = ingest_directory(inbox, archive, db)

        assert ids == []
        mock_ingest.assert_not_called()
        # Parts remain in inbox
        assert part1.exists()
        assert part2.exists()


# ---------------------------------------------------------------------------
# _is_raw_part — pure logic
# ---------------------------------------------------------------------------


class TestIsRawPart:
    @pytest.mark.parametrize("name,expected", [
        ("test_01.tif", True),
        ("test_02.tif", True),
        ("artikel_99.tif", True),
        ("test_00.tif", False),   # _00 is the merged file, not a raw part
        ("artikel.tif", False),   # no pattern
        ("article_x.tif", False), # non-numeric suffix
    ])
    def test_classification(self, tmp_path, name, expected):
        assert _is_raw_part(Path(name)) is expected


# ---------------------------------------------------------------------------
# Watcher: _01 dropped → ingest NOT called, log message emitted
# ---------------------------------------------------------------------------


class TestWatcherSkipsRawParts:
    """Verification test 5: watcher ignores _01/_02 files."""

    def test_raw_part_not_ingested(self, tmp_path, caplog):
        archive = tmp_path / "archive"
        db = tmp_path / "archive.db"
        handler = _TiffHandler(archive, db)

        raw_part = tmp_path / "artikel_01.tif"
        raw_part.touch()

        event = MagicMock()
        event.is_directory = False
        event.src_path = str(raw_part)

        with (
            patch("app.worker.watcher.ingest") as mock_ingest,
            caplog.at_level("INFO", logger="app.worker.watcher"),
        ):
            handler.on_created(event)

        mock_ingest.assert_not_called()
        assert any("Skipping raw scan part" in r.message for r in caplog.records)

    def test_merged_00_is_ingested(self, tmp_path):
        archive = tmp_path / "archive"
        db = tmp_path / "archive.db"
        handler = _TiffHandler(archive, db)

        merged = tmp_path / "artikel_00.tif"
        merged.touch()

        event = MagicMock()
        event.is_directory = False
        event.src_path = str(merged)

        with (
            patch("app.worker.watcher.ingest") as mock_ingest,
            patch("app.worker.watcher.time.sleep"),  # skip the 2-second wait
        ):
            handler.on_created(event)

        mock_ingest.assert_called_once_with(merged, archive, db)
