"""Database access layer for Zeitungsarchiv (SQLite + FTS5)."""

import sqlite3
import json
from pathlib import Path
from typing import Optional

# Countries and large regions used to split the locations dropdown.
# Values from articles.locations matching this set appear in the "Land/Region"
# filter; everything else appears in the "Ort" filter.
_GEO_REGIONS: frozenset[str] = frozenset({
    # Sovereign states
    "Österreich", "Deutschland", "Schweiz", "Frankreich", "Italien", "Spanien",
    "Portugal", "Niederlande", "Belgien", "Luxemburg", "Polen", "Tschechien",
    "Slowakei", "Ungarn", "Slowenien", "Kroatien", "Rumänien", "Bulgarien",
    "Griechenland", "Türkei", "Russland", "Ukraine", "Serbien", "Dänemark",
    "Schweden", "Norwegen", "Finnland", "Irland", "Großbritannien", "England",
    "USA", "Kanada", "Australien", "Japan", "China", "Indien", "Brasilien",
    "Chile", "Argentinien", "Mexiko", "Israel", "Iran", "Irak",
    # Large regions / federal states
    "Bayern", "Flandern", "Südtirol", "Europa",
    "Niederösterreich", "Oberösterreich", "Steiermark", "Tirol", "Salzburg",
    "Kärnten", "Burgenland", "Vorarlberg",
    # Common Austrian sub-regions
    "Innviertel", "Mühlviertel", "Waldviertel", "Weinviertel",
    "Hausruckviertel", "Traunviertel", "Sauwald", "Salzkammergut",
    "Wachau", "Kamptal", "Kremstal", "Eisacktal",
})

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


