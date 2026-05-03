"""Geocoder using the Nominatim/OSM API (no external packages required)."""

import logging
import time
import urllib.parse
import urllib.request
import json
from pathlib import Path

log = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "Zeitungsarchiv/1.0"
# Nominatim policy: max 1 request per second
_RATE_LIMIT_SECONDS = 1.1


def geocode_place(place: dict) -> dict | None:
    """Geocode a single place dict using Nominatim.

    Returns {"lat": float, "lng": float, "state": str | None} on success, None on failure.
    """
    queries = _build_queries(place)
    for q in queries:
        result = _nominatim_search(q)
        if result:
            return result
        time.sleep(_RATE_LIMIT_SECONDS)
    return None


def _build_queries(place: dict) -> list[str]:
    """Build ordered list of query strings from most specific to least specific.

    Tries up to five variants so that even places with incomplete address data
    have a chance of being resolved to at least city-level coordinates.
    """
    name = place.get("name") or ""
    address = place.get("address") or ""
    postal = place.get("postal_code") or ""
    city = place.get("city") or ""
    country = place.get("country") or ""

    seen: set[str] = set()
    queries: list[str] = []

    def _add(*parts: str) -> None:
        q = ", ".join(p for p in parts if p)
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    # 1. Most specific: name + full address
    if name and (address or city):
        _add(name, address, postal, city, country)

    # 2. Address without name (useful when name is not in OSM)
    if address and city:
        _add(address, postal, city, country)

    # 3. Name + city (skip street address)
    if name and city:
        _add(name, city, country)

    # 4. Name + country (when city is unknown but name may be findable)
    if name and country and not city:
        _add(name, country)

    # 5. City + country only (requires city — country-only returns centroid)
    if city:
        _add(city, country)

    # 6. Postal code + country (last resort when city name is ambiguous)
    if postal and country:
        _add(postal, country)

    return queries


def _nominatim_search(query: str) -> dict | None:
    """Execute a single Nominatim search and return result dict for the first hit.

    Returns {"lat": float, "lng": float, "state": str | None}, or None on failure.
    """
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "limit": 1,
        "addressdetails": 1,
    })
    url = f"{_NOMINATIM_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if data:
            lat = float(data[0]["lat"])
            lng = float(data[0]["lon"])
            state = data[0].get("address", {}).get("state")
            log.debug("Geocoded %r -> (%.4f, %.4f) state=%r", query, lat, lng, state)
            return {"lat": lat, "lng": lng, "state": state}
    except Exception as exc:
        log.warning("Nominatim error for %r: %s", query, exc)
    return None


def geocode_all_places(db_path: Path) -> int:
    """
    Geocode all places in the DB that are missing coordinates.

    Respects the Nominatim rate limit (1 req/s). Returns count of
    successfully geocoded places.
    """
    from app.db.database import get_places_without_coords, update_place_coords

    pending = get_places_without_coords(db_path)
    if not pending:
        log.info("All places already geocoded.")
        return 0

    log.info("Geocoding %d place(s)...", len(pending))
    success = 0
    for place in pending:
        result = geocode_place(place)
        if result:
            update_place_coords(place["id"], result["lat"], result["lng"],
                                state=result.get("state"), db_path=db_path)
            log.info("Geocoded place id=%d %r -> (%.4f, %.4f) state=%r",
                     place["id"], place.get("name"), result["lat"], result["lng"],
                     result.get("state"))
            success += 1
        else:
            log.warning("Could not geocode place id=%d %r",
                        place["id"], place.get("name"))
        # Always sleep between requests regardless of result
        time.sleep(_RATE_LIMIT_SECONDS)

    log.info("Geocoding done: %d/%d successful", success, len(pending))
    return success
