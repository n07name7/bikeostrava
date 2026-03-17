"""
Cycling route calculation.

Priority:
1. Local GraphHopper (localhost:8989) - bike_safe profile with path details
2. GraphHopper public API
3. OSRM public API - free fallback

Requests up to 3 alternative routes from GraphHopper; returns a list of route
dicts so the caller can score and present each alternative.
"""
import logging
import math

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

LOCAL_GH_URL = "http://localhost:8991/route"

# road_class values GraphHopper returns → our category
BIKE_FRIENDLY = {"cycleway", "path", "footway", "pedestrian", "living_street", "track"}
NEUTRAL       = {"residential", "unclassified", "service", "road", "other"}
BUSY          = {"tertiary", "secondary", "primary", "trunk", "motorway",
                 "tertiary_link", "secondary_link", "primary_link"}

# GH turn sign → arrow character (all in DejaVu Sans)
_SIGNS = {
    -3: "↰", -2: "←", -1: "↖",
     0: "↑",
     1: "↗",  2: "→",  3: "↱",
     4: "⚑",   5: "⬤",  6: "↻",
}


def get_route(start_lat: float, start_lng: float, end_lat: float, end_lng: float) -> list[dict]:
    """
    Calculate cycling route alternatives. Returns 1–3 route dicts, each:
    {
        "coordinates":       [[lng, lat], ...],
        "distance_km":       float,
        "duration_min":      int,
        "road_segments":     [{...}, ...],
        "elevation_profile": [[dist_km, ele_m], ...],  # empty if GH has no DEM
        "instructions":      [{...}, ...],
    }
    """
    try:
        return _graphhopper_local(start_lat, start_lng, end_lat, end_lng)
    except Exception as exc:
        logger.warning("Local GraphHopper unavailable: %s", exc)

    if settings.GRAPHHOPPER_API_KEY:
        try:
            return _graphhopper_cloud(start_lat, start_lng, end_lat, end_lng)
        except Exception as exc:
            logger.warning("GraphHopper cloud failed: %s", exc)

    try:
        return _osrm(start_lat, start_lng, end_lat, end_lng)
    except Exception as exc:
        raise ValueError(
            "Nepodařilo se vypočítat trasu. Zkuste to prosím znovu za chvíli."
        ) from exc


def _graphhopper_local(start_lat, start_lng, end_lat, end_lng) -> list[dict]:
    payload = {
        "points":          [[start_lng, start_lat], [end_lng, end_lat]],
        "profile":         "bike_safe",
        "points_encoded":  False,
        "instructions":    True,
        "elevation":       True,
        "locale":          "cs",
        "details":         ["road_class", "surface"],
        "ch.disable":      True,   # LM mode - required for path details + alternatives
        "algorithm":                           "alternative_route",
        "alternative_route.max_paths":         3,
        "alternative_route.max_weight_factor": 1.4,
        "alternative_route.max_share_factor":  0.6,
    }
    response = requests.post(LOCAL_GH_URL, json=payload, timeout=15)
    response.raise_for_status()
    data = response.json()
    if "message" in data:
        raise ValueError(data["message"])
    return _parse_gh_paths(data)


def _graphhopper_cloud(start_lat, start_lng, end_lat, end_lng) -> list[dict]:
    payload = {
        "points":          [[start_lng, start_lat], [end_lng, end_lat]],
        "profile":         "bike",
        "points_encoded":  False,
        "instructions":    True,
        "elevation":       True,
        "algorithm":                           "alternative_route",
        "alternative_route.max_paths":         3,
        "alternative_route.max_weight_factor": 1.4,
    }
    response = requests.post(
        settings.GRAPHHOPPER_API_URL,
        json=payload,
        params={"key": settings.GRAPHHOPPER_API_KEY},
        timeout=15,
    )
    response.raise_for_status()
    return _parse_gh_paths(response.json())


def _parse_gh_paths(data: dict) -> list[dict]:
    paths = data.get("paths", [])
    if not paths:
        raise ValueError("GraphHopper returned no paths")
    return [_parse_gh_path(p) for p in paths]


