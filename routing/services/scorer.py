"""
Safety scoring engine.

Inputs:
- route coordinates        [[lng, lat], ...]
- bike_paths               list of path coordinate lists from Overpass
- green_zones              list of polygon coordinate lists from Overpass
- accident_count           int | None

Outputs a dict:
{
    "overall": int,             # 0-100
    "bike_path_coverage": int,  # 0-100
    "accident_density": int | None,
    "green_zone_coverage": int, # 0-100
    "highlights": [str, ...],   # Czech-language human-readable bullets
}

Scoring formulas (from spec):
  bike_path:   0%→20, 50%→60, 90%+→100   (piecewise linear)
  accident:    0→100, 3→70, 10+→30        (piecewise linear)
  green_zone:  0%→40, 50%→70, 80%+→100   (piecewise linear)
  overall = bike*0.45 + accident*0.35 + green*0.20
"""
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Geometry helpers (pure Python - no GDAL/PostGIS needed here)
# ---------------------------------------------------------------------------

def _haversine_m(lng1, lat1, lng2, lat2) -> float:
    """Haversine distance in metres between two WGS-84 points."""
    R = 6_371_000  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _segment_length_m(c1, c2) -> float:
    return _haversine_m(c1[0], c1[1], c2[0], c2[1])


def _total_length_m(coords: list) -> float:
    total = 0.0
    for i in range(len(coords) - 1):
        total += _segment_length_m(coords[i], coords[i + 1])
    return total


def _point_to_segment_dist_m(px, py, ax, ay, bx, by) -> float:
    """Minimum distance (metres) from point P to segment AB."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return _haversine_m(px, py, ax, ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return _haversine_m(px, py, cx, cy)


def _min_dist_to_polyline_m(point, polyline) -> float:
    """Minimum distance from *point* [lng, lat] to any segment of *polyline*."""
    px, py = point
    min_d = float("inf")
    for i in range(len(polyline) - 1):
        ax, ay = polyline[i]
        bx, by = polyline[i + 1]
        d = _point_to_segment_dist_m(px, py, ax, ay, bx, by)
        if d < min_d:
            min_d = d
    return min_d


def _min_dist_to_ring_m(point, ring) -> float:
    """Minimum distance from *point* to any edge of a polygon ring."""
    px, py = point
    min_d = float("inf")
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        d = _point_to_segment_dist_m(px, py, ax, ay, bx, by)
        if d < min_d:
            min_d = d
    return min_d


def _point_in_ring(px, py, ring) -> bool:
    """Ray-casting point-in-polygon for a coordinate ring."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Coverage calculators
# ---------------------------------------------------------------------------

def _bike_path_coverage(route_coords: list, bike_paths: list, threshold_m: float = 15) -> float:
    """
    Fraction (0.0–1.0) of route midpoints within *threshold_m* of any bike path.
    We sample at midpoints of each route segment.
    """
    if not bike_paths or len(route_coords) < 2:
        return 0.0

    hit = 0
    total = len(route_coords) - 1

    for i in range(total):
        # midpoint of segment
        mx = (route_coords[i][0] + route_coords[i + 1][0]) / 2
        my = (route_coords[i][1] + route_coords[i + 1][1]) / 2
        for path in bike_paths:
            if len(path) < 2:
                continue
            d = _min_dist_to_polyline_m([mx, my], path)
            if d <= threshold_m:
                hit += 1
                break

    return hit / total if total > 0 else 0.0


def _green_zone_coverage(route_coords: list, green_zones: list, buffer_m: float = 50) -> float:
    """
    Fraction of route points within *buffer_m* metres of (or inside) any green zone polygon.
    """
    if not green_zones or not route_coords:
        return 0.0

    hit = 0
    for pt in route_coords:
        px, py = pt[0], pt[1]
        for zone in green_zones:
            ring = zone["coords"] if isinstance(zone, dict) else zone
            if len(ring) < 3:
                continue
            if _point_in_ring(px, py, ring):
                hit += 1
                break
            if _min_dist_to_ring_m([px, py], ring) <= buffer_m:
                hit += 1
                break

    return hit / len(route_coords)


# ---------------------------------------------------------------------------
# Piecewise linear score functions (from spec)
# ---------------------------------------------------------------------------

def _bike_path_score(coverage_pct: float) -> int:
    """coverage_pct: 0–100."""
    if coverage_pct <= 0:
        return 20
    if coverage_pct <= 50:
        return 20 + int((coverage_pct / 50) * 40)
    if coverage_pct <= 90:
        return 60 + int(((coverage_pct - 50) / 40) * 40)
    return 100


