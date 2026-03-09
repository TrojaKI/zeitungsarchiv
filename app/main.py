"""Application entry point: starts inbox watcher + web server in parallel."""

import logging
import multiprocessing
import os
from pathlib import Path

_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))
_INBOX = Path(os.getenv("INBOX_DIR", "/app/inbox"))
_ARCHIVE = Path(os.getenv("ARCHIVE_DIR", "/app/archive"))
_WATCH = os.getenv("WATCH_INBOX", "true").lower() == "true"
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def _run_watcher():
    logging.basicConfig(level=_LOG_LEVEL, format="%(asctime)s  %(levelname)-8s  %(message)s")
    from app.worker.watcher import watch
    watch(_INBOX, _ARCHIVE, _DB)


def _run_web():
    import uvicorn
    uvicorn.run(
        "app.web.main:app",
        host=os.getenv("WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("WEB_PORT", "8000")),
        log_level=_LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    logging.basicConfig(level=_LOG_LEVEL, format="%(asctime)s  %(levelname)-8s  %(message)s")

    processes: list[multiprocessing.Process] = []

    if _WATCH:
        p = multiprocessing.Process(target=_run_watcher, name="watcher", daemon=True)
        p.start()
        processes.append(p)
        logging.info("Watcher started (pid=%s)", p.pid)

    # Web server runs in the main process
    _run_web()
