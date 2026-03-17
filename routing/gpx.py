"""
Generate a GPX 1.1 track file from a SavedRoute.
Compatible with Garmin, Wahoo, Komoot, Strava import, etc.
"""
from datetime import datetime
from xml.sax.saxutils import escape


def generate_route_gpx(saved_route) -> bytes:
    data   = saved_route.route_data or {}
    geojson = data.get("route_geojson", {})
    coords  = (geojson.get("geometry") or {}).get("coordinates", []) if geojson else []
    elev    = {round(p[0], 5): p[1]
               for p in data.get("elevation_profile", [])}   # dist→ele not useful here

    # elevation_profile is indexed by point order, not dist - rebuild from raw coords
    # If GH returned 3-D coords they were stripped; elevation_profile is our source
    elev_list = [p[1] for p in data.get("elevation_profile", [])]

    name   = escape(f"{saved_route.start_address} → {saved_route.end_address}")
    ts     = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    score  = saved_route.safety_score

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="BikeOstrava" '
        'xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://www.topografix.com/GPX/1/1 '
        'http://www.topografix.com/GPX/1/1/gpx.xsd">',
        f'  <metadata>',
        f'    <name>{name}</name>',
        f'    <desc>BikeOstrava safety score: {score}/100 · '
        f'{saved_route.distance_km:.1f} km · {saved_route.duration_min} min</desc>',
        f'    <time>{ts}</time>',
        f'  </metadata>',
        f'  <trk>',
        f'    <name>{name}</name>',
        f'    <trkseg>',
    ]

    for i, coord in enumerate(coords):
        lng, lat = coord[0], coord[1]
        ele_tag  = ""
        if i < len(elev_list):
            ele_tag = f"<ele>{elev_list[i]:.1f}</ele>"
        lines.append(f'      <trkpt lat="{lat:.7f}" lon="{lng:.7f}">{ele_tag}</trkpt>')

    lines += [
        '    </trkseg>',
        '  </trk>',
        '</gpx>',
    ]

    return "\n".join(lines).encode("utf-8")
