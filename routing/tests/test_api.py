"""
Tests for API endpoints.
"""
import uuid
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient


@override_settings(
    OSTRAVA_BBOX={'lat_min': 49.77, 'lat_max': 49.87, 'lng_min': 18.10, 'lng_max': 18.35},
)
class ComputeRouteAPITests(TestCase):
    """Tests for POST /api/route/."""

    def setUp(self):
        self.client = APIClient()
        self.url = '/api/route/'

    def test_missing_start(self):
        resp = self.client.post(self.url, {'end': 'Poruba'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_missing_end(self):
        resp = self.client.post(self.url, {'start': 'VŠB-TUO'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_empty_body(self):
        resp = self.client.post(self.url, {}, format='json')
        self.assertEqual(resp.status_code, 400)

    @patch('routing.views.geocode')
    def test_geocode_outside_ostrava(self, mock_geocode):
        """Points outside Ostrava should return 422."""
        mock_geocode.return_value = {'address': 'Praha', 'lat': 50.08, 'lng': 14.42}
        resp = self.client.post(self.url, {'start': 'Praha', 'end': 'Brno'}, format='json')
        self.assertEqual(resp.status_code, 422)

    @patch('routing.views.get_accidents_near_route', return_value=[])
    @patch('routing.views.count_accidents_near_route', return_value=0)
    @patch('routing.views.get_map_context', return_value=([], []))
    @patch('routing.views.get_route')
    @patch('routing.views.geocode')
    def test_successful_route(self, mock_geocode, mock_route, mock_ctx, mock_count, mock_accidents):
        """Full successful route request with mocked external APIs."""
        mock_geocode.side_effect = [
            {'address': 'VŠB-TUO, Ostrava', 'lat': 49.8345, 'lng': 18.1633},
            {'address': 'Centrum, Ostrava', 'lat': 49.8362, 'lng': 18.2916},
        ]
        mock_route.return_value = [{
            'coordinates': [[18.1633, 49.8345], [18.22, 49.83], [18.2916, 49.8362]],
            'distance_km': 9.3,
            'duration_min': 30,
            'road_segments': [
                {'coords': [[18.1633, 49.8345], [18.2916, 49.8362]], 'road_class': 'cycleway', 'category': 'bike'},
            ],
            'elevation_profile': [],
            'instructions': [],
        }]

        resp = self.client.post(self.url, {
            'start': 'VŠB-TUO Ostrava',
            'end': 'Centrum Ostrava',
        }, format='json')

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('safety_score', data)
        self.assertIn('route_geojson', data)
        self.assertIn('alternatives', data)
        self.assertIsInstance(data['safety_score'], int)
        self.assertGreaterEqual(data['safety_score'], 0)
        self.assertLessEqual(data['safety_score'], 100)


class GetRouteByIdAPITests(TestCase):
    """Tests for GET /api/route/<id>/."""

    def setUp(self):
        self.client = APIClient()

    def test_nonexistent_route(self):
        fake_id = uuid.uuid4()
        resp = self.client.get(f'/api/route/{fake_id}/')
        self.assertEqual(resp.status_code, 404)

    def test_invalid_uuid(self):
        resp = self.client.get('/api/route/not-a-uuid/')
        self.assertEqual(resp.status_code, 404)  # Django URL routing returns 404 for invalid UUID
