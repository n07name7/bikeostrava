"""
Query accident data near a cycling route.

AccidentPoint rows are loaded by the management command `load_accidents`.
Uses bounding-box SQL filtering + Python haversine for distance calculations,
so no PostGIS extension is needed.

Provides:
- count_accidents_near_route(coords, radius_m=100)  → int
- get_accidents_near_route(coords, radius_m=100)    → list of {lat, lng, severity, date}
"""
import logging
import math

from routing.services.geo_utils import haversine_m, point_to_segment_dist_m

logger = logging.getLogger(__name__)

# Rough conversion: 1 degree latitude ≈ 111 km
_DEG_PER_METER_LAT = 1.0 / 111_000


def _bbox_for_coords(coordinates: list, radius_m: int) -> tuple:
    """
    Compute a bounding box around a route (list of [lng, lat] pairs),
    expanded by radius_m in each direction.
    Returns (lat_min, lat_max, lng_min, lng_max).
    """
    lats = [c[1] for c in coordinates]
    lngs = [c[0] for c in coordinates]
    lat_margin = radius_m * _DEG_PER_METER_LAT
    # Longitude margin depends on latitude
    avg_lat = sum(lats) / len(lats)
    lng_margin = lat_margin / max(math.cos(math.radians(avg_lat)), 0.01)
    return (
        min(lats) - lat_margin,
        max(lats) + lat_margin,
        min(lngs) - lng_margin,
        max(lngs) + lng_margin,
    )


def _point_to_line_dist_m(pt_lng: float, pt_lat: float, coordinates: list) -> float:
    """
    Minimum distance (metres) from a point to any segment of the route polyline.
    Uses haversine for each segment — fast enough for ≤2000 candidate points
    and ≤500 route segments.
    """
    min_dist = float("inf")
    for i in range(len(coordinates) - 1):
        seg_lng1, seg_lat1 = coordinates[i][0], coordinates[i][1]
        seg_lng2, seg_lat2 = coordinates[i + 1][0], coordinates[i + 1][1]
        dist = point_to_segment_dist_m(pt_lng, pt_lat, seg_lng1, seg_lat1, seg_lng2, seg_lat2)
        if dist < min_dist:
            min_dist = dist
            if dist < 1.0:  # close enough, skip remaining segments
                break
    return min_dist


def count_accidents_near_route(coordinates: list, radius_m: int = 100) -> int | None:
    """
    Count AccidentPoint rows within *radius_m* metres of the route.

    Returns None if the AccidentPoint table is empty (data not loaded yet),
    so the API can return accident_score=null gracefully.
    """
    from routing.models import AccidentPoint  # avoid circular import at module level

    try:
        if not AccidentPoint.objects.exists():
            logger.info("No accident data loaded - returning null score")
            return None

        lat_min, lat_max, lng_min, lng_max = _bbox_for_coords(coordinates, radius_m)
        candidates = AccidentPoint.objects.filter(
            latitude__gte=lat_min, latitude__lte=lat_max,
            longitude__gte=lng_min, longitude__lte=lng_max,
        ).values_list("latitude", "longitude")

        count = sum(
            1 for lat, lng in candidates
            if _point_to_line_dist_m(lng, lat, coordinates) <= radius_m
        )
        return count
    except Exception as exc:
        logger.warning("Accident query failed: %s", exc)
        return None


def get_accidents_near_route(coordinates: list, radius_m: int = 100) -> list:
    """
    Return accident point details within *radius_m* metres of the route.

    Each item: {"lat": float, "lng": float, "severity": str, "date": str|None}
    """
    from routing.models import AccidentPoint

    try:
        if not AccidentPoint.objects.exists():
            return []

        lat_min, lat_max, lng_min, lng_max = _bbox_for_coords(coordinates, radius_m)
        candidates = AccidentPoint.objects.filter(
            latitude__gte=lat_min, latitude__lte=lat_max,
            longitude__gte=lng_min, longitude__lte=lng_max,
        ).values("latitude", "longitude", "severity", "date")

        result = []
        for a in candidates:
            if _point_to_line_dist_m(a["longitude"], a["latitude"], coordinates) <= radius_m:
                result.append({
                    "lat": a["latitude"],
                    "lng": a["longitude"],
                    "severity": a["severity"] or "unknown",
                    "date": str(a["date"]) if a["date"] else None,
                })
                if len(result) >= 50:  # cap at 50 for frontend
                    break
        return result
    except Exception as exc:
        logger.warning("Accident detail query failed: %s", exc)
        return []
