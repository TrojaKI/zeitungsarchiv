-- SQLite schema for Zeitungsarchiv
-- Requires SQLite 3.35+ with FTS5 support

-- Main table: articles
CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,          -- original filename
    scan_date       TEXT NOT NULL,          -- ISO 8601: YYYY-MM-DD

    -- Metadata (automatic or manual)
    newspaper       TEXT,                   -- e.g. "Kurier"
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

    -- Quality & status
    ocr_confidence  REAL,                   -- Tesseract confidence 0.0–100.0
    needs_review    INTEGER DEFAULT 0,      -- 1 = manual review required
    meta_source     TEXT DEFAULT 'auto',    -- 'auto' | 'manual' | 'partial'
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
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
