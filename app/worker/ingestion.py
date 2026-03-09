"""Full ingestion pipeline: TIFF scan → preprocess → OCR → metadata → DB."""

import logging
import shutil
from datetime import date
from pathlib import Path

from app.db.database import init_db, insert_article
from app.worker.metadata import extract_metadata
from app.worker.ocr import process_scan

log = logging.getLogger(__name__)

# Subdirectory for files that could not be processed
_QUARANTINE_DIR_NAME = "quarantine"


def _quarantine(path: Path, archive_dir: Path, reason: str) -> None:
    """Move a problematic file to archive/quarantine/ and log the reason."""
    q_dir = archive_dir / _QUARANTINE_DIR_NAME
    q_dir.mkdir(parents=True, exist_ok=True)
    dest = q_dir / path.name
    shutil.move(str(path), dest)
    log.error("Quarantined %s → %s  Reason: %s", path.name, dest, reason)


def ingest(
    tiff_path: Path,
    archive_dir: Path,
    db_path: Path,
) -> int | None:
    """
    Process a single TIFF scan end-to-end.

    Steps:
      1. OCR + image preprocessing (preprocess → Tesseract)
      2. Metadata extraction via Claude API
      3. Insert article record into SQLite DB
      4. Move TIFF from inbox to archive/<stem>/original.tif

    Returns the new article DB id, or None on failure.
    """
    log.info("Ingesting: %s", tiff_path.name)

    # --- step 1: OCR ---
    try:
        ocr_result = process_scan(tiff_path, archive_dir)
    except Exception as exc:
        _quarantine(tiff_path, archive_dir, f"OCR failed: {exc}")
        return None

    # --- step 2: metadata via Claude API ---
    metadata = extract_metadata(ocr_result["full_text"])

    # Elevate needs_review if critical metadata fields are missing
    if not metadata.get("article_date") or not metadata.get("newspaper"):
        ocr_result["needs_review"] = True
        metadata["meta_source"] = "partial"

    # --- step 3: build DB record ---
    article = {
        "filename": tiff_path.name,
        "scan_date": date.today().isoformat(),
        "newspaper": metadata.get("newspaper"),
        "article_date": metadata.get("article_date"),
        "page": metadata.get("page"),
        "headline": metadata.get("headline"),
        "summary": metadata.get("summary"),
        "category": metadata.get("category"),
        "tags": metadata.get("tags", []),
        "full_text": ocr_result["full_text"],
        "image_path": ocr_result["image_path"],
        "thumb_path": ocr_result["thumb_path"],
        "ocr_confidence": ocr_result["ocr_confidence"],
        "needs_review": int(ocr_result["needs_review"]),
        "meta_source": ocr_result.get("meta_source", metadata.get("meta_source", "auto")),
    }

    try:
        article_id = insert_article(article, db_path)
    except Exception as exc:
        log.error("DB insert failed for %s: %s", tiff_path.name, exc)
        _quarantine(tiff_path, archive_dir, f"DB insert failed: {exc}")
        return None

    # --- step 4: move original TIFF to archive ---
    dest_dir = archive_dir / tiff_path.stem
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "original.tif"
    shutil.move(str(tiff_path), dest)

    log.info(
        "Ingested %s → id=%s  confidence=%.1f  needs_review=%s",
        tiff_path.name, article_id, article["ocr_confidence"], bool(article["needs_review"]),
    )
    return article_id


def ingest_directory(
    inbox_dir: Path,
    archive_dir: Path,
    db_path: Path,
) -> list[int]:
    """
    Process all TIFF files currently present in inbox_dir.

    Initializes the DB schema on first run.
    Returns a list of successfully created article ids.
    """
    init_db(db_path)
    tiffs = sorted(inbox_dir.glob("*.tif")) + sorted(inbox_dir.glob("*.tiff"))

    if not tiffs:
        log.info("No TIFF files found in %s", inbox_dir)
        return []

    ids = []
    for tiff in tiffs:
        article_id = ingest(tiff, archive_dir, db_path)
        if article_id is not None:
            ids.append(article_id)

    log.info("Batch complete: %d/%d files ingested successfully", len(ids), len(tiffs))
    return ids
