"""Click CLI for Zeitungsarchiv."""

import json
import os
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()

_DB = Path(os.getenv("DB_PATH", "./db/archive.db"))
_INBOX = Path(os.getenv("INBOX_DIR", "./inbox"))
_ARCHIVE = Path(os.getenv("ARCHIVE_DIR", "./archive"))


@click.group()
def cli():
    """Zeitungsarchiv — command line interface."""


@cli.command()
@click.argument("query")
@click.option("--category", default="", help="Filter by category")
@click.option("--newspaper", default="", help="Filter by newspaper name")
@click.option("--limit", default=20, show_default=True)
def search(query: str, category: str, newspaper: str, limit: int):
    """Full-text search in the archive."""
    from app.db.database import search_full

    results = search_full(
        query=query, category=category, newspaper=newspaper,
        limit=limit, db_path=_DB,
    )
    if not results:
        click.echo("No articles found.")
        return
    for r in results:
        date = r.get("article_date") or r.get("scan_date", "?")
        click.echo(f"[{r['id']:>4}] {date}  {r['headline'] or '(no headline)'}")


@cli.command()
@click.argument("article_id", type=int)
def show(article_id: int):
    """Show full details of a single article."""
    from app.db.database import get_article

    a = get_article(article_id, _DB)
    if a is None:
        click.echo(f"Article {article_id} not found.", err=True)
        raise SystemExit(1)

    tags = a.get("tags") or "[]"
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = []

    click.echo(f"ID:          {a['id']}")
    click.echo(f"Zeitung:     {a.get('newspaper') or '-'}")
    click.echo(f"Datum:       {a.get('article_date') or '-'}")
    click.echo(f"Schlagzeile: {a.get('headline') or '-'}")
    click.echo(f"Kategorie:   {a.get('category') or '-'}")
    click.echo(f"Tags:        {', '.join(tags) or '-'}")
    click.echo(f"OCR:         {a.get('ocr_confidence', 0):.1f}%")
    click.echo(f"Review:      {'ja' if a.get('needs_review') else 'nein'}")
    if a.get("summary"):
        click.echo(f"\n{a['summary']}")


@cli.command()
@click.option("--dir", "inbox_dir", default=None,
              help="Inbox directory (default: $INBOX_DIR or ./inbox)")
def process(inbox_dir: str | None):
    """Process all TIFF scans in the inbox directory."""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    from app.worker.ingestion import ingest_directory

    inbox = Path(inbox_dir) if inbox_dir else _INBOX
    ids = ingest_directory(inbox, _ARCHIVE, _DB)
    click.echo(f"Done: {len(ids)} article(s) ingested.")


@cli.command()
def stats():
    """Show archive statistics."""
    from app.db.database import get_stats

    s = get_stats(_DB)
    click.echo(f"Artikel gesamt:  {s['total']}")
    click.echo(f"Zur Review:      {s['needs_review']}")
    if s["by_newspaper"]:
        click.echo("\nNach Zeitung:")
        for row in s["by_newspaper"]:
            click.echo(f"  {row['newspaper']:<35} {row['cnt']:>4}")
    if s["by_category"]:
        click.echo("\nNach Kategorie:")
        for row in s["by_category"]:
            click.echo(f"  {row['category']:<20} {row['cnt']:>4}")


@cli.command()
@click.option("--format", "fmt", default="csv",
              type=click.Choice(["csv", "json", "sql"]), show_default=True)
@click.option("--output", "-o", default=None, help="Output file (default: stdout)")
def export(fmt: str, output: str | None):
    """Export all articles as CSV, JSON, or SQL dump."""
    import csv
    import sqlite3
    import sys

    from app.db.database import search_full

    if fmt == "sql":
        if not _DB.exists():
            click.echo(f"Database not found: {_DB}", err=True)
            raise SystemExit(1)
        con = sqlite3.connect(_DB)
        out = open(output, "w", encoding="utf-8") if output else sys.stdout
        try:
            for line in con.iterdump():
                out.write(line + "\n")
        finally:
            con.close()
            if output:
                out.close()
        if output:
            click.echo(f"SQL dump written to {output}")
        return

    articles = search_full(limit=100_000, db_path=_DB)
    out = open(output, "w", encoding="utf-8") if output else sys.stdout

    try:
        if fmt == "json":
            json.dump(articles, out, ensure_ascii=False, indent=2)
        else:
            fields = [
                "id", "filename", "scan_date", "newspaper", "article_date", "page",
                "headline", "summary", "category", "tags", "ocr_confidence",
                "needs_review", "meta_source", "created_at",
            ]
            writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(articles)
    finally:
        if output:
            out.close()

    if output:
        click.echo(f"Exported {len(articles)} article(s) to {output}")


@cli.command()
@click.option("--output", "-o", default=None,
              help="Destination path (default: db/archive_backup_YYYYMMDD_HHMMSS.db)")
def backup(output: str | None):
    """Create a timestamped backup of the SQLite database."""
    import sqlite3
    from datetime import datetime

    src = _DB
    if not src.exists():
        click.echo(f"Database not found: {src}", err=True)
        raise SystemExit(1)

    if output:
        dest = Path(output)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = src.parent / f"archive_backup_{stamp}.db"

    dest.parent.mkdir(parents=True, exist_ok=True)

    src_con = sqlite3.connect(src)
    dst_con = sqlite3.connect(dest)
    with dst_con:
        src_con.backup(dst_con)
    dst_con.close()
    src_con.close()

    size_kb = dest.stat().st_size // 1024
    click.echo(f"Backup written to {dest}  ({size_kb} KB)")


