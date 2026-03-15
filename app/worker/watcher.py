"""Inbox directory watcher: triggers ingestion when new TIFF files appear."""

import logging
import re
import time
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.db.database import init_db
from app.worker.ingestion import ingest

log = logging.getLogger(__name__)

_TIFF_SUFFIXES = {".tif", ".tiff"}
_PART_RE = re.compile(r"^.+_(\d{2})$")


def _is_raw_part(path: Path) -> bool:
    """Return True for _01, _02, … files (not _00).

    Raw parts must be stitched via 'zeitungsarchiv process' before import.
    """
    m = _PART_RE.match(path.stem)
    return bool(m) and int(m.group(1)) > 0


class _TiffHandler(FileSystemEventHandler):
    """Handle new TIFF files dropped into the inbox directory."""

    def __init__(self, archive_dir: Path, db_path: Path) -> None:
        self.archive_dir = archive_dir
        self.db_path = db_path

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in _TIFF_SUFFIXES:
            return

        if _is_raw_part(path):
            log.info("Skipping raw scan part (use 'process' to stitch): %s", path.name)
            return

        # Wait briefly so the scanner has finished writing the file
        time.sleep(2)

        if not path.exists():
            log.warning("File disappeared before ingestion: %s", path)
            return

        log.info("New scan detected: %s", path.name)
        ingest(path, self.archive_dir, self.db_path)


def watch(
    inbox_dir: Path,
    archive_dir: Path,
    db_path: Path,
    poll_interval: float = 1.0,
) -> None:
    """
    Block and watch inbox_dir for new TIFF files indefinitely.

    Initializes the DB schema before starting.
    Press Ctrl-C to stop.
    """
    init_db(db_path)
    inbox_dir.mkdir(parents=True, exist_ok=True)

    handler = _TiffHandler(archive_dir, db_path)
    observer = Observer()
    observer.schedule(handler, str(inbox_dir), recursive=False)
    observer.start()

    log.info("Watching %s for new scans (Ctrl-C to stop)...", inbox_dir)
    try:
        while True:
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        log.info("Watcher stopped by user.")
    finally:
        observer.stop()
        observer.join()
