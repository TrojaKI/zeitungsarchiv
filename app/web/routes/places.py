"""Places routes: list/search all places, edit/delete individual place."""

import os
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.db.database import (delete_place, get_all_places, get_geocoded_places,
                              get_place_filter_options, get_review_count, update_place)
from app.web.templating import templates as _templates

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


def _ctx(request: Request, **kwargs) -> dict:
    return {"request": request, "review_count": get_review_count(_DB), **kwargs}


@router.get("/places", response_class=HTMLResponse)
async def places_list(request: Request, q: str = "", city: str = "", country: str = ""):
    places = get_all_places(query=q, city=city, country=country, db_path=_DB)
    opts = get_place_filter_options(_DB)
    ctx = _ctx(request, places=places, q=q, city=city, country=country, **opts)
    # HTMX partial request: return only the results fragment
    if request.headers.get("hx-request"):
        return _templates.TemplateResponse("places_results.html", ctx)
    return _templates.TemplateResponse("places.html", ctx)


@router.get("/places/map-data", response_class=JSONResponse)
async def places_map_data():
    """Return all geocoded places as JSON for the map view."""
    places = get_geocoded_places(_DB)
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
):
    update_place(place_id, {
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
    }, _DB)
    return RedirectResponse(f"/articles/{article_id}/edit", status_code=303)


@router.post("/places/{place_id}/delete")
async def place_delete(place_id: int, article_id: int = Form(...)):
    delete_place(place_id, _DB)
    return RedirectResponse(f"/articles/{article_id}/edit", status_code=303)
