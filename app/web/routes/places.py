"""Places routes: list/search all places, edit/delete individual place."""

import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.db.database import (delete_place, get_all_places, get_geocoded_places,
                              get_place, get_place_filter_options, get_review_count,
                              merge_places, update_place, update_place_coords)
from app.web.templating import templates as _templates

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


def _ctx(request: Request, **kwargs) -> dict:
    return {"request": request, "review_count": get_review_count(_DB), **kwargs}


@router.get("/places/cities", response_class=HTMLResponse)
async def places_cities(country: str = ""):
    """Return city <option> elements filtered by country for HTMX dropdown update."""
    opts = get_place_filter_options(country=country, db_path=_DB)
    options = '<option value="">Alle Orte</option>'
    for c in opts["cities"]:
        options += f'<option value="{c}">{c}</option>'
    return HTMLResponse(options)


@router.get("/places", response_class=HTMLResponse)
async def places_list(
    request: Request,
    q: str = "",
    city: str = "",
    country: str = "",
    sort: str = "country_asc",
    geocoded: str = "",
):
    places = get_all_places(query=q, city=city, country=country, sort=sort,
                            geocoded=geocoded, db_path=_DB)
    opts = get_place_filter_options(country=country, db_path=_DB)
    ctx = _ctx(request, places=places, q=q, city=city, country=country, sort=sort,
               geocoded=geocoded, **opts)
    # HTMX partial request: return only the results fragment
    if request.headers.get("hx-request"):
        return _templates.TemplateResponse(request, "places_results.html", ctx)
    return _templates.TemplateResponse(request, "places.html", ctx)


@router.get("/places/map-data", response_class=JSONResponse)
async def places_map_data(q: str = "", city: str = "", country: str = "", geocoded: str = ""):
    """Return geocoded places as JSON for the map view, respecting active filters."""
    places = get_geocoded_places(query=q, city=city, country=country, geocoded=geocoded, db_path=_DB)
    return JSONResponse(content=places)


@router.post("/places/{place_id}")
async def place_update(
    place_id: int,
    article_id: int = Form(...),
    name: str = Form(""),
    description: str = Form(""),
    address: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    country: str = Form(""),
    phone: str = Form(""),
    hours: str = Form(""),
    url: str = Form(""),
    rating: str = Form(""),
    lat: str = Form(""),
    lng: str = Form(""),
):
    fields: dict = {
        "name":        name or None,
        "description": description or None,
        "address":     address or None,
        "postal_code": postal_code or None,
        "city":        city or None,
        "country":     country or None,
        "phone":       phone or None,
        "hours":       hours or None,
        "url":         url or None,
        "rating":      rating or None,
    }
    try:
        if lat:
            fields["lat"] = float(lat)
        if lng:
            fields["lng"] = float(lng)
        if lat or lng:
            fields["geocode_source"] = "manual"
    except ValueError:
        pass
    try:
        update_place(place_id, fields, _DB)
    except sqlite3.IntegrityError:
        return HTMLResponse(
            "Ein Ort mit diesem Namen und dieser Stadt existiert bereits. "
            "Bitte die Orte zusammenführen.",
            status_code=409,
        )
    return RedirectResponse(f"/articles/{article_id}/edit", status_code=303)


@router.post("/places/{place_id}/geocode", response_class=HTMLResponse)
async def place_geocode(place_id: int):
    """Trigger Nominatim geocoding for a single place and return a status fragment.

    place_id refers to place_articles.id; geocoding updates the canonical places row.
    """
    from app.worker.geocoder import geocode_place as _geocode
    # get_place resolves pa_id to the canonical place fields
    place = get_place(place_id, _DB)
    if not place:
        return HTMLResponse('<span class="geo-error">Ort nicht gefunden</span>')
    coords = _geocode(place)
    if coords:
        lat, lng = coords
        # Update the canonical places row using places.id (place["id"])
        update_place_coords(place["id"], lat, lng, db_path=_DB)
        # OOB swaps keep the form inputs in sync so a subsequent save does not
        # overwrite the freshly geocoded coordinates with stale form values.
        return HTMLResponse(
            f'<span class="geo-ok">&#x1F4CD; {lat:.7f}, {lng:.7f}</span>'
            f'<input type="number" step="0.0000001" name="lat"'
            f' id="lat-{place_id}" value="{lat:.7f}" hx-swap-oob="true">'
            f'<input type="number" step="0.0000001" name="lng"'
            f' id="lng-{place_id}" value="{lng:.7f}" hx-swap-oob="true">'
        )
    return HTMLResponse('<span class="geo-error">Kein Ergebnis von Nominatim</span>')


@router.post("/places/{place_id}/delete")
async def place_delete(place_id: int, article_id: int = Form(...)):
    delete_place(place_id, _DB)
    return RedirectResponse(f"/articles/{article_id}/edit", status_code=303)


@router.get("/places/canonical/{canonical_id}/merge-candidates", response_class=HTMLResponse)
async def place_merge_candidates(canonical_id: int):
    """Return <option> elements for merge target candidates (canonical places.id)."""
    from app.db.database import get_connection
    with get_connection(_DB) as conn:
        row = conn.execute(
            "SELECT name_key FROM places WHERE id = ?", (canonical_id,)
        ).fetchone()
        if not row:
            return HTMLResponse('<option value="">Kein Eintrag gefunden</option>')
        name_key = row["name_key"]
        candidates = conn.execute(
            """SELECT p.id, p.name, p.city, COUNT(pa.id) AS article_count
               FROM places p JOIN place_articles pa ON pa.place_id = p.id
               WHERE p.id != ?
                 AND (p.name_key LIKE ? OR ? LIKE '%' || p.name_key || '%')
               GROUP BY p.id ORDER BY p.name""",
            (canonical_id, f"%{name_key}%", name_key),
        ).fetchall()
    if not candidates:
        return HTMLResponse('<option value="">Keine ähnlichen Einträge gefunden</option>')
    opts = '<option value="">Bitte wählen…</option>'
    for c in candidates:
        label = c["name"] + (f" ({c['city']})" if c["city"] else "")
        label += f" – {c['article_count']} Artikel"
        opts += f'<option value="{c["id"]}">{label}</option>'
    return HTMLResponse(opts)


@router.post("/places/canonical/{canonical_id}/confirm-coords", response_class=HTMLResponse)
async def place_confirm_coords(canonical_id: int):
    """Mark a place's existing coordinates as manually confirmed (removes from suspect list)."""
    from app.db.database import confirm_place_coords
    confirm_place_coords(canonical_id, _DB)
    return HTMLResponse("")


@router.post("/places/canonical/{canonical_id}/merge")
async def place_merge(canonical_id: int, target_place_id: int = Form(...)):
    """Merge canonical_id into target_place_id and refresh the places list."""
    if canonical_id == target_place_id or not target_place_id:
        from fastapi.responses import Response
        return Response(status_code=204)
    merge_places(canonical_id, target_place_id, _DB)
    from fastapi.responses import Response
    r = Response(status_code=204)
    r.headers["HX-Refresh"] = "true"
    return r
