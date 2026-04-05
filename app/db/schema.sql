-- SQLite schema for Zeitungsarchiv
-- Requires SQLite 3.35+ with FTS5 support

-- Main table: articles
CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,          -- original filename
    scan_date       TEXT NOT NULL,          -- ISO 8601: YYYY-MM-DD

    -- Metadata (automatic or manual)
    newspaper       TEXT,                   -- e.g. "Kurier"
    section         TEXT,                   -- supplement/section name (e.g. "freizeit.at", "Plus/Minus")
    article_date    TEXT,                   -- publication date of the article
    page            TEXT,                   -- page number (optional)
    headline        TEXT,                   -- article headline
    summary         TEXT,                   -- AI summary (2-3 sentences)
    category        TEXT,                   -- Politik, Kultur, Sport, Wirtschaft, ...
    tags            TEXT,                   -- JSON array: '["tag1","tag2"]'

    -- Content
    full_text       TEXT,                   -- OCR full text
    image_path      TEXT,                   -- path to WebP archive image
    thumb_path      TEXT,                   -- path to JPEG thumbnail
    locations       TEXT,                   -- JSON array of place names mentioned
    urls            TEXT,                   -- JSON array of URLs mentioned

    -- Quality & status
    ocr_confidence  REAL,                   -- Tesseract confidence 0.0–100.0
    needs_review    INTEGER DEFAULT 0,      -- 1 = manual review required
    meta_source     TEXT DEFAULT 'auto',    -- 'auto' | 'manual' | 'partial'
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),

    -- Multi-page article grouping (_pNN convention)
    article_group   TEXT,                   -- e.g. "sternlicht_oase" (null for single-page)
    page_number     INTEGER                 -- 1-based page index (null for single-page)
);

-- Full-text index (SQLite FTS5) for fast search
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    headline,
    summary,
    full_text,
    tags,
    content='articles',
    content_rowid='id',
    tokenize='unicode61'
);

-- Trigger: keep FTS index in sync on INSERT
CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, headline, summary, full_text, tags)
    VALUES (new.id, new.headline, new.summary, new.full_text, new.tags);
END;

-- Trigger: keep FTS index in sync on UPDATE
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, headline, summary, full_text, tags)
    VALUES ('delete', old.id, old.headline, old.summary, old.full_text, old.tags);
    INSERT INTO articles_fts(rowid, headline, summary, full_text, tags)
    VALUES (new.id, new.headline, new.summary, new.full_text, new.tags);
END;

-- Trigger: keep FTS index in sync on DELETE
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, headline, summary, full_text, tags)
    VALUES ('delete', old.id, old.headline, old.summary, old.full_text, old.tags);
END;

-- Trigger: auto-update updated_at on every UPDATE
CREATE TRIGGER IF NOT EXISTS articles_updated_at AFTER UPDATE ON articles BEGIN
    UPDATE articles SET updated_at = datetime('now') WHERE id = new.id;
END;

-- Books/publications recommended in articles
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
    description TEXT,
    url         TEXT            -- link to publisher or bookshop (e.g. Thalia, Amazon)
);

-- Recipes published in articles
CREATE TABLE IF NOT EXISTS recipes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    name         TEXT,
    category     TEXT,
    servings     TEXT,
    prep_time    TEXT,
    ingredients  TEXT,   -- free-text block
    instructions TEXT    -- free-text block
);

-- Canonical physical place (one row per real-world location)
CREATE TABLE IF NOT EXISTS places (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    address     TEXT,
    postal_code TEXT,
    city        TEXT,
    country     TEXT,
    phone       TEXT,
    hours       TEXT,
    url         TEXT,
    lat         REAL,           -- WGS84 latitude (from Nominatim geocoding)
    lng         REAL,           -- WGS84 longitude (from Nominatim geocoding)
    geocode_source TEXT,        -- 'nominatim' | 'manual' | NULL = unbekannt/verdächtig
    name_key    TEXT NOT NULL,  -- LOWER(TRIM(name)) for deduplication
    city_key    TEXT NOT NULL   -- LOWER(TRIM(COALESCE(city,''))) for deduplication
);

-- Unique constraint: one canonical row per (name, city) combination
CREATE UNIQUE INDEX IF NOT EXISTS places_dedup ON places (name_key, city_key);

-- Article-specific mention: description and rating come from the article context
CREATE TABLE IF NOT EXISTS place_articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id    INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    description TEXT,           -- how this article describes the place
    rating      TEXT,           -- "+", "-", "+/-", or NULL
    UNIQUE (place_id, article_id)
);

CREATE INDEX IF NOT EXISTS place_articles_article ON place_articles (article_id);
CREATE INDEX IF NOT EXISTS place_articles_place   ON place_articles (place_id);

-- Auto-delete orphaned places when the last article reference is removed
CREATE TRIGGER IF NOT EXISTS place_articles_cleanup
AFTER DELETE ON place_articles
BEGIN
    DELETE FROM places WHERE id = OLD.place_id
      AND NOT EXISTS (SELECT 1 FROM place_articles WHERE place_id = OLD.place_id);
END;