def _accident_score(count: int) -> int:
    if count <= 0:
        return 100
    if count <= 3:
        return 100 - int((count / 3) * 30)
    if count <= 10:
        return 70 - int(((count - 3) / 7) * 40)
    return 30


def _green_zone_score(coverage_pct: float) -> int:
    """coverage_pct: 0–100."""
    if coverage_pct <= 0:
        return 40
    if coverage_pct <= 50:
        return 40 + int((coverage_pct / 50) * 30)
    if coverage_pct <= 80:
        return 70 + int(((coverage_pct - 50) / 30) * 30)
    return 100


# ---------------------------------------------------------------------------
# Highlight generation (Czech)
# ---------------------------------------------------------------------------

def _generate_highlights(bike_pct: float, accident_count: Optional[int], green_pct: float, green_zones: list) -> list:
    highlights = []

    # Bike path highlight
    if bike_pct >= 70:
        highlights.append(f"{int(bike_pct)}% trasy vede po cyklostezce nebo cyklopruhu")
    elif bike_pct >= 40:
        highlights.append(f"{int(bike_pct)}% trasy má vyhrazený prostor pro cyklisty")
    else:
        highlights.append("Trasa vede převážně po sdílených komunikacích")

    # Accident highlight
    if accident_count is None:
        highlights.append("Data o nehodách nejsou k dispozici")
    elif accident_count == 0:
        highlights.append("Žádné zaznamenané nehody v okolí 100 m trasy")
    elif accident_count <= 2:
        highlights.append(f"{accident_count} nehody zaznamenány v okolí trasy - buďte opatrní")
    else:
        highlights.append(f"Pozor: {accident_count} nehod v okolí 100 m trasy")

    # Green zone highlight
    if green_pct >= 60:
        highlights.append(f"Průjezd zelenými zónami ({int(green_pct)}% trasy)")
    elif green_pct >= 30:
        highlights.append(f"Část trasy prochází zelenými oblastmi ({int(green_pct)}%)")
    else:
        highlights.append("Trasa vede převážně zastavěnou oblastí")

    return highlights


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def bike_pct_from_segments(road_segments: list) -> float:
    """
    Calculate bike-friendly coverage % directly from GH road_segments.
    More accurate than Overpass proximity - uses actual road class per segment.
    """
    if not road_segments:
        return 0.0
    total = sum(_total_length_m(s["coords"]) for s in road_segments if len(s["coords"]) >= 2)
    if total == 0:
        return 0.0
    bike = sum(
        _total_length_m(s["coords"])
        for s in road_segments
        if s.get("category") == "bike" and len(s["coords"]) >= 2
    )
    return min(100.0, (bike / total) * 100)


def compute_safety_score(
    route_coords: list,
    road_segments: list,
    green_zones: list,
    accident_count: Optional[int],
) -> dict:
    """
    Compute full safety score dict.

    Args:
        route_coords:   [[lng, lat], ...]
        road_segments:  segmented route from router (with road_class + category)
        green_zones:    list of green-zone polygon coord lists from Overpass
        accident_count: int count or None if unavailable
    """
    # Bike coverage: from GH road_class data (exact) if available, else Overpass fallback
    if road_segments and any(s.get("road_class", "other") != "other" for s in road_segments):
        bike_pct = bike_pct_from_segments(road_segments)
    else:
        bike_pct = _bike_path_coverage(route_coords, []) * 100  # no Overpass fallback

    green_frac = _green_zone_coverage(route_coords, green_zones)

    green_pct = green_frac * 100

    # Component scores (0-100)
    b_score = _bike_path_score(bike_pct)
    g_score = _green_zone_score(green_pct)
    a_score = _accident_score(accident_count) if accident_count is not None else None

    # Overall (handle null accident score)
    if a_score is not None:
        overall = round(b_score * 0.45 + a_score * 0.35 + g_score * 0.20)
    else:
        # Redistribute weights when accident data absent
        overall = round(b_score * 0.55 + g_score * 0.45)

    overall = max(0, min(100, overall))

    highlights = _generate_highlights(bike_pct, accident_count, green_pct, green_zones)

    return {
        "overall": overall,
        "bike_path_coverage": b_score,
        "accident_density": a_score,
        "green_zone_coverage": g_score,
        "highlights": highlights,
        # raw percentages for PDF breakdown text
        "_bike_pct": round(bike_pct, 1),
        "_green_pct": round(green_pct, 1),
    }
