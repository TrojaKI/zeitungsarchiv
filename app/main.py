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
_LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"

# Uvicorn log config: apply our timestamp format to all uvicorn loggers.
# Without this, uvicorn uses its own formatter that omits the timestamp.
_UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": _LOG_FORMAT,
            "use_colors": False,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(asctime)s  %(levelname)-8s  %(client_addr)s - "%(request_line)s" %(status_code)s',
            "use_colors": False,
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": _LOG_LEVEL, "propagate": False},
        "uvicorn.error": {"level": _LOG_LEVEL},
        "uvicorn.access": {"handlers": ["access"], "level": _LOG_LEVEL, "propagate": False},
    },
}


def _run_watcher():
    logging.basicConfig(level=_LOG_LEVEL, format=_LOG_FORMAT)
    from app.worker.watcher import watch
    watch(_INBOX, _ARCHIVE, _DB)


def _run_web():
    import uvicorn
    uvicorn.run(
        "app.web.main:app",
        host=os.getenv("WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("WEB_PORT", "8000")),
        log_config=_UVICORN_LOG_CONFIG,
    )


if __name__ == "__main__":
    logging.basicConfig(level=_LOG_LEVEL, format=_LOG_FORMAT)

    processes: list[multiprocessing.Process] = []

    if _WATCH:
        p = multiprocessing.Process(target=_run_watcher, name="watcher", daemon=True)
        p.start()
        processes.append(p)
        logging.info("Watcher started (pid=%s)", p.pid)

    # Web server runs in the main process
    _run_web()
