"""Places routes: list/search all places, edit/delete individual place."""

import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response)
from markupsafe import escape

from app.db.database import (delete_manual_place, delete_place, get_all_places,
                              get_canonical_place, get_geocoded_places,
                              get_manual_place, get_place,
                              get_place_filter_options, get_review_count,
                              insert_manual_place, merge_places,
                              update_canonical_place, update_place,
                              update_place_coords)
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
        options += f'<option value="{escape(c)}">{escape(c)}</option>'
    return HTMLResponse(options)


@router.get("/places/states", response_class=HTMLResponse)
async def places_states():
    """Return state <option> elements for HTMX dropdown update."""
    opts = get_place_filter_options(db_path=_DB)
    options = '<option value="">Alle Bundesländer</option>'
    for s in opts["states"]:
        options += f'<option value="{escape(s)}">{escape(s)}</option>'
    return HTMLResponse(options)


_DUPLICATE_MESSAGE = "Ein Ort mit diesem Namen und dieser Stadt existiert bereits."


def _place_form(request: Request, place: dict | None = None,
                error: str = "") -> HTMLResponse:
    """Render the shared create/edit form partial for #manual-place-form."""
    return _templates.TemplateResponse(
        request, "place_form.html", {"request": request, "place": place, "error": error}
    )


def _refresh_places() -> Response:
    """Tell HTMX to reload the places list, like set-active and merge do."""
    response = Response(status_code=204)
    response.headers["HX-Refresh"] = "true"
    return response


def _coord_fields(lat: str, lng: str) -> dict:
    """Parse lat/lng form values; entering coordinates by hand counts as manual geocoding."""
    fields: dict = {}
    try:
        if lat:
            fields["lat"] = float(lat)
        if lng:
            fields["lng"] = float(lng)
    except ValueError:
        return {}
    if fields:
        fields["geocode_source"] = "manual"
    return fields


@router.get("/places/new-form", response_class=HTMLResponse)
async def places_new_form(request: Request):
    """Return the inline manual-place creation form partial."""
    return _place_form(request)


@router.post("/places/create")
async def place_create(
    request: Request,
    name: str = Form(""),
    description: str = Form(""),
    address: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    country: str = Form(""),
    phone: str = Form(""),
    hours: str = Form(""),
    url: str = Form(""),
):
    submitted = {"name": name, "description": description, "address": address,
                 "postal_code": postal_code, "city": city, "country": country,
                 "phone": phone, "hours": hours, "url": url}
    if not name.strip():
        return _place_form(request, submitted, error="Name ist ein Pflichtfeld.")
    try:
        insert_manual_place(submitted, _DB)
    except sqlite3.IntegrityError:
        return _place_form(request, submitted, error=_DUPLICATE_MESSAGE)
    return _refresh_places()


@router.get("/places/canonical/{place_id}/edit-form", response_class=HTMLResponse)
async def place_edit_form(request: Request, place_id: int):
    """Return the inline edit form for a canonical place (manual or article-sourced)."""
    place = get_canonical_place(place_id, _DB)
    if not place:
        return HTMLResponse("Ort nicht gefunden", status_code=404)
    return _place_form(request, place)


@router.post("/places/canonical/{place_id}/update")
async def place_canonical_update(
    request: Request,
    place_id: int,
    name: str = Form(""),
    description: str = Form(""),
    address: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    country: str = Form(""),
    state: str = Form(""),
    phone: str = Form(""),
    hours: str = Form(""),
    url: str = Form(""),
    is_active: int = Form(1),
    lat: str = Form(""),
    lng: str = Form(""),
):
    """Update the shared master data of a place from the places list.

    Article-specific description and rating stay with the article — they are edited
    via /articles/{id}/edit.
    """
    existing = get_canonical_place(place_id, _DB)
    if not existing:
        return HTMLResponse("Ort nicht gefunden", status_code=404)
    fields: dict = {
        "name": name or None, "description": description or None,
        "address": address or None, "postal_code": postal_code or None,
        "city": city or None, "country": country or None,
        "state": state or None, "phone": phone or None,
        "hours": hours or None, "url": url or None, "is_active": is_active,
        **_coord_fields(lat, lng),
    }
    # On error re-render the form with what was typed, keeping id and source intact
    submitted = {**fields, "id": place_id, "source": existing["source"]}
    if not name.strip():
        return _place_form(request, submitted, error="Name ist ein Pflichtfeld.")
    try:
        update_canonical_place(place_id, fields, _DB)
    except sqlite3.IntegrityError:
        return _place_form(request, submitted, error=_DUPLICATE_MESSAGE)
    return _refresh_places()


