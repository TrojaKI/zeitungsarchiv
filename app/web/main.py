"""FastAPI application factory for Zeitungsarchiv."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.web.routes import articles, admin, review, search

_ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "/app/archive"))
_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Zeitungsarchiv")

# Register routers
app.include_router(search.router)
app.include_router(articles.router)
app.include_router(review.router)
app.include_router(admin.router)