@cli.command("enrich-books")
def enrich_books():
    """Look up Open Library URLs for all books that have none."""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    from app.db.database import get_all_books, update_book
    from app.worker.books import lookup_book_url

    books = [b for b in get_all_books(db_path=_DB) if not b.get("url")]
    if not books:
        click.echo("All books already have a URL.")
        return

    click.echo(f"Looking up URLs for {len(books)} book(s)...")
    found = 0
    for b in books:
        url = lookup_book_url(b)
        if url:
            update_book(b["id"], {"url": url}, db_path=_DB)
            click.echo(f"  [{b['id']}] {b.get('title') or '?'}  →  {url}")
            found += 1
        else:
            click.echo(f"  [{b['id']}] {b.get('title') or '?'}  →  not found")
    click.echo(f"Done: {found}/{len(books)} URLs found.")


@cli.command("enrich-pages")
@click.option("--archive-dir", default=None, help="Archive directory (default: ./archive)")
def enrich_pages(archive_dir: str | None):
    """Backfill page numbers for articles where the field is NULL.

    Re-runs margin OCR (PSM 11) on the original TIFF and asks the LLM to extract
    the page number from the header/footer text.
    """
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s  %(levelname)-8s  %(message)s")

    from app.db.database import get_connection, get_article, update_article
    from app.worker.metadata import extract_metadata
    from app.worker.ocr import _extract_margin_text
    from app.worker.preprocess import preprocess

    _archive = Path(archive_dir) if archive_dir else _ARCHIVE

    # Fetch all articles missing a page number
    with get_connection(_DB) as conn:
        rows = conn.execute(
            "SELECT id, filename, full_text FROM articles WHERE page IS NULL ORDER BY id"
        ).fetchall()

    if not rows:
        click.echo("All articles already have a page number.")
        return

    click.echo(f"Processing {len(rows)} article(s) without page number...")
    found = 0
    for row in rows:
        article_id: int = row["id"]
        stem = Path(row["filename"]).stem
        tiff_path = _archive / stem / "original.tif"

        if not tiff_path.exists():
            click.echo(f"  [{article_id}] TIFF not found: {tiff_path}  →  skipped")
            continue

        try:
            pre = preprocess(tiff_path, _archive)
            margin_text = _extract_margin_text(pre["binary"])
        except Exception as exc:
            click.echo(f"  [{article_id}] Preprocessing failed: {exc}  →  skipped")
            continue

        if not margin_text:
            click.echo(f"  [{article_id}] No margin text found  →  skipped")
            continue

        metadata = extract_metadata(row["full_text"] or "", margin_text)
        page = metadata.get("page")

        if page:
            update_article(article_id, {"page": page}, db_path=_DB)
            click.echo(f"  [{article_id}] {stem}  →  page {page!r}")
            found += 1
        else:
            click.echo(f"  [{article_id}] {stem}  →  not found (margin: {margin_text!r})")

    click.echo(f"Done: {found}/{len(rows)} page numbers found.")


@cli.command()
def geocode():
    """Geocode all places that are missing coordinates (uses Nominatim/OSM)."""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    from app.db.database import get_places_without_coords
    from app.worker.geocoder import geocode_all_places

    pending = get_places_without_coords(_DB)
    if not pending:
        click.echo("All places already geocoded.")
        return
    click.echo(f"Geocoding {len(pending)} place(s)...")
    done = geocode_all_places(_DB)
    click.echo(f"Done: {done}/{len(pending)} geocoded successfully.")


@cli.command("sync-locations")
def sync_locations():
    """Backfill articles.locations with city names from the places table."""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    from app.db.database import get_connection, sync_locations_from_places

    with get_connection(_DB) as conn:
        rows = conn.execute(
            """SELECT DISTINCT pa.article_id FROM place_articles pa
               JOIN places p ON p.id = pa.place_id
               WHERE p.city IS NOT NULL"""
        ).fetchall()

    article_ids = [r[0] for r in rows]
    if not article_ids:
        click.echo("No places with cities found.")
        return

    click.echo(f"Syncing locations for {len(article_ids)} article(s)...")
    updated = 0
    for aid in article_ids:
        merged = sync_locations_from_places(aid, _DB)
        if merged:
            click.echo(f"  [article {aid}] locations: {merged}")
            updated += 1
    click.echo(f"Done: {updated}/{len(article_ids)} articles updated.")


@cli.command()
@click.argument("parts", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--output", "-o", default=None, help="Output TIFF path (default: <first>_stitched.tif)")
def stitch(parts: tuple[str, ...], output: str | None):
    """Stitch 2+ scan parts into one TIFF using Hugin.

    \b
    Example workflow for a multi-page article with some pages scanned in two passes:
      zeitungsarchiv stitch scan002.tif scan003.tif -o inbox/article_p02.tif
      zeitungsarchiv stitch scan004.tif scan005.tif -o inbox/article_p03.tif
      cp scan001.tif inbox/article_p01.tif
      zeitungsarchiv process
    """
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    from app.worker.stitch import stitch_multipart

    part_paths = [Path(p) for p in parts]
    if len(part_paths) < 2:
        raise click.UsageError("At least 2 input files required.")

    if output:
        out_path = Path(output)
    else:
        out_path = part_paths[0].parent / f"{part_paths[0].stem}_stitched.tif"

    click.echo(f"Stitching {len(part_paths)} parts → {out_path.name} ...")
    try:
        result = stitch_multipart(part_paths, out_path)
        click.echo(f"Done: {result}")
    except RuntimeError as e:
        raise click.ClickException(str(e))


@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8000, show_default=True)
def serve(host: str, port: int):
    """Start the web server (without Docker)."""
    import uvicorn
    uvicorn.run("app.web.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    cli()
