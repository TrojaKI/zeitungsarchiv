"""Full ingestion pipeline: TIFF scan → preprocess → OCR → metadata → DB."""

import logging
import re
import shutil
from datetime import date
from pathlib import Path

from app.db.database import (
    init_db, insert_article, insert_books, insert_places, insert_recipes,
    sync_locations_from_places,
)
from app.worker.books import extract_books
from app.worker.metadata import extract_metadata
from app.worker.ocr import process_scan
from app.worker.places import extract_places
from app.worker.recipes import extract_recipes
from app.worker.stitch import stitch_multipart

log = logging.getLogger(__name__)

_PART_RE = re.compile(r"^(.+)_(\d{2})$")
# Multi-page articles: name_p01.tif, name_p02.tif, … (NOT stitched, kept separate)
_PAGE_RE = re.compile(r"^(.+)_p(\d{2})$", re.IGNORECASE)


def group_multipart_scans(
    tiffs: list[Path],
) -> tuple[list[Path], list[tuple[list[Path], Path]]]:
    """Split a TIFF list into files to ingest directly and groups to stitch first.

    Pattern: stem ending in _NN (2 digits).
    - _00 → direct ingest (merged file already present)
    - _01/_02/… + existing _00 → skip parts, _00 already handled
    - _01/_02/… without _00 → group for stitching
    - no suffix pattern → direct ingest
    """
    # Separate matched from unmatched stems
    groups: dict[str, dict[int, Path]] = {}  # prefix → {index: path}
    standalone: list[Path] = []

    for tiff in tiffs:
        # _pNN files are individual pages — never stitch, always ingest directly
        if _PAGE_RE.match(tiff.stem):
            standalone.append(tiff)
            continue
        m = _PART_RE.match(tiff.stem)
        if m:
            prefix, idx = m.group(1), int(m.group(2))
            groups.setdefault(prefix, {})[idx] = tiff
        else:
            standalone.append(tiff)

    to_ingest: list[Path] = list(standalone)
    to_stitch: list[tuple[list[Path], Path]] = []

    for prefix, indexed in groups.items():
        if 0 in indexed:
            # _00 already exists — ingest it, ignore parts
            to_ingest.append(indexed[0])
        else:
            # Only raw parts present — schedule stitching
            parts = [indexed[i] for i in sorted(indexed) if i > 0]
            # Output path sits next to the parts
            output_path = parts[0].parent / f"{prefix}_00.tif"
            to_stitch.append((parts, output_path))

    return to_ingest, to_stitch


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

    # Detect multi-page article convention: name_p01.tif, name_p02.tif, …
    _page_match = _PAGE_RE.match(tiff_path.stem)
    article_group = _page_match.group(1) if _page_match else None
    page_number = int(_page_match.group(2)) if _page_match else None

    # --- step 1: OCR ---
    try:
        ocr_result = process_scan(tiff_path, archive_dir)
    except Exception as exc:
        _quarantine(tiff_path, archive_dir, f"OCR failed: {exc}")
        return None

    # --- step 2: metadata via Claude API ---
    metadata = extract_metadata(ocr_result["full_text"], ocr_result.get("margin_text", ""))

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
        "section": metadata.get("section"),
        "category": metadata.get("category"),
        "tags": metadata.get("tags", []),
        "locations": metadata.get("locations", []),
        "urls": metadata.get("urls", []),
        "full_text": ocr_result["full_text"],
        "image_path": ocr_result["image_path"],
        "thumb_path": ocr_result["thumb_path"],
        "ocr_confidence": ocr_result["ocr_confidence"],
        "needs_review": int(ocr_result["needs_review"]),
        "meta_source": ocr_result.get("meta_source", metadata.get("meta_source", "auto")),
        "article_group": article_group,
        "page_number": page_number,
    }

    # Extract structured place listings (restaurants, hotels, etc.)
    # Retry once on empty result — LLM may time out or return [] spuriously
    _MAX_EXTRACT_ATTEMPTS = 2
    places: list[dict] = []
    for attempt in range(1, _MAX_EXTRACT_ATTEMPTS + 1):
        places = extract_places(ocr_result["full_text"])
        if places:
            break
        if attempt < _MAX_EXTRACT_ATTEMPTS:
            log.warning("extract_places returned empty (attempt %d/%d), retrying…",
                        attempt, _MAX_EXTRACT_ATTEMPTS)
    books   = extract_books(ocr_result["full_text"])
    recipes = extract_recipes(ocr_result["full_text"])

    try:
        article_id = insert_article(article, db_path)
    except Exception as exc:
        log.error("DB insert failed for %s: %s", tiff_path.name, exc)
        _quarantine(tiff_path, archive_dir, f"DB insert failed: {exc}")
        return None

    # --- step 4: save extracted places, books, and recipes ---
    if places:
        insert_places(article_id, places, db_path)
        log.info("Saved %d place(s) for article id=%s", len(places), article_id)
        # Merge place cities into article.locations
        updated = sync_locations_from_places(article_id, db_path)
        if updated:
            log.info("Updated locations for article id=%s: %s", article_id, updated)
        # Geocode newly inserted places (non-blocking: errors are logged only)
        try:
            from app.worker.geocoder import geocode_all_places
            geocode_all_places(db_path)
        except Exception as exc:
            log.warning("Geocoding failed for article id=%s: %s", article_id, exc)
    if books:
        insert_books(article_id, books, db_path)
        log.info("Saved %d book(s) for article id=%s", len(books), article_id)
    if recipes:
        insert_recipes(article_id, recipes, db_path)
        log.info("Saved %d recipe(s) for article id=%s", len(recipes), article_id)

    # --- step 5: move original TIFF to archive ---
    dest_dir = archive_dir / tiff_path.stem
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "original.tif"
    # Use explicit copy+delete to handle cross-device moves (Docker volumes on separate filesystems).
    # shutil.move() fails with OSError EXDEV when inbox and archive are on different mounts.
    try:
        shutil.copy2(str(tiff_path), dest)
        tiff_path.unlink()
    except FileNotFoundError:
        log.warning("Source TIFF already moved or deleted, skipping move: %s", tiff_path.name)

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

    Multi-part scans (_01, _02, …) are stitched via Hugin into a _00 file
    before ingestion. Parts are moved to archive/<stem>/parts/ afterwards.

    Initializes the DB schema on first run.
    Returns a list of successfully created article ids.
    """
    init_db(db_path)
    tiffs = sorted(inbox_dir.glob("*.tif")) + sorted(inbox_dir.glob("*.tiff"))

    if not tiffs:
        log.info("No TIFF files found in %s", inbox_dir)
        return []

    standalone, groups = group_multipart_scans(tiffs)

    # Stitch groups that don't have a _00 yet
    for parts, output_path in groups:
        try:
            stitch_multipart(parts, output_path)
            standalone.append(output_path)
            # Move raw parts out of inbox into archive/<stem>/parts/
            dest_parts_dir = archive_dir / output_path.stem / "parts"
            dest_parts_dir.mkdir(parents=True, exist_ok=True)
            for p in parts:
                shutil.move(str(p), dest_parts_dir / p.name)
        except Exception as exc:
            log.error("Stitching failed for %s: %s", output_path.name, exc)
            # Parts stay in inbox; no _00 created; skip this group

    ids = []
    for tiff in sorted(standalone):
        article_id = ingest(tiff, archive_dir, db_path)
        if article_id is not None:
            ids.append(article_id)

    log.info("Batch complete: %d/%d files ingested successfully", len(ids), len(standalone))
    return ids
