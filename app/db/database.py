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
        # Migrate existing DBs: add columns introduced after initial schema
        existing = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
        migrations = [
            ("locations", "ALTER TABLE articles ADD COLUMN locations TEXT"),
            ("urls", "ALTER TABLE articles ADD COLUMN urls TEXT"),
            ("article_group", "ALTER TABLE articles ADD COLUMN article_group TEXT"),
            ("page_number", "ALTER TABLE articles ADD COLUMN page_number INTEGER"),
        ]
        for col, sql in migrations:
            if col not in existing:
                conn.execute(sql)
        # Migrate: create books and recipes tables if not yet present
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS books (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                title       TEXT,
                author      TEXT,
                publisher   TEXT,
                year        TEXT,
                pages       TEXT,
                price       TEXT,
                isbn        TEXT,
                description TEXT
            );
            CREATE TABLE IF NOT EXISTS recipes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id   INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                name         TEXT,
                category     TEXT,
                servings     TEXT,
                prep_time    TEXT,
                ingredients  TEXT,
                instructions TEXT
            );
        """)


def insert_article(article: dict, db_path: Path = _DEFAULT_DB_PATH) -> int:
    """Insert a new article and return its id."""
    sql = """
        INSERT INTO articles (
            filename, scan_date, newspaper, article_date, page,
            headline, summary, category, tags,
            full_text, image_path, thumb_path,
            ocr_confidence, needs_review, meta_source,
            locations, urls,
            article_group, page_number
        ) VALUES (
            :filename, :scan_date, :newspaper, :article_date, :page,
            :headline, :summary, :category, :tags,
            :full_text, :image_path, :thumb_path,
            :ocr_confidence, :needs_review, :meta_source,
            :locations, :urls,
            :article_group, :page_number
        )
    """
    # Serialize tags list to JSON string if necessary
    data = dict(article)
    for field in ("tags", "locations", "urls"):
        if isinstance(data.get(field), list):
            data[field] = json.dumps(data[field], ensure_ascii=False)
    data.setdefault("locations", None)
    data.setdefault("urls", None)
    data.setdefault("article_group", None)
    data.setdefault("page_number", None)

    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, data)
        return cursor.lastrowid


def get_group_articles(group: str, db_path: Path = _DEFAULT_DB_PATH) -> list[dict]:
    """Return all articles belonging to the same article_group, ordered by page_number."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM articles WHERE article_group = ? ORDER BY page_number",
            (group,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_article(article_id: int, fields: dict, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Update specific fields of an existing article."""
    if not fields:
        return
    for field in ("tags", "locations", "urls"):
        if isinstance(fields.get(field), list):
            fields[field] = json.dumps(fields[field], ensure_ascii=False)

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


def search_full(
    query: str = "",
    newspaper: str = "",
    category: str = "",
    date_from: str = "",
    date_to: str = "",
    location: str = "",
    needs_review: Optional[bool] = None,
    limit: int = 20,
    offset: int = 0,
    db_path: Path = _DEFAULT_DB_PATH,
) -> list[dict]:
    """
    Full-text search with snippet highlighting and all filter options.

    Uses FTS5 snippet() for search terms; falls back to plain SELECT otherwise.
    """
    params: list = []
    q = query.strip().replace('"', "")  # sanitize FTS special chars

    if q:
        sql = """
            SELECT a.*,
                   snippet(articles_fts, 2, '<mark>', '</mark>', '…', 20) AS snippet
            FROM articles_fts
            JOIN articles a ON articles_fts.rowid = a.id
            WHERE articles_fts MATCH ?
        """
        params.append(q)
        prefix = "AND a."
    else:
        sql = "SELECT *, '' AS snippet FROM articles WHERE 1=1"
        prefix = "AND "

    if newspaper:
        sql += f" {prefix}newspaper = ?"
        params.append(newspaper)
    if category:
        sql += f" {prefix}category = ?"
        params.append(category)
    if date_from:
        sql += f" {prefix}article_date >= ?"
        params.append(date_from)
    if date_to:
        sql += f" {prefix}article_date <= ?"
        params.append(date_to)
    if needs_review is not None:
        sql += f" {prefix}needs_review = ?"
        params.append(1 if needs_review else 0)
    if location:
        # Locations stored as JSON array — match exact entry via JSON quoting
        loc_col = "a.locations" if q else "locations"
        sql += f' AND {loc_col} LIKE ?'
        params.append(f'%"{location}"%')

    order_col = "a.article_date" if q else "article_date"
    sql += f" ORDER BY {order_col} DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_place(place_id: int, fields: dict, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Update specific fields of a place entry."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["_id"] = place_id
    with get_connection(db_path) as conn:
        conn.execute(f"UPDATE places SET {set_clause} WHERE id = :_id", fields)


def delete_place(place_id: int, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Delete a single place entry."""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM places WHERE id = ?", (place_id,))


def get_all_places(
    query: str = "",
    city: str = "",
    country: str = "",
    db_path: Path = _DEFAULT_DB_PATH,
) -> list[dict]:
    """Return all places with article info, optionally filtered."""
    params: list = []
    sql = """
        SELECT p.*, a.id AS article_id, a.headline, a.article_date, a.newspaper
        FROM places p JOIN articles a ON a.id = p.article_id
        WHERE 1=1
    """
    if query:
        sql += " AND (p.name LIKE ? OR p.city LIKE ? OR p.address LIKE ? OR p.country LIKE ?)"
        q = f"%{query}%"
        params.extend([q, q, q, q])
    if city:
        sql += " AND p.city LIKE ?"
        params.append(f"%{city}%")
    if country:
        sql += " AND p.country = ?"
        params.append(country)
    sql += " ORDER BY p.country, p.city, p.name"
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_place_filter_options(db_path: Path = _DEFAULT_DB_PATH) -> dict:
    """Return distinct countries and cities for place filter dropdowns."""
    with get_connection(db_path) as conn:
        countries = [r[0] for r in conn.execute(
            "SELECT DISTINCT country FROM places WHERE country IS NOT NULL ORDER BY country"
        ).fetchall()]
        cities = [r[0] for r in conn.execute(
            "SELECT DISTINCT city FROM places WHERE city IS NOT NULL ORDER BY city"
        ).fetchall()]
    return {"countries": countries, "cities": cities}


def get_review_count(db_path: Path = _DEFAULT_DB_PATH) -> int:
    """Return the number of articles flagged for manual review."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM articles WHERE needs_review = 1").fetchone()
    return row[0] if row else 0


def get_stats(db_path: Path = _DEFAULT_DB_PATH) -> dict:
    """Return aggregate statistics for the archive."""
    with get_connection(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        review = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE needs_review = 1"
        ).fetchone()[0]
        by_newspaper = conn.execute(
            "SELECT newspaper, COUNT(*) AS cnt FROM articles "
            "WHERE newspaper IS NOT NULL GROUP BY newspaper ORDER BY cnt DESC"
        ).fetchall()
        by_category = conn.execute(
            "SELECT category, COUNT(*) AS cnt FROM articles "
            "WHERE category IS NOT NULL GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
    return {
        "total": total,
        "needs_review": review,
        "by_newspaper": [dict(r) for r in by_newspaper],
        "by_category": [dict(r) for r in by_category],
    }


def insert_places(article_id: int, places: list[dict],
                  db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Delete existing places for the article and insert the new list."""
    sql = """INSERT INTO places
             (article_id, name, description, address, postal_code,
              city, country, phone, hours, url)
             VALUES (:article_id, :name, :description, :address, :postal_code,
                     :city, :country, :phone, :hours, :url)"""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM places WHERE article_id = ?", (article_id,))
        for p in places:
            conn.execute(sql, {"article_id": article_id, **p})


def get_places(article_id: int, db_path: Path = _DEFAULT_DB_PATH) -> list[dict]:
    """Return all places linked to an article."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM places WHERE article_id = ? ORDER BY id", (article_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def insert_books(article_id: int, books: list[dict],
                 db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Delete existing books for the article and insert the new list."""
    sql = """INSERT INTO books
             (article_id, title, author, publisher, year, pages, price, isbn, description)
             VALUES (:article_id, :title, :author, :publisher, :year, :pages, :price, :isbn, :description)"""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM books WHERE article_id = ?", (article_id,))
        for b in books:
            conn.execute(sql, {"article_id": article_id, **b})


def get_books(article_id: int, db_path: Path = _DEFAULT_DB_PATH) -> list[dict]:
    """Return all books linked to an article."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM books WHERE article_id = ? ORDER BY id", (article_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_book(book_id: int, fields: dict, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Update specific fields of a book entry."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["_id"] = book_id
    with get_connection(db_path) as conn:
        conn.execute(f"UPDATE books SET {set_clause} WHERE id = :_id", fields)


def delete_book(book_id: int, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Delete a single book entry."""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))


