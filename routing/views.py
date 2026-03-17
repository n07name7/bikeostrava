"""
API views for BikeOstrava.

POST /api/route/            - compute route + safety score (returns alternatives)
GET  /api/route/<id>/pdf/   - download PDF summary for a specific route/alternative
GET  /api/route/<id>/       - retrieve cached route by UUID
"""
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django_ratelimit.decorators import ratelimit

from routing.models import RouteCache, SavedRoute
from routing.services.geocoder import geocode
from routing.services.overpass import get_green_zones, get_cyclist_pois
from routing.services.router import get_route
from routing.services.accidents import count_accidents_near_route, get_accidents_near_route
from routing.services.scorer import compute_safety_score
from routing.pdf import generate_route_pdf
from routing.gpx import generate_route_gpx

logger = logging.getLogger(__name__)

# Ostrava metropolitan area bounds (generous - covers all districts)
_OSTRAVA_LAT_MIN, _OSTRAVA_LAT_MAX = 49.73, 49.92
_OSTRAVA_LNG_MIN, _OSTRAVA_LNG_MAX = 18.09, 18.47


def _within_ostrava(lat: float, lng: float) -> bool:
    return (_OSTRAVA_LAT_MIN <= lat <= _OSTRAVA_LAT_MAX and
            _OSTRAVA_LNG_MIN <= lng <= _OSTRAVA_LNG_MAX)


# ---------------------------------------------------------------------------
# POST /api/route/
# ---------------------------------------------------------------------------

