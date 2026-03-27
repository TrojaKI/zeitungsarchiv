"""FastAPI application factory for Zeitungsarchiv."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)

# Load .env automatically if present (local dev)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db.database import init_db
from app.web.routes import articles, admin, books, places, recipes, review, search

_ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "/app/archive"))
_STATIC_DIR = Path(__file__).parent / "static"
_DB_PATH = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run schema creation and column migrations on startup
    init_db(_DB_PATH)
    yield


app = FastAPI(title="Zeitungsarchiv", lifespan=lifespan)

# Static files: app CSS/JS and archive images
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
app.mount("/archive", StaticFiles(directory=str(_ARCHIVE_DIR)), name="archive")

# Register routers
app.include_router(search.router)
app.include_router(articles.router)
app.include_router(places.router)
app.include_router(books.router)
app.include_router(recipes.router)
app.include_router(review.router)
app.include_router(admin.router)
