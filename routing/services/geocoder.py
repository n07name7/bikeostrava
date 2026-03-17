"""
Geocoding via Photon (primary) with Nominatim fallback.

Photon: https://photon.komoot.io - free, OSM-based, no API key, no rate limit sign-up.
Nominatim: https://nominatim.openstreetmap.org - official OSM geocoder.

Both bias results toward Ostrava, Czech Republic.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PHOTON_URL    = "https://photon.komoot.io/api/"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Bounding box roughly covering Ostrava + 30 km surroundings
# Photon: lon_min,lat_min,lon_max,lat_max  (bbox parameter)
OSTRAVA_BBOX      = "17.8,49.7,18.5,49.95"
# Nominatim viewbox: lon_min,lat_min,lon_max,lat_max
NOMINATIM_VIEWBOX = "17.8,49.7,18.5,49.95"


def geocode(address: str) -> dict:
    """
    Geocode *address* to lat/lng, biased toward Ostrava.

    Returns:
        {"address": str, "lat": float, "lng": float}

    Raises:
        ValueError: if address cannot be resolved.
    """
    try:
        return _photon(address)
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("Photon failed, trying Nominatim: %s", exc)

    try:
        return _nominatim(address)
    except Exception as exc:
        raise ValueError(
            f"Adresa nenalezena: '{address}'. "
            "Zkuste přesnější dotaz, např. 'Stodolní, Ostrava'."
        ) from exc


# ---------------------------------------------------------------------------
# Photon
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": "Mozilla/5.0 BikeOstrava/1.0 (https://github.com/bikeostrava; contact@bikeostrava.cz)"
}


def _photon(address: str) -> dict:
    params = {
        "q":     address,
        "limit": 5,
        "lang":  "en",
        # bias toward Ostrava centre
        "lon":   18.2625,
        "lat":   49.8209,
    }
    resp = requests.get(PHOTON_URL, params=params, headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    features = data.get("features", [])
    if not features:
        raise ValueError(
            f"Adresa nenalezena: '{address}'. "
            "Zkuste přesnější dotaz, např. 'Stodolní, Ostrava'."
        )

    best = _photon_pick_best(features, address)
    props = best["properties"]
    coords = best["geometry"]["coordinates"]  # [lng, lat]

    # Build a readable display name
    parts = [
        props.get("name", ""),
        props.get("street", ""),
        props.get("housenumber", ""),
        props.get("city", props.get("town", props.get("village", ""))),
        props.get("country", ""),
    ]
    display = ", ".join(p for p in parts if p)

    return {
        "address": display or address,
        "lat":     float(coords[1]),
        "lng":     float(coords[0]),
    }


def _photon_pick_best(features: list, query: str) -> dict:
    """Prefer results in Czech Republic near Ostrava."""
    query_lower = query.lower()

    # 1st pass: Czech Republic + city mention
    for f in features:
        p = f["properties"]
        if p.get("country_code", "").lower() == "cz":
            city = (p.get("city") or p.get("town") or "").lower()
            if "ostrava" in city or "ostrava" in query_lower:
                return f

    # 2nd pass: any Czech result
    for f in features:
        if f["properties"].get("country_code", "").lower() == "cz":
            return f

    return features[0]


# ---------------------------------------------------------------------------
# Nominatim (fallback)
# ---------------------------------------------------------------------------

def _nominatim(address: str) -> dict:
    params = {
        "q":             address,
        "format":        "jsonv2",
        "limit":         5,
        "addressdetails": 1,
        "countrycodes":  "cz",
        "viewbox":       NOMINATIM_VIEWBOX,
        "bounded":       0,
    }
    headers = {"User-Agent": settings.NOMINATIM_USER_AGENT}
    resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    results = resp.json()

    if not results:
        raise ValueError(f"Nominatim: no results for '{address}'")

    best = _nominatim_pick_best(results)
    return {
        "address": best["display_name"],
        "lat":     float(best["lat"]),
        "lng":     float(best["lon"]),
    }


def _nominatim_pick_best(results: list) -> dict:
    for r in results:
        if "ostrava" in r.get("display_name", "").lower():
            return r
    return results[0]