def _parse_gh_path(path: dict) -> dict:
    raw_coords = path["points"]["coordinates"]   # [[lng, lat] or [lng, lat, ele]]
    details    = path.get("details", {})
    rc_segs    = details.get("road_class", [])

    # Strip elevation from 2-D coords used everywhere else
    coords_2d = [[c[0], c[1]] for c in raw_coords]

    return {
        "coordinates":       coords_2d,
        "distance_km":       round(path["distance"] / 1000, 2),
        "duration_min":      round(path["time"] / 60000),
        "road_segments":     _build_segments(coords_2d, rc_segs),
        "elevation_profile": _build_elevation_profile(raw_coords),
        "instructions":      _parse_instructions(path.get("instructions", [])),
    }


# ── Elevation ─────────────────────────────────────────────────────────────────

def _haversine_m(lng1, lat1, lng2, lat2) -> float:
    R = 6_371_000
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = φ2 - φ1
    dλ = math.radians(lng2 - lng1)
    a  = math.sin(dφ/2)**2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _build_elevation_profile(coords: list) -> list:
    """
    Returns [[dist_km, ele_m], ...].
    Empty list if coords have no elevation component (GH without DEM).
    """
    if not coords or len(coords[0]) < 3:
        return []
    profile   = []
    cum_dist  = 0.0
    for i, c in enumerate(coords):
        if i > 0:
            cum_dist += _haversine_m(coords[i-1][0], coords[i-1][1], c[0], c[1])
        profile.append([round(cum_dist / 1000, 3), round(c[2], 1)])
    return profile


# ── Instructions ──────────────────────────────────────────────────────────────

def _parse_instructions(raw: list) -> list:
    """
    Normalise GH instructions into a list of dicts.
    Only meaningful turns (sign != 0 or first/last) are kept to keep the table short.
    """
    if not raw:
        return []
    result = []
    for i, instr in enumerate(raw):
        sign   = instr.get("sign", 0)
        dist_m = round(instr.get("distance", 0))
        text   = instr.get("text", "").strip()
        # Always include first (start) and last (finish); skip trivial "continue" steps
        if i == 0 or i == len(raw) - 1 or sign != 0 or dist_m == 0:
            result.append({
                "sign":   sign,
                "arrow":  _SIGNS.get(sign, "↑"),
                "text":   text,
                "dist_m": dist_m,
            })
    return result


# ── Segments ──────────────────────────────────────────────────────────────────

def _build_segments(coords: list, rc_segs: list) -> list:
    if not rc_segs:
        return [{"coords": coords, "road_class": "other", "category": "neutral"}]

    segments = []
    for from_idx, to_idx, rc in rc_segs:
        seg_coords = coords[from_idx : to_idx + 1]
        if len(seg_coords) < 2:
            continue
        rc_lower = rc.lower()
        if rc_lower in BIKE_FRIENDLY:
            category = "bike"
        elif rc_lower in BUSY:
            category = "busy"
        else:
            category = "neutral"

        if segments and segments[-1]["category"] == category:
            segments[-1]["coords"].extend(seg_coords[1:])
            segments[-1]["road_class"] = rc_lower
        else:
            segments.append({"coords": seg_coords, "road_class": rc_lower, "category": category})

    return segments or [{"coords": coords, "road_class": "other", "category": "neutral"}]


# ── OSRM fallback ─────────────────────────────────────────────────────────────

def _osrm(start_lat, start_lng, end_lat, end_lng) -> list[dict]:
    url = f"{settings.OSRM_API_URL}/bike/{start_lng},{start_lat};{end_lng},{end_lat}"
    response = requests.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=15)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError(f"OSRM error: {data.get('code', 'unknown')}")
    route  = data["routes"][0]
    coords = route["geometry"]["coordinates"]
    return [{
        "coordinates":       coords,
        "distance_km":       round(route["distance"] / 1000, 2),
        "duration_min":      round(route["duration"] / 60),
        "road_segments":     [{"coords": coords, "road_class": "other", "category": "neutral"}],
        "elevation_profile": [],
        "instructions":      [],
    }]