def _migrate_places_normalize(conn: sqlite3.Connection) -> None:
    """One-time migration: split old places table into places + place_articles.

    Called by init_db() when place_articles table does not yet exist.
    Deduplicates by (LOWER(TRIM(name)), LOWER(TRIM(city))) and picks the
    first non-NULL value for each place-level field across duplicate rows.
    """
    import logging
    log = logging.getLogger(__name__)

    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(places)")}
    if "article_id" not in existing_cols:
        # Already migrated or empty DB with new schema, nothing to do
        log.info("places migration: no old schema found, skipping")
        return

    log.info("Migrating places table to normalized schema...")
    conn.execute("ALTER TABLE places RENAME TO _places_old")

    # Create new normalized tables (executescript is not used here because it
    # commits any open transaction; use execute() to stay in the same transaction)
    conn.execute("""
        CREATE TABLE places (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            address     TEXT,
            postal_code TEXT,
            city        TEXT,
            country     TEXT,
            phone       TEXT,
            hours       TEXT,
            url         TEXT,
            lat         REAL,
            lng         REAL,
            name_key    TEXT NOT NULL,
            city_key    TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX places_dedup ON places (name_key, city_key)"
    )
    conn.execute("""
        CREATE TABLE place_articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            place_id    INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
            article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
            description TEXT,
            rating      TEXT,
            UNIQUE (place_id, article_id)
        )
    """)
    conn.execute("CREATE INDEX place_articles_article ON place_articles (article_id)")
    conn.execute("CREATE INDEX place_articles_place   ON place_articles (place_id)")
    conn.execute("""
        CREATE TRIGGER place_articles_cleanup
        AFTER DELETE ON place_articles
        BEGIN
            DELETE FROM places WHERE id = OLD.place_id
              AND NOT EXISTS (SELECT 1 FROM place_articles WHERE place_id = OLD.place_id);
        END
    """)

    # Build canonical places (one per name+city combination, best non-NULL fields)
    conn.execute("""
        INSERT OR IGNORE INTO places
            (name, address, postal_code, city, country, phone, hours, url,
             lat, lng, name_key, city_key)
        SELECT
            name,
            (SELECT address     FROM _places_old o2
             WHERE LOWER(TRIM(o2.name)) = nk
               AND LOWER(TRIM(COALESCE(o2.city, ''))) = ck
               AND o2.address IS NOT NULL LIMIT 1),
            (SELECT postal_code FROM _places_old o2
             WHERE LOWER(TRIM(o2.name)) = nk
               AND LOWER(TRIM(COALESCE(o2.city, ''))) = ck
               AND o2.postal_code IS NOT NULL LIMIT 1),
            (SELECT city        FROM _places_old o2
             WHERE LOWER(TRIM(o2.name)) = nk
               AND LOWER(TRIM(COALESCE(o2.city, ''))) = ck
               AND o2.city IS NOT NULL LIMIT 1),
            (SELECT country     FROM _places_old o2
             WHERE LOWER(TRIM(o2.name)) = nk
               AND LOWER(TRIM(COALESCE(o2.city, ''))) = ck
               AND o2.country IS NOT NULL LIMIT 1),
            (SELECT phone       FROM _places_old o2
             WHERE LOWER(TRIM(o2.name)) = nk
               AND LOWER(TRIM(COALESCE(o2.city, ''))) = ck
               AND o2.phone IS NOT NULL LIMIT 1),
            (SELECT hours       FROM _places_old o2
             WHERE LOWER(TRIM(o2.name)) = nk
               AND LOWER(TRIM(COALESCE(o2.city, ''))) = ck
               AND o2.hours IS NOT NULL LIMIT 1),
            (SELECT url         FROM _places_old o2
             WHERE LOWER(TRIM(o2.name)) = nk
               AND LOWER(TRIM(COALESCE(o2.city, ''))) = ck
               AND o2.url IS NOT NULL LIMIT 1),
            (SELECT lat FROM _places_old o2
             WHERE LOWER(TRIM(o2.name)) = nk
               AND LOWER(TRIM(COALESCE(o2.city, ''))) = ck
               AND o2.lat IS NOT NULL LIMIT 1),
            (SELECT lng FROM _places_old o2
             WHERE LOWER(TRIM(o2.name)) = nk
               AND LOWER(TRIM(COALESCE(o2.city, ''))) = ck
               AND o2.lng IS NOT NULL LIMIT 1),
            nk, ck
        FROM (
            SELECT name,
                   LOWER(TRIM(name))                   AS nk,
                   LOWER(TRIM(COALESCE(city, '')))      AS ck
            FROM _places_old
            WHERE name IS NOT NULL AND TRIM(name) != ''
            GROUP BY nk, ck
        )
    """)

    # Create join rows (one per original row, preserving description + rating).
    # INSERT OR IGNORE handles the case where the same place appears more than
    # once in the same article (keeps the first occurrence).
    conn.execute("""
        INSERT OR IGNORE INTO place_articles (place_id, article_id, description, rating)
        SELECT p.id, op.article_id, op.description, op.rating
        FROM _places_old op
        JOIN places p
          ON p.name_key = LOWER(TRIM(op.name))
         AND p.city_key = LOWER(TRIM(COALESCE(op.city, '')))
        WHERE op.name IS NOT NULL AND TRIM(op.name) != ''
    """)

    conn.execute("DROP TABLE _places_old")

    place_count = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    pa_count    = conn.execute("SELECT COUNT(*) FROM place_articles").fetchone()[0]
    log.info("Migration done: %d canonical places, %d article links", place_count, pa_count)


def init_db(db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Create tables and FTS index from schema.sql if they do not exist."""
    schema = _SCHEMA_PATH.read_text()
    with get_connection(db_path) as conn:
        # Migrate places to normalized schema BEFORE running executescript so that
        # the rename happens first and the new schema creates fresh tables afterwards.
        db_tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "place_articles" not in db_tables:
            _migrate_places_normalize(conn)

        conn.executescript(schema)
        # Migrate existing DBs: add columns introduced after initial schema
        existing = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
        migrations = [
            ("locations", "ALTER TABLE articles ADD COLUMN locations TEXT"),
            ("urls", "ALTER TABLE articles ADD COLUMN urls TEXT"),
            ("article_group", "ALTER TABLE articles ADD COLUMN article_group TEXT"),
            ("page_number", "ALTER TABLE articles ADD COLUMN page_number INTEGER"),
            ("section", "ALTER TABLE articles ADD COLUMN section TEXT"),
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
            filename, scan_date, newspaper, section, article_date, page,
            headline, summary, category, tags,
            full_text, image_path, thumb_path,
            ocr_confidence, needs_review, meta_source,
            locations, urls,
            article_group, page_number
        ) VALUES (
            :filename, :scan_date, :newspaper, :section, :article_date, :page,
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
    data.setdefault("section", None)
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


def delete_article(article_id: int, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Delete an article; related books/recipes/places cascade via FK."""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))


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
    section: str = "",
    date_from: str = "",
    date_to: str = "",
    location: str = "",
    country: str = "",
    needs_review: Optional[bool] = None,
    sort: str = "date_desc",
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
    if section:
        sql += f" {prefix}section = ?"
        params.append(section)
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
    if country:
        loc_col = "a.locations" if q else "locations"
        sql += f' AND {loc_col} LIKE ?'
        params.append(f'%"{country}"%')

    _sort_map = {
        "date_desc":    ("a.article_date", "article_date", "DESC"),
        "date_asc":     ("a.article_date", "article_date", "ASC"),
        "headline_asc": ("a.headline",     "headline",     "ASC"),
        "id_desc":      ("a.id",           "id",           "DESC"),
    }
    fts_col, plain_col, direction = _sort_map.get(sort, _sort_map["date_desc"])
    order_col = fts_col if q else plain_col
    sql += f" ORDER BY {order_col} {direction} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


_PLACE_FIELDS = frozenset({
    "name", "address", "postal_code", "city", "country", "phone", "hours", "url", "lat", "lng"
})
_PA_FIELDS = frozenset({"description", "rating"})

# Unicode apostrophe variants produced by OCR (curly quotes, prime, etc.)
_APOSTROPHE_VARIANTS = str.maketrans("\u2019\u2018\u201b\u02bc\u0060", "'''''")


def _make_key(s: str) -> str:
    """Normalize a place name or city to a deduplication key.

    Lowercases and collapses Unicode apostrophe variants to the standard
    ASCII apostrophe so that OCR artefacts (e.g. U+2019) do not prevent
    matching identical names.
    """
    return s.strip().lower().translate(_APOSTROPHE_VARIANTS)


def update_place(pa_id: int, fields: dict, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Update place fields by place_articles.id.

    Fields in _PLACE_FIELDS update the canonical places row (shared across
    all articles). Fields in _PA_FIELDS update only the place_articles row
    (article-specific description and rating).
    """
    if not fields:
        return
    place_fields = {k: v for k, v in fields.items() if k in _PLACE_FIELDS}
    pa_fields    = {k: v for k, v in fields.items() if k in _PA_FIELDS}
    with get_connection(db_path) as conn:
        if pa_fields:
            set_clause = ", ".join(f"{k} = :{k}" for k in pa_fields)
            pa_fields["_id"] = pa_id
            conn.execute(f"UPDATE place_articles SET {set_clause} WHERE id = :_id", pa_fields)
        if place_fields:
            row = conn.execute(
                "SELECT place_id FROM place_articles WHERE id = ?", (pa_id,)
            ).fetchone()
            if not row:
                return
            place_id = row["place_id"]
            # Keep name_key/city_key in sync when name or city changes
            if "name" in place_fields or "city" in place_fields:
                existing = conn.execute(
                    "SELECT name, city FROM places WHERE id = ?", (place_id,)
                ).fetchone()
                new_name = (place_fields.get("name") or existing["name"] or "")
                new_city = (place_fields.get("city") or existing["city"] or "")
                place_fields["name_key"] = _make_key(new_name)
                place_fields["city_key"]  = _make_key(new_city)
            set_clause = ", ".join(f"{k} = :{k}" for k in place_fields)
            place_fields["_id"] = place_id
            conn.execute(f"UPDATE places SET {set_clause} WHERE id = :_id", place_fields)


def update_place_coords(place_id: int, lat: float, lng: float,
                        db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Store geocoded coordinates for a canonical place (places.id)."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE places SET lat = ?, lng = ? WHERE id = ?",
            (lat, lng, place_id),
        )


def get_places_without_coords(db_path: Path = _DEFAULT_DB_PATH) -> list[dict]:
    """Return canonical places missing coordinates with a representative article link."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT p.id, p.name, p.address, p.postal_code, p.city, p.country,
                      (SELECT pa.article_id FROM place_articles pa WHERE pa.place_id = p.id
                       ORDER BY pa.id ASC LIMIT 1) AS article_id,
                      (SELECT a.headline FROM articles a
                       JOIN place_articles pa ON pa.article_id = a.id
                       WHERE pa.place_id = p.id ORDER BY pa.id ASC LIMIT 1) AS headline
               FROM places p WHERE p.lat IS NULL ORDER BY p.name"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_geocoded_places(
    query: str = "",
    city: str = "",
    country: str = "",
    db_path: Path = _DEFAULT_DB_PATH,
) -> list[dict]:
    """Return geocoded places with article info.

    Returns one row per article mention so the map JS can group by place.id
    and build multi-article popups for places referenced in several articles.
    """
    params: list = []
    sql = """
        SELECT p.id, p.name, p.city, p.country, p.lat, p.lng,
               pa.description, pa.rating, pa.article_id, a.headline
        FROM places p
        JOIN place_articles pa ON pa.place_id = p.id
        JOIN articles a ON a.id = pa.article_id
        WHERE p.lat IS NOT NULL AND p.lng IS NOT NULL
    """
    if query:
        q = f"%{query}%"
        sql += " AND (p.name LIKE ? OR p.city LIKE ? OR p.address LIKE ? OR p.country LIKE ?)"
        params.extend([q, q, q, q])
    if city:
        sql += " AND p.city LIKE ?"
        params.append(f"%{city}%")
    if country:
        sql += " AND p.country = ?"
        params.append(country)
    sql += " ORDER BY p.name"
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def delete_place(pa_id: int, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Delete a place_articles row by id.

    If this was the last article referencing the canonical place, the orphan
    cleanup trigger in the schema removes the places row automatically.
    """
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM place_articles WHERE id = ?", (pa_id,))


def get_place(pa_id: int, db_path: Path = _DEFAULT_DB_PATH) -> dict | None:
    """Return canonical place fields for a given place_articles.id, or None."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            """SELECT p.*, pa.id AS pa_id, pa.place_id
               FROM place_articles pa JOIN places p ON p.id = pa.place_id
               WHERE pa.id = ?""",
            (pa_id,),
        ).fetchone()
    return dict(row) if row else None


def get_all_places(
    query: str = "",
    city: str = "",
    country: str = "",
    sort: str = "country_asc",
    geocoded: str = "",
    db_path: Path = _DEFAULT_DB_PATH,
) -> list[dict]:
    """Return all canonical places with article count, optionally filtered and sorted."""
    _sort_map = {
        "name_asc":    "p.name ASC",
        "name_desc":   "p.name DESC",
        "city_asc":    "p.city ASC, p.name ASC",
        "city_desc":   "p.city DESC, p.name ASC",
        "country_asc": "p.country ASC, p.city ASC, p.name ASC",
        "country_desc":"p.country DESC, p.city ASC, p.name ASC",
    }
    order = _sort_map.get(sort, _sort_map["country_asc"])
    params: list = []
    sql = """
        SELECT p.*,
               COUNT(pa.id) AS article_count,
               (SELECT pa2.article_id FROM place_articles pa2
                WHERE pa2.place_id = p.id ORDER BY pa2.id ASC LIMIT 1) AS article_id,
               (SELECT a2.headline FROM articles a2
                JOIN place_articles pa2 ON pa2.article_id = a2.id
                WHERE pa2.place_id = p.id ORDER BY pa2.id ASC LIMIT 1) AS headline
        FROM places p
        JOIN place_articles pa ON pa.place_id = p.id
        WHERE 1=1
    """
    if query:
        q = f"%{query}%"
        sql += " AND (p.name LIKE ? OR p.city LIKE ? OR p.address LIKE ? OR p.country LIKE ?)"
        params.extend([q, q, q, q])
    if city:
        sql += " AND p.city LIKE ?"
        params.append(f"%{city}%")
    if country:
        sql += " AND p.country = ?"
        params.append(country)
    if geocoded == "geocoded":
        sql += " AND p.lat IS NOT NULL AND p.lng IS NOT NULL"
    elif geocoded == "not_geocoded":
        sql += " AND (p.lat IS NULL OR p.lng IS NULL)"
    sql += f" GROUP BY p.id ORDER BY {order}"
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def merge_places(source_id: int, target_id: int, db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Merge source canonical place into target, then delete source.

    All place_articles rows from source are reassigned to target.
    If an article already references target (UNIQUE conflict), the source row
    is dropped (keeping the target's description/rating).
    Missing place-level fields in target are filled from source.
    """
    if source_id == target_id:
        return
    with get_connection(db_path) as conn:
        # Fill in missing fields in target from source (never overwrite)
        for field in ("address", "postal_code", "city", "country", "phone", "hours", "url",
                      "lat", "lng"):
            conn.execute(
                f"UPDATE places SET {field} = (SELECT {field} FROM places WHERE id = ?) "
                f"WHERE id = ? AND {field} IS NULL",
                (source_id, target_id),
            )
        # Keep name_key/city_key in sync after potential city fill-in
        row = conn.execute("SELECT name, city FROM places WHERE id = ?", (target_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE places SET name_key = ?, city_key = ? WHERE id = ?",
                (_make_key(row["name"] or ""), _make_key(row["city"] or ""), target_id),
            )
        # Reassign article links; conflicts (same article already in target) are ignored
        conn.execute(
            "UPDATE OR IGNORE place_articles SET place_id = ? WHERE place_id = ?",
            (target_id, source_id),
        )
        # Drop any remaining source rows that conflicted (article already in target)
        conn.execute("DELETE FROM place_articles WHERE place_id = ?", (source_id,))
        # Delete the now-orphaned source canonical place
        conn.execute("DELETE FROM places WHERE id = ?", (source_id,))


def get_place_filter_options(country: str = "", db_path: Path = _DEFAULT_DB_PATH) -> dict:
    """Return distinct countries and cities for place filter dropdowns.

    If country is given, cities are restricted to that country.
    """
    with get_connection(db_path) as conn:
        countries = [r[0] for r in conn.execute(
            "SELECT DISTINCT country FROM places WHERE country IS NOT NULL ORDER BY country"
        ).fetchall()]
        if country:
            cities = [r[0] for r in conn.execute(
                "SELECT DISTINCT city FROM places WHERE city IS NOT NULL AND country = ? ORDER BY city",
                (country,),
            ).fetchall()]
        else:
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
    """Match-or-insert canonical places and link them to the article.

    For each place dict: if a place with the same (name, city) already exists,
    the canonical row is reused and any missing place-level fields are filled in.
    A new place_articles row is created for the article link (description, rating).
    """
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM place_articles WHERE article_id = ?", (article_id,))
        for p in places:
            name = (p.get("name") or "").strip()
            if not name:
                continue
            city  = (p.get("city") or "").strip()
            name_key = _make_key(name)
            city_key = _make_key(city)

            row = conn.execute(
                "SELECT id FROM places WHERE name_key = ? AND city_key = ?",
                (name_key, city_key),
            ).fetchone()

            if row:
                place_id = row["id"]
                # Fill in missing place-level fields only; never overwrite existing values
                for field in ("address", "postal_code", "city", "country",
                              "phone", "hours", "url"):
                    if p.get(field):
                        conn.execute(
                            f"UPDATE places SET {field} = ? WHERE id = ? AND {field} IS NULL",
                            (p[field], place_id),
                        )
            else:
                cur = conn.execute(
                    """INSERT INTO places
                       (name, address, postal_code, city, country,
                        phone, hours, url, name_key, city_key)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, p.get("address"), p.get("postal_code"), city or None,
                     p.get("country"), p.get("phone"), p.get("hours"), p.get("url"),
                     name_key, city_key),
                )
                place_id = cur.lastrowid

            conn.execute(
                "INSERT OR IGNORE INTO place_articles "
                "(place_id, article_id, description, rating) VALUES (?, ?, ?, ?)",
                (place_id, article_id, p.get("description"), p.get("rating")),
            )


def get_places(article_id: int, db_path: Path = _DEFAULT_DB_PATH) -> list[dict]:
    """Return all places linked to an article.

    Returns pa.id aliased as 'id' so existing templates and routes can use
    p.id as the form target without changes. Also exposes p.id as 'place_id'
    for the shared-place notice in the edit template.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT pa.id,
                      pa.article_id,
                      p.id AS place_id,
                      p.name, p.address, p.postal_code, p.city, p.country,
                      p.phone, p.hours, p.url, p.lat, p.lng,
                      pa.description, pa.rating
               FROM place_articles pa
               JOIN places p ON p.id = pa.place_id
               WHERE pa.article_id = ?
               ORDER BY p.name""",
            (article_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def insert_books(article_id: int, books: list[dict],
                 db_path: Path = _DEFAULT_DB_PATH) -> None:
    """Delete existing books for the article and insert the new list."""
    sql = """INSERT INTO books
             (article_id, title, author, publisher, year, pages, price, isbn, description, url)
             VALUES (:article_id, :title, :author, :publisher, :year, :pages, :price, :isbn, :description, :url)"""
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


def get_all_books(
    query: str = "",
    sort: str = "author_asc",
    db_path: Path = _DEFAULT_DB_PATH,
) -> list[dict]:
    """Return all books with article info, optionally filtered and sorted."""
    _sort_map = {
        "author_asc":  "b.author ASC, b.title ASC",
        "author_desc": "b.author DESC, b.title ASC",
        "title_asc":   "b.title ASC",
        "title_desc":  "b.title DESC",
        "year_asc":    "CAST(b.year AS INTEGER) ASC NULLS LAST, b.title ASC",
        "year_desc":   "CAST(b.year AS INTEGER) DESC NULLS LAST, b.title ASC",
    }
    order = _sort_map.get(sort, _sort_map["author_asc"])
    params: list = []
    sql = """
        SELECT b.*, a.id AS article_id, a.headline, a.article_date, a.newspaper
        FROM books b JOIN articles a ON a.id = b.article_id
        WHERE 1=1
    """
    if query:
        sql += " AND (b.title LIKE ? OR b.author LIKE ? OR b.publisher LIKE ?)"
        q = f"%{query}%"
        params.extend([q, q, q])
    sql += f" ORDER BY {order}"
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_all_recipes(
    query: str = "",
    sort: str = "category_asc",
    db_path: Path = _DEFAULT_DB_PATH,
) -> list[dict]:
    """Return all recipes with article info, optionally filtered and sorted."""
    _sort_map = {
        "category_asc":  "r.category ASC, r.name ASC",
        "category_desc": "r.category DESC, r.name ASC",
        "name_asc":      "r.name ASC",
        "name_desc":     "r.name DESC",
    }
    order = _sort_map.get(sort, _sort_map["category_asc"])
    params: list = []
    sql = """
        SELECT r.*, a.id AS article_id, a.headline, a.article_date, a.newspaper
        FROM recipes r JOIN articles a ON a.id = r.article_id
        WHERE 1=1
    """
    if query:
        sql += " AND (r.name LIKE ? OR r.category LIKE ?)"
        q = f"%{query}%"
        params.extend([q, q])
    sql += f" ORDER BY {order}"
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def sync_locations_from_places(article_id: int, db_path: Path = _DEFAULT_DB_PATH) -> list[str]:
    """Merge place cities into article.locations and return the updated list."""
    with get_connection(db_path) as conn:
        city_rows = conn.execute(
            """SELECT p.city FROM place_articles pa
               JOIN places p ON p.id = pa.place_id
               WHERE pa.article_id = ? AND p.city IS NOT NULL""",
            (article_id,),
        ).fetchall()
        existing_raw = conn.execute(
            "SELECT locations FROM articles WHERE id = ?", (article_id,)
        ).fetchone()

    cities = [r[0] for r in city_rows]
    existing: list[str] = []
    if existing_raw and existing_raw[0]:
        try:
            existing = json.loads(existing_raw[0])
        except (json.JSONDecodeError, TypeError):
            pass

    merged = list(dict.fromkeys(existing + cities))
    update_article(article_id, {"locations": merged}, db_path)
    return merged


def search_places(query: str, db_path: Path = _DEFAULT_DB_PATH) -> list[dict]:
    """Search canonical places by name, city, address, etc. (case-insensitive LIKE)."""
    q = f"%{query}%"
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT p.*,
                      pa.id AS pa_id, pa.description, pa.rating, pa.article_id,
                      a.headline, a.article_date, a.newspaper
               FROM places p
               JOIN place_articles pa ON pa.place_id = p.id
               JOIN articles a ON a.id = pa.article_id
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
        sections = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT section FROM articles "
                "WHERE section IS NOT NULL ORDER BY section"
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

    # Split into countries/regions and specific places (cities, landmarks, etc.)
    geo_regions = sorted(v for v in all_locations if v in _GEO_REGIONS)
    locations = sorted(v for v in all_locations if v not in _GEO_REGIONS)

    return {
        "newspapers": newspapers,
        "categories": categories,
        "sections": sections,
        "locations": locations,
        "geo_regions": geo_regions,
    }
