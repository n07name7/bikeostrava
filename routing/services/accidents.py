"""
Query accident data stored in PostGIS.

AccidentPoint rows are loaded by the management command `load_accidents`.
This module provides:
- count_accidents_near_route(coords, radius_m=100)  → int
- get_accidents_near_route(coords, radius_m=100)    → list of {lat, lng, severity, date}
"""
import logging

from django.contrib.gis.geos import LineString, Point
from django.contrib.gis.measure import Distance

logger = logging.getLogger(__name__)


def _route_linestring(coordinates: list) -> LineString:
    """Convert [[lng, lat], ...] GeoJSON coords to a PostGIS LineString."""
    # GeoJSON is [lng, lat]; Django GIS Point/LineString takes (x=lng, y=lat)
    return LineString(coordinates, srid=4326)


def count_accidents_near_route(coordinates: list, radius_m: int = 100) -> int | None:
    """
    Count AccidentPoint rows within *radius_m* metres of the route LineString.

    Returns None if the AccidentPoint table is empty (data not loaded yet),
    so the API can return accident_score=null gracefully.
    """
    from routing.models import AccidentPoint  # avoid circular import at module level

    try:
        if not AccidentPoint.objects.exists():
            logger.info("No accident data loaded - returning null score")
            return None

        route_line = _route_linestring(coordinates)
        count = AccidentPoint.objects.filter(
            location__distance_lte=(route_line, Distance(m=radius_m))
        ).count()
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

        route_line = _route_linestring(coordinates)
        accidents = AccidentPoint.objects.filter(
            location__distance_lte=(route_line, Distance(m=radius_m))
        ).values("location", "severity", "date")[:50]  # cap at 50 for frontend

        result = []
        for a in accidents:
            pt = a["location"]
            result.append({
                "lat": pt.y,
                "lng": pt.x,
                "severity": a["severity"] or "unknown",
                "date": str(a["date"]) if a["date"] else None,
            })
        return result
    except Exception as exc:
        logger.warning("Accident detail query failed: %s", exc)
        return []
