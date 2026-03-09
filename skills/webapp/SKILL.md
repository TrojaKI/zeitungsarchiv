---
name: webapp
description: >
  Implementiere die FastAPI + HTMX WebApp und Click-CLI für das
  Zeitungsarchiv. Verwende diesen Skill bei WebApp, Frontend, Suche,
  Review-Interface, CLI-Befehle, Export, und Docker-Setup.
---

# Skill: WebApp (FastAPI + HTMX) + CLI

## Abhängigkeiten

```bash
pip install fastapi uvicorn jinja2 python-multipart click aiofiles
```

## Projektstruktur

```
app/web/
├── main.py           ← FastAPI App + Startup
├── routes/
│   ├── search.py     ← GET /search
│   ├── articles.py   ← GET/POST /articles/{id}
│   ├── review.py     ← GET /review
│   └── admin.py      ← POST /process, GET /stats, GET /export
├── templates/
│   ├── base.html     ← Layout mit HTMX-Import
│   ├── index.html    ← Startseite + Suchmaske
│   ├── search_results.html  ← HTMX-Fragment
│   ├── article.html  ← Detailansicht
│   ├── edit.html     ← Metadaten-Editor
│   └── review.html   ← Review-Queue
└── static/
    └── style.css     ← Minimales CSS
```

## FastAPI App (Grundgerüst)

```python
# app/web/main.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Zeitungsarchiv")
templates = Jinja2Templates(directory="app/web/templates")
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
app.mount("/archive", StaticFiles(directory="/app/archive"), name="archive")

# Routes einbinden
from app.web.routes import search, articles, review, admin
app.include_router(search.router)
app.include_router(articles.router)
app.include_router(review.router)
app.include_router(admin.router)
```

## Suchroute mit FTS5

```python
# app/web/routes/search.py
@router.get("/search")
async def search(request: Request, q: str = "", newspaper: str = "",
                 category: str = "", date_from: str = "", date_to: str = ""):

    results = db.search(q=q, newspaper=newspaper, category=category,
                        date_from=date_from, date_to=date_to, limit=20)

    # HTMX: nur Fragment zurückgeben wenn hx-request
    if request.headers.get("hx-request"):
        return templates.TemplateResponse("search_results.html",
                                          {"request": request, "results": results})
    return templates.TemplateResponse("index.html",
                                      {"request": request, "results": results, "q": q})
```

## SQLite FTS5 Suchabfrage

```python
# app/db/database.py
def search(q: str, newspaper: str = "", category: str = "",
           date_from: str = "", date_to: str = "", limit: int = 20) -> list:

    where = []
    params = []

    if q:
        # FTS5 Suche
        sql = """
          SELECT a.*, snippet(articles_fts, 2, '<mark>', '</mark>', '…', 20) as snippet
          FROM articles_fts
          JOIN articles a ON articles_fts.rowid = a.id
          WHERE articles_fts MATCH ?
        """
        params.append(q)
    else:
        sql = "SELECT *, '' as snippet FROM articles WHERE 1=1"

    if newspaper:
        where.append("a.newspaper = ?")
        params.append(newspaper)
    if category:
        where.append("a.category = ?")
        params.append(category)
    if date_from:
        where.append("a.article_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("a.article_date <= ?")
        params.append(date_to)

    if where:
        sql += " AND " + " AND ".join(where)

    sql += f" ORDER BY a.article_date DESC LIMIT {limit}"
    return db.execute(sql, params).fetchall()
```

## HTMX Template (Beispiel)

```html
<!-- base.html -->
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Zeitungsarchiv</title>
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <nav>
    <a href="/">Suche</a>
    <a href="/review">Review Queue {% if review_count %}<span class="badge">{{review_count}}</span>{% endif %}</a>
    <a href="/stats">Statistiken</a>
  </nav>
  {% block content %}{% endblock %}
</body>
</html>

<!-- index.html -->
{% extends "base.html" %}
{% block content %}
<form hx-get="/search" hx-target="#results" hx-trigger="input delay:300ms, submit">
  <input type="text" name="q" placeholder="Suche..." value="{{q}}" autofocus>
  <select name="category">
    <option value="">Alle Kategorien</option>
    <option>Politik</option><option>Wirtschaft</option>
    <option>Kultur</option><option>Sport</option>
    <option>Wissenschaft</option><option>Lokales</option>
    <option>International</option>
  </select>
  <input type="date" name="date_from"> bis <input type="date" name="date_to">
  <button type="submit">Suchen</button>
</form>
<div id="results">{% include "search_results.html" %}</div>
{% endblock %}
```

## CLI (Click)

```python
# app/cli/main.py
import click

@click.group()
def cli(): pass

@cli.command()
@click.argument("query")
@click.option("--category", default="")
def search(query, category):
    """Volltextsuche im Archiv."""
    results = db.search(q=query, category=category)
    for r in results:
        click.echo(f"[{r['id']}] {r['article_date']} — {r['headline']}")

@cli.command()
@click.argument("article_id", type=int)
def show(article_id):
    """Artikel-Details anzeigen."""
    article = db.get(article_id)
    click.echo(f"Zeitung:    {article['newspaper']}")
    click.echo(f"Datum:      {article['article_date']}")
    click.echo(f"Schlagzeile:{article['headline']}")
    click.echo(f"Kategorie:  {article['category']}")
    click.echo(f"Tags:       {article['tags']}")
    click.echo(f"\n{article['summary']}")

@cli.command()
@click.option("--dir", "inbox_dir", default="./inbox")
def process(inbox_dir):
    """Scans aus Verzeichnis verarbeiten."""
    from app.worker.pipeline import process_directory
    count = process_directory(inbox_dir)
    click.echo(f"{count} Artikel verarbeitet.")

@cli.command()
@click.option("--format", "fmt", default="csv", type=click.Choice(["csv","json"]))
def export(fmt):
    """Archiv exportieren."""
    ...
```

## Docker-Start

```dockerfile
# Im Dockerfile: beide Services in einem Container
CMD ["sh", "-c", "python -m app.worker.watcher & uvicorn app.web.main:app --host 0.0.0.0 --port 8000"]
```

## Typische Fehler

| Problem | Lösung |
|---------|--------|
| HTMX-Fragment nicht erkannt | `hx-request`-Header prüfen |
| Bilder nicht sichtbar | `/archive` StaticFiles-Mount prüfen |
| FTS5 Sonderzeichen | Suchterm escapen: `q.replace('"', '')` |
| Worker + Web gleichzeitig | Supervisor oder `&` im CMD |
