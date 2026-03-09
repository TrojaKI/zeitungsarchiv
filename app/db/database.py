"""Database access layer for Zeitungsarchiv (SQLite + FTS5)."""

import sqlite3
import json
from pathlib import Path
from typing import Optional

# Resolve schema and DB paths relative to this file
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "db" / "archive.db"


def get_connection(db_path: Path = _DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with row_factory and WAL mode enabled."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # WAL mode allows concurrent reads while writing
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Create tables and FTS index from schema.sql if they do not exist."""
    schema = _SCHEMA_PATH.read_text()
    with get_connection(db_path) as conn:
        conn.executescript(schema)


def insert_article(article: dict, db_path: Path = _DEFAULT_DB_PATH) -> int:
    """Insert a new article and return its id."""
    sql = """
        INSERT INTO articles (
            filename, scan_date, newspaper, article_date, page,
            headline, summary, category, tags,
            full_text, image_path, thumb_path,
            ocr_confidence, needs_review, meta_source
        ) VALUES (
            :filename, :scan_date, :newspaper, :article_date, :page,
            :headline, :summary, :category, :tags,
            :full_text, :image_path, :thumb_path,
            :ocr_confidence, :needs_review, :meta_source
        )
    """
    # Serialize tags list to JSON string if necessary
    data = dict(article)
    if isinstance(data.get("tags"), list):
        data["tags"] = json.dumps(data["tags"], ensure_ascii=False)

    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, data)
        return cursor.lastrowid


def update_article(article_id: int, fields: dict, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Update specific fields of an existing article."""
    if not fields:
        return
    if isinstance(fields.get("tags"), list):
        fields["tags"] = json.dumps(fields["tags"], ensure_ascii=False)

    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["_id"] = article_id
    with get_connection(db_path) as conn:
        conn.execute(f"UPDATE articles SET {set_clause} WHERE id = :_id", fields)


def get_article(article_id: int, db_path: Path = _DEFAULT_DB_PATH) -> Optional[dict]:
    """Fetch a single article by id, or None if not found."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    return dict(row) if row else None


def search_articles(
    query: str,
    newspaper: Optional[str] = None,
    category: Optional[str] = None,
    needs_review: Optional[bool] = None,
    limit: int = 20,
    offset: int = 0,
    db_path: Path = _DEFAULT_DB_PATH,
) -> list[dict]:
    """Full-text search with optional filters. Returns a list of article dicts."""
    params: list = []

    if query.strip():
        base_sql = """
            SELECT a.*
            FROM articles_fts fts
            JOIN articles a ON a.id = fts.rowid
            WHERE articles_fts MATCH ?
        """
        params.append(query)
    else:
        base_sql = "SELECT * FROM articles WHERE 1=1"

    if newspaper:
        base_sql += " AND a.newspaper = ?" if "fts" in base_sql else " AND newspaper = ?"
        params.append(newspaper)
    if category:
        base_sql += " AND a.category = ?" if "fts" in base_sql else " AND category = ?"
        params.append(category)
    if needs_review is not None:
        col = "a.needs_review" if "fts" in base_sql else "needs_review"
        base_sql += f" AND {col} = ?"
        params.append(1 if needs_review else 0)

    base_sql += " ORDER BY a.scan_date DESC LIMIT ? OFFSET ?" if "fts" in base_sql \
        else " ORDER BY scan_date DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_connection(db_path) as conn:
        rows = conn.execute(base_sql, params).fetchall()
    return [dict(r) for r in rows]
