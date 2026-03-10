"""FastAPI application factory for Zeitungsarchiv."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.web.routes import articles, admin, review, search

_ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "/app/archive"))
_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Zeitungsarchiv")

# Static files: app CSS/JS and archive images
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
app.mount("/archive", StaticFiles(directory=str(_ARCHIVE_DIR)), name="archive")

# Register routers
app.include_router(search.router)
app.include_router(articles.router)
app.include_router(review.router)
app.include_router(admin.router)
