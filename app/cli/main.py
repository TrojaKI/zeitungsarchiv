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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

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


@cli.command()
def geocode():
    """Geocode all places that are missing coordinates (uses Nominatim/OSM)."""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    from app.db.database import get_places_without_coords
    from app.worker.geocoder import geocode_all_places

    pending = get_places_without_coords(_DB)
    if not pending:
        click.echo("All places already geocoded.")
        return
    click.echo(f"Geocoding {len(pending)} place(s)...")
    done = geocode_all_places(_DB)
    click.echo(f"Done: {done}/{len(pending)} geocoded successfully.")


@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8000, show_default=True)
def serve(host: str, port: int):
    """Start the web server (without Docker)."""
    import uvicorn
    uvicorn.run("app.web.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    cli()