@router.post("/places/manual/{place_id}/delete")
async def manual_place_delete(place_id: int):
    delete_manual_place(place_id, _DB)
    return RedirectResponse("/places", status_code=303)


@router.post("/places/manual/{place_id}/geocode", response_class=HTMLResponse)
def manual_place_geocode(place_id: int):
    # Sync handler: Nominatim call + rate-limit sleep must not block the event loop
    from app.worker.geocoder import geocode_place as _geocode
    from fastapi.responses import Response
    place = get_manual_place(place_id, _DB)
    if not place:
        return HTMLResponse('<span class="geo-error">Ort nicht gefunden</span>')
    result = _geocode(place)
    if result:
        update_place_coords(place_id, result["lat"], result["lng"],
                            state=result.get("state"), db_path=_DB)
        r = Response(status_code=204)
        r.headers["HX-Refresh"] = "true"
        return r
    return HTMLResponse('<span class="geo-error">Kein Ergebnis von Nominatim</span>')


@router.get("/places", response_class=HTMLResponse)
async def places_list(
    request: Request,
    q: str = "",
    city: str = "",
    country: str = "",
    state: str = "",
    is_active: str = "active",
    sort: str = "country_asc",
    geocoded: str = "",
):
    places = get_all_places(query=q, city=city, country=country, state=state,
                            is_active=is_active, sort=sort, geocoded=geocoded, db_path=_DB)
    opts = get_place_filter_options(country=country, db_path=_DB)
    ctx = _ctx(request, places=places, q=q, city=city, country=country, state=state,
               is_active=is_active, sort=sort, geocoded=geocoded, **opts)
    # HTMX partial request: return only the results fragment
    if request.headers.get("hx-request"):
        return _templates.TemplateResponse(request, "places_results.html", ctx)
    return _templates.TemplateResponse(request, "places.html", ctx)


@router.get("/places/map-data", response_class=JSONResponse)
async def places_map_data(q: str = "", city: str = "", country: str = "",
                          state: str = "", geocoded: str = ""):
    """Return geocoded active places as JSON for the map view, respecting active filters."""
    places = get_geocoded_places(query=q, city=city, country=country, state=state,
                                 geocoded=geocoded, db_path=_DB)
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
    state: str = Form(""),
    phone: str = Form(""),
    hours: str = Form(""),
    url: str = Form(""),
    rating: str = Form(""),
    lat: str = Form(""),
    lng: str = Form(""),
    is_active: int = Form(1),
):
    fields: dict = {
        "name":        name or None,
        "description": description or None,
        "address":     address or None,
        "postal_code": postal_code or None,
        "city":        city or None,
        "country":     country or None,
        "state":       state or None,
        "phone":       phone or None,
        "hours":       hours or None,
        "url":         url or None,
        "rating":      rating or None,
        "is_active":   is_active,
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
def place_geocode(place_id: int):
    """Trigger Nominatim geocoding for a single place and return a status fragment.

    place_id refers to place_articles.id; geocoding updates the canonical places row.
    Sync handler: Nominatim call + rate-limit sleep must not block the event loop.
    """
    from app.worker.geocoder import geocode_place as _geocode
    # get_place resolves pa_id to the canonical place fields
    place = get_place(place_id, _DB)
    if not place:
        return HTMLResponse('<span class="geo-error">Ort nicht gefunden</span>')
    result = _geocode(place)
    if result:
        lat, lng = result["lat"], result["lng"]
        # Update the canonical places row using places.id (place["id"])
        update_place_coords(place["id"], lat, lng, state=result.get("state"), db_path=_DB)
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
        opts += f'<option value="{c["id"]}">{escape(label)}</option>'
    return HTMLResponse(opts)


@router.post("/places/canonical/{canonical_id}/set-active")
async def place_set_active(canonical_id: int, is_active: int = Form(...)):
    """Toggle is_active on the canonical places row."""
    from app.db.database import get_connection
    with get_connection(_DB) as conn:
        conn.execute("UPDATE places SET is_active = ? WHERE id = ?", (is_active, canonical_id))
    from fastapi.responses import Response
    r = Response(status_code=204)
    r.headers["HX-Refresh"] = "true"
    return r


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
