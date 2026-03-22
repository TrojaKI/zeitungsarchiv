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


def geocode_place(place: dict) -> tuple[float, float] | None:
    """
    Geocode a single place dict using Nominatim.

    Tries a specific query first (name + address + city + country),
    then falls back to city + country only.

    Returns (lat, lng) on success, None on failure.
    """
    queries = _build_queries(place)
    for q in queries:
        result = _nominatim_search(q)
        if result:
            return result
        time.sleep(_RATE_LIMIT_SECONDS)
    return None


def _build_queries(place: dict) -> list[str]:
    """Build ordered list of query strings from most specific to least specific."""
    name = place.get("name") or ""
    address = place.get("address") or ""
    postal = place.get("postal_code") or ""
    city = place.get("city") or ""
    country = place.get("country") or ""

    queries = []

    # Most specific: name + full address
    if name and (address or city):
        parts = [p for p in [name, address, postal, city, country] if p]
        queries.append(", ".join(parts))

    # Fallback: city + country
    if city or country:
        parts = [p for p in [city, country] if p]
        if parts:
            queries.append(", ".join(parts))

    return queries


def _nominatim_search(query: str) -> tuple[float, float] | None:
    """
    Execute a single Nominatim search and return (lat, lng) for the first result.
    Returns None if no results or on any error.
    """
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "limit": 1,
    })
    url = f"{_NOMINATIM_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if data:
            lat = float(data[0]["lat"])
            lng = float(data[0]["lon"])
            log.debug("Geocoded %r -> (%.4f, %.4f)", query, lat, lng)
            return lat, lng
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
        coords = geocode_place(place)
        if coords:
            lat, lng = coords
            update_place_coords(place["id"], lat, lng, db_path)
            log.info("Geocoded place id=%d %r -> (%.4f, %.4f)",
                     place["id"], place.get("name"), lat, lng)
            success += 1
        else:
            log.warning("Could not geocode place id=%d %r",
                        place["id"], place.get("name"))
        # Always sleep between requests regardless of result
        time.sleep(_RATE_LIMIT_SECONDS)

    log.info("Geocoding done: %d/%d successful", success, len(pending))
    return success
