"""
Fetch bike infrastructure and green zones from OpenStreetMap via Overpass API.

Two functions are exposed:
- get_bike_paths(bbox)   → list of LineString coordinate lists
- get_green_zones(bbox)  → list of Polygon coordinate lists

bbox = (lat_min, lng_min, lat_max, lng_max)  - the bounding box of the route.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _overpass_query(ql: str) -> dict:
    """Execute an Overpass QL query and return parsed JSON."""
    try:
        response = requests.post(
            settings.OVERPASS_API_URL,
            data={"data": ql},
            headers={"User-Agent": "BikeOstrava/1.0 (Contact: admin@bikeostrava.cz)"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        logger.warning("Overpass API timed out")
        return {"elements": []}
    except requests.RequestException as exc:
        logger.warning("Overpass API error: %s", exc)
        return {"elements": []}


def _route_bbox(route_coords: list, padding_deg: float = 0.01) -> tuple:
    """
    Compute lat/lng bounding box from a list of [lng, lat] GeoJSON coords,
    expanded by *padding_deg* degrees on each side.
    """
    lats = [c[1] for c in route_coords]
    lngs = [c[0] for c in route_coords]
    return (
        min(lats) - padding_deg,
        min(lngs) - padding_deg,
        max(lats) + padding_deg,
        max(lngs) + padding_deg,
    )


def get_bike_paths(route_coords: list) -> list:
    """
    Return bike-path geometries (lists of [lng, lat]) overlapping the route bbox.

    Queries OSM for:
    - highway=cycleway
    - bicycle=designated / bicycle=yes on any highway
    - route=bicycle relations (named cycle routes)
    """
    lat_min, lng_min, lat_max, lng_max = _route_bbox(route_coords)
    bbox_str = f"{lat_min},{lng_min},{lat_max},{lng_max}"

    ql = f"""
[out:json][timeout:10];
(
  way["highway"="cycleway"]({bbox_str});
  way["bicycle"="designated"]({bbox_str});
  way["bicycle"="yes"]["highway"~"path|footway|track"]({bbox_str});
);
out geom;
"""
    data = _overpass_query(ql)
    paths = []
    for element in data.get("elements", []):
        if element.get("type") == "way" and "geometry" in element:
            coords = [[pt["lon"], pt["lat"]] for pt in element["geometry"]]
            if len(coords) >= 2:
                paths.append(coords)
    return paths


def get_map_context(route_coords: list) -> tuple[list, list]:
    """
    Return both green-zone polygons and cyclist POIs in a single Overpass request.
    This avoids HTTP 429 errors from parallel requests.
    Returns (green_zones, pois).
    """
    lat_min, lng_min, lat_max, lng_max = _route_bbox(route_coords, padding_deg=0.005)
    bbox_str = f"{lat_min},{lng_min},{lat_max},{lng_max}"

    ql = f"""
[out:json][timeout:15];
(
  way["leisure"~"park|nature_reserve"]({bbox_str});
  way["landuse"~"forest|meadow"]({bbox_str});
  way["natural"~"wood|scrub|heath"]({bbox_str});
  node["shop"="bicycle"]({bbox_str});
  node["amenity"="bicycle_repair_station"]({bbox_str});
  node["amenity"="drinking_water"]({bbox_str});
);
out geom;
"""
    data = _overpass_query(ql)
    zones = []
    pois = []
    
    for el in data.get("elements", []):
        el_type = el.get("type")
        tags = el.get("tags", {})
        
        if el_type == "way" and "geometry" in el:
            coords = [[pt["lon"], pt["lat"]] for pt in el["geometry"]]
            if len(coords) >= 3:
                zones.append({
                    "coords": coords,
                    "name": tags.get("name", ""),
                    "zone_type": (
                        tags.get("leisure") or
                        tags.get("landuse") or
                        tags.get("natural") or
                        "green"
                    ),
                })
        elif el_type == "node":
            amenity = tags.get("amenity", "")
            shop = tags.get("shop", "")
            if shop == "bicycle" or amenity == "bicycle_repair_station":
                poi_type = "bike_shop"
            elif amenity == "drinking_water":
                poi_type = "water"
            else:
                continue
                
            pois.append({
                "lat":  el["lat"],
                "lng":  el["lon"],
                "type": poi_type,
                "name": tags.get("name", ""),
            })
            
    return zones, pois