@ratelimit(key='ip', rate='20/h', method='POST', block=True)
@api_view(['POST'])
def compute_route(request):
    """
    Geocode start/end, compute up to 3 alternative routes, score each,
    persist a SavedRoute for every alternative, return full JSON response.
    """
    start_raw = (request.data.get('start') or '').strip()
    end_raw   = (request.data.get('end')   or '').strip()

    if not start_raw or not end_raw:
        return Response(
            {'error': 'Zadejte prosím výchozí bod i cíl.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Normalise for cache lookup
    cache_key_start = start_raw.lower()
    cache_key_end   = end_raw.lower()

    cached = RouteCache.objects.filter(
        start_normalized=cache_key_start,
        end_normalized=cache_key_end,
    ).first()
    if cached:
        logger.info("Cache hit: %s → %s", start_raw, end_raw)
        return Response(cached.result_json)

    # --- Geocode (or use pre-resolved coords from map click) ---
    try:
        start_lat_r = request.data.get('start_lat')
        start_lng_r = request.data.get('start_lng')
        if start_lat_r is not None and start_lng_r is not None:
            start_geo = {'address': start_raw, 'lat': float(start_lat_r), 'lng': float(start_lng_r)}
        else:
            start_geo = geocode(start_raw)

        end_lat_r = request.data.get('end_lat')
        end_lng_r = request.data.get('end_lng')
        if end_lat_r is not None and end_lng_r is not None:
            end_geo = {'address': end_raw, 'lat': float(end_lat_r), 'lng': float(end_lng_r)}
        else:
            end_geo = geocode(end_raw)
    except (ValueError, TypeError) as exc:
        return Response({'error': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    # --- Ostrava geo-restriction ---
    if not _within_ostrava(start_geo['lat'], start_geo['lng']):
        return Response(
            {'error': 'Výchozí bod je mimo oblast Ostravy. BikeOstrava slouží výhradně cyklistům v Ostravě a okolí.'},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if not _within_ostrava(end_geo['lat'], end_geo['lng']):
        return Response(
            {'error': 'Cíl je mimo oblast Ostravy. BikeOstrava slouží výhradně cyklistům v Ostravě a okolí.'},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    # --- Routing (1–3 alternatives) ---
    try:
        route_results = get_route(
            start_geo['lat'], start_geo['lng'],
            end_geo['lat'],   end_geo['lng'],
        )
    except ValueError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    # --- Green zones, accident points and POIs - fetched in parallel ---
    primary_coords = route_results[0]['coordinates']

    def _fetch_green_zones():
        try:
            return get_green_zones(primary_coords)
        except Exception as exc:
            logger.warning("Green zones fetch failed: %s", exc)
            return []

    def _fetch_cyclist_pois():
        try:
            return get_cyclist_pois(primary_coords)
        except Exception as exc:
            logger.warning("Cyclist POIs fetch failed: %s", exc)
            return []

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_green    = pool.submit(_fetch_green_zones)
        f_accidents = pool.submit(get_accidents_near_route, primary_coords)
        f_pois     = pool.submit(_fetch_cyclist_pois)
        green_zones     = f_green.result()
        accident_points = f_accidents.result()
        cyclist_pois    = f_pois.result()

    # --- Score + persist each alternative ---
    alternatives = []
    for i, route_result in enumerate(route_results):
        coords        = route_result['coordinates']
        road_segments = route_result.get('road_segments', [])

        # Per-route accident count for accurate safety scoring
        acc_count = count_accidents_near_route(coords)
        score     = compute_safety_score(coords, road_segments, green_zones, acc_count)

        route_id      = uuid.uuid4()
        route_geojson = {
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': coords},
            'properties': {},
        }

        alt_payload = {
            'id':                str(route_id),
            'index':             i,
            'start':             start_geo,
            'end':               end_geo,
            'safety_score':      score['overall'],
            'distance_km':       route_result['distance_km'],
            'duration_min':      route_result['duration_min'],
            'route_geojson':     route_geojson,
            'score_breakdown': {
                'bike_path_coverage':  score['bike_path_coverage'],
                'accident_density':    score['accident_density'],
                'green_zone_coverage': score['green_zone_coverage'],
            },
            'highlights':        score['highlights'],
            'road_segments':     road_segments,
            'elevation_profile': route_result.get('elevation_profile', []),
            'instructions':      route_result.get('instructions', []),
        }
        alternatives.append(alt_payload)

        try:
            SavedRoute.objects.create(
                id=route_id,
                start_address=start_geo['address'],
                end_address=end_geo['address'],
                start_lat=start_geo['lat'],
                start_lng=start_geo['lng'],
                end_lat=end_geo['lat'],
                end_lng=end_geo['lng'],
                safety_score=score['overall'],
                distance_km=route_result['distance_km'],
                duration_min=route_result['duration_min'],
                route_data=alt_payload,
            )
        except Exception as exc:
            logger.error("Failed to persist route alt %d: %s", i, exc)

    # Sort alternatives by safety score (best first)
    alternatives.sort(key=lambda a: a['safety_score'], reverse=True)

    # Build final response: best alternative data at top level (backwards-compat)
    # plus shared accident_points and the full alternatives list.
    best = alternatives[0]
    # Slim down green zones: cap at 60, simplify coords (keep every 3rd point)
    def _simplify_zone(z):
        coords = z['coords']
        step = max(1, len(coords) // 30)   # target ≤ 30 points per polygon
        simplified = coords[::step]
        # ensure polygon is closed
        if simplified and simplified[0] != simplified[-1]:
            simplified.append(simplified[0])
        return {'coords': simplified, 'name': z['name'], 'zone_type': z['zone_type']}

    green_zones_slim = [_simplify_zone(z) for z in green_zones[:60]]

    response_payload = {
        **best,
        'accident_points': accident_points,
        'alternatives':    alternatives,
        'green_zones':     green_zones_slim,
        'cyclist_pois':    cyclist_pois,
    }

    try:
        RouteCache.objects.update_or_create(
            start_normalized=cache_key_start,
            end_normalized=cache_key_end,
            defaults={'result_json': response_payload},
        )
    except Exception as exc:
        logger.error("Failed to cache route: %s", exc)

    return Response(response_payload, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# GET /api/route/<id>/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def get_route_by_id(request, route_id):
    try:
        saved = SavedRoute.objects.get(id=route_id)
    except SavedRoute.DoesNotExist:
        return Response({'error': 'Trasa nenalezena.'}, status=status.HTTP_404_NOT_FOUND)
    except Exception:
        return Response({'error': 'Neplatné ID trasy.'}, status=status.HTTP_400_BAD_REQUEST)

    return Response(saved.route_data)


# ---------------------------------------------------------------------------
# GET /api/route/<id>/pdf/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def download_pdf(request, route_id):
    try:
        saved = SavedRoute.objects.get(id=route_id)
    except SavedRoute.DoesNotExist:
        return Response({'error': 'Trasa nenalezena.'}, status=status.HTTP_404_NOT_FOUND)
    except Exception:
        return Response({'error': 'Neplatné ID trasy.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        pdf_bytes = generate_route_pdf(saved)
    except Exception as exc:
        logger.error("PDF generation failed: %s", exc)
        return Response(
            {'error': 'Generování PDF selhalo. Zkuste to prosím znovu.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="bikeostrava-route-{route_id}.pdf"'
    )
    return response


# ---------------------------------------------------------------------------
# GET /api/route/<id>/gpx/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def download_gpx(request, route_id):
    try:
        saved = SavedRoute.objects.get(id=route_id)
    except SavedRoute.DoesNotExist:
        return Response({'error': 'Trasa nenalezena.'}, status=status.HTTP_404_NOT_FOUND)
    except Exception:
        return Response({'error': 'Neplatné ID trasy.'}, status=status.HTTP_400_BAD_REQUEST)

    gpx_bytes = generate_route_gpx(saved)
    response  = HttpResponse(gpx_bytes, content_type='application/gpx+xml')
    response['Content-Disposition'] = (
        f'attachment; filename="bikeostrava-route-{route_id}.gpx"'
    )
    return response
