# Manual Address Entry — Design Spec

**Date:** 2026-05-09  
**Status:** Approved

## Context

The /places view currently only shows places extracted automatically from scanned articles via LLM. The user wants to manually add addresses (e.g., restaurants, bars, shops) without any associated scan. Manual entries must appear in the same /places list, be globally searchable, and be clearly labelled as "manuell" in the article column.

## Approach: `source` column on `places`

Add a `source` column to `places` to distinguish manually entered records from article-derived ones. Manual places have no `place_articles` row. The auto-cleanup trigger is scoped to `source='article'` only.

## Schema Changes (`app/db/schema.sql`)

Two new columns on the `places` table (applied as migration):

```sql
ALTER TABLE places ADD COLUMN source TEXT NOT NULL DEFAULT 'article';
-- values: 'article' | 'manual'

ALTER TABLE places ADD COLUMN description TEXT;
-- canonical description/notes; used by manual entries (article-based description stays in place_articles)
```

Updated auto-cleanup trigger — only deletes article-sourced orphan places:

```sql
DROP TRIGGER IF EXISTS place_articles_cleanup;

CREATE TRIGGER place_articles_cleanup
AFTER DELETE ON place_articles
BEGIN
    DELETE FROM places
    WHERE id = OLD.place_id
      AND source = 'article'
      AND NOT EXISTS (
          SELECT 1 FROM place_articles WHERE place_id = OLD.place_id
      );
END;
```

## Database Layer (`app/db/database.py`)

### New function: `insert_manual_place`

```python
def insert_manual_place(fields: dict) -> int:
    """Insert a manually entered place. Returns canonical place.id."""
```

- Inserts into `places` with `source='manual'`
- Sets `name_key = LOWER(TRIM(name))`, `city_key = LOWER(TRIM(city or ''))`
- Returns the new `places.id`
- Raises `ValueError` if a place with the same (name_key, city_key) already exists

### New function: `get_manual_place`

```python
def get_manual_place(place_id: int) -> dict | None:
    """Fetch a manual place by places.id."""
```

### New function: `update_manual_place`

```python
def update_manual_place(place_id: int, fields: dict) -> None:
    """Update canonical fields of a manual place."""
```

### Modified: `get_all_places`

- Change `JOIN place_articles` → `LEFT JOIN place_articles`
- Manual places appear with `article_count=0`, `articles=[]`
- Add `source` to returned fields so templates can differentiate

### Modified: `get_geocoded_places`

- Same LEFT JOIN change so manual geocoded places appear on the map

## Routes (`app/web/routes/places.py`)

### Existing routes (unchanged)

`/places/{place_id}` — operates on `place_articles.id`, article-based places only.

### New routes for manual places

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/places/new-form` | Returns HTMX partial: inline create form |
| `POST` | `/places/create` | Insert new manual place, redirect to /places |
| `POST` | `/places/manual/{place_id}` | Update manual place fields (inline edit, same pattern as article-based) |
| `POST` | `/places/manual/{place_id}/delete` | Delete manual place |
| `POST` | `/places/manual/{place_id}/geocode` | Trigger Nominatim geocoding |
| `POST` | `/places/manual/{place_id}/confirm-coords` | Mark coordinates as manually confirmed |

All `/places/manual/` routes look up by `places.id` directly (no place_articles indirection).

## Frontend (`app/web/templates/`)

### `places.html` — add button + form container

Above the results div:

```html
<button hx-get="/places/new-form"
        hx-target="#manual-place-form"
        hx-swap="innerHTML">
  + Ort hinzufügen
</button>
<div id="manual-place-form"></div>
```

### New partial: `places_new_form.html`

Inline form with fields:

- Name* (required, text input)
- Beschreibung (textarea, ~3 rows)
- Adresse (text)
- PLZ (text, short)
- Stadt (text)
- Land (text, default "Österreich")
- Telefon (text)
- Öffnungszeiten (text)
- URL (text)
- [Abbrechen] [Speichern]

Abbrechen: `hx-get` clears `#manual-place-form`. Speichern: `POST /places/create`, on success triggers `hx-get="/places"` to reload results.

### `places_results.html` — article column for manual entries

```html
{% if place.source == 'manual' %}
  <span class="badge">manuell</span>
{% else %}
  {# existing article links #}
{% endif %}
```

Description shown as subtitle under the place name (same position as article-based places show their `place_articles.description`).

## Geocoding

Manual places use the same Nominatim geocoder as article-based places. After creation, the user can click the geocode button in the list (same ⚠️ icon flow). The `/places/manual/{place_id}/geocode` route calls `geocode_place(place_id)` and updates `places.lat`, `places.lng`, `places.geocode_source`.

## Search

`search_places()` and `get_all_places()` query the `places` table directly — manual places are included automatically once the LEFT JOIN is in place. No changes needed to the FTS5 search (articles table is separate).

## Out of Scope

- Linking a manual place to an article after the fact (possible future feature)
- Import from CSV / external sources
- Place type/category field (explicitly excluded per user decision)

## Verification

1. Start server: `ARCHIVE_DIR=$(pwd)/archive DB_PATH=$(pwd)/db/archive.db uvicorn app.web.main:app --host 0.0.0.0 --port 8000`
2. Open `/places` — "+ Ort hinzufügen" button visible above table
3. Fill form (Name required, rest optional), save → entry appears in table with "manuell" badge
4. Entry survives page reload (persisted in DB, `source='manual'`)
5. Geocode button triggers Nominatim → lat/lng populated, icon switches from ⚠️ to 📍
6. Edit fields via existing edit flow at `/places/manual/{id}`
7. Delete works, entry removed, no orphan in `places`
8. Search box finds manual entries alongside article-based ones
9. Map tab shows manual geocoded places as markers
10. Existing article-based places: no regression, trigger still auto-cleans orphans