def insert_recipes(article_id: int, recipes: list[dict],
                   db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Delete existing recipes for the article and insert the new list."""
    sql = """INSERT INTO recipes
             (article_id, name, category, servings, prep_time, ingredients, instructions)
             VALUES (:article_id, :name, :category, :servings, :prep_time, :ingredients, :instructions)"""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM recipes WHERE article_id = ?", (article_id,))
        for r in recipes:
            conn.execute(sql, {"article_id": article_id, **r})


def get_recipes(article_id: int, db_path: Path = _DEFAULT_DB_PATH) -> list[dict]:
    """Return all recipes linked to an article."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM recipes WHERE article_id = ? ORDER BY id", (article_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_recipe(recipe_id: int, fields: dict, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Update specific fields of a recipe entry."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["_id"] = recipe_id
    with get_connection(db_path) as conn:
        conn.execute(f"UPDATE recipes SET {set_clause} WHERE id = :_id", fields)


def delete_recipe(recipe_id: int, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Delete a single recipe entry."""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))


def search_places(query: str, db_path: Path = _DEFAULT_DB_PATH) -> list[dict]:
    """Search places by name, city, or address (case-insensitive LIKE)."""
    q = f"%{query}%"
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT p.*, a.headline, a.article_date, a.newspaper
               FROM places p JOIN articles a ON a.id = p.article_id
               WHERE p.name LIKE ? OR p.city LIKE ? OR p.address LIKE ?
                  OR p.postal_code LIKE ? OR p.country LIKE ?
               ORDER BY p.city, p.name""",
            (q, q, q, q, q),
        ).fetchall()
    return [dict(r) for r in rows]


def get_filter_options(db_path: Path = _DEFAULT_DB_PATH) -> dict:
    """Return distinct values for filter dropdowns (newspapers, categories, locations)."""
    with get_connection(db_path) as conn:
        newspapers = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT newspaper FROM articles "
                "WHERE newspaper IS NOT NULL ORDER BY newspaper"
            ).fetchall()
        ]
        categories = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT category FROM articles "
                "WHERE category IS NOT NULL ORDER BY category"
            ).fetchall()
        ]
        # Parse all locations JSON arrays and collect distinct values
        raw_locs = conn.execute(
            "SELECT locations FROM articles WHERE locations IS NOT NULL AND locations != '[]'"
        ).fetchall()

    all_locations: set[str] = set()
    for row in raw_locs:
        try:
            locs = json.loads(row[0])
            all_locations.update(locs)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "newspapers": newspapers,
        "categories": categories,
        "locations": sorted(all_locations),
    }
