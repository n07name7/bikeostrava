"""
Unit tests for geo_utils — haversine distance and point-to-segment distance.
"""
from django.test import TestCase

from routing.services.geo_utils import haversine_m, point_to_segment_dist_m


class HaversineTests(TestCase):
    """Tests for the haversine_m function."""

    def test_same_point_returns_zero(self):
        self.assertAlmostEqual(haversine_m(18.2, 49.8, 18.2, 49.8), 0.0, places=2)

    def test_known_distance_ostrava(self):
        """VŠB-TUO ↔ Masarykovo náměstí ≈ 6.5 km (straight line)."""
        dist = haversine_m(18.1633, 49.8345, 18.2916, 49.8362)
        self.assertAlmostEqual(dist, 9300, delta=500)

    def test_symmetry(self):
        d1 = haversine_m(18.2, 49.8, 18.3, 49.9)
        d2 = haversine_m(18.3, 49.9, 18.2, 49.8)
        self.assertAlmostEqual(d1, d2, places=2)

    def test_small_distance(self):
        """~11 metres between two very close points."""
        dist = haversine_m(18.2000, 49.8000, 18.2001, 49.8001)
        self.assertGreater(dist, 5)
        self.assertLess(dist, 25)


class PointToSegmentTests(TestCase):
    """Tests for point_to_segment_dist_m function."""

    def test_point_on_segment_endpoint(self):
        """Point at segment start → distance should be ~0."""
        d = point_to_segment_dist_m(18.2, 49.8, 18.2, 49.8, 18.3, 49.9)
        self.assertAlmostEqual(d, 0.0, places=1)

    def test_degenerate_segment(self):
        """Segment is a single point (A == B) → should return haversine(P, A)."""
        d1 = point_to_segment_dist_m(18.2, 49.8, 18.3, 49.9, 18.3, 49.9)
        d2 = haversine_m(18.2, 49.8, 18.3, 49.9)
        self.assertAlmostEqual(d1, d2, places=1)

    def test_perpendicular_projection(self):
        """Point near the midpoint of a segment — distance should be small."""
        # Segment runs east-west; point is slightly north
        d = point_to_segment_dist_m(
            18.25, 49.8001,   # point slightly north
            18.20, 49.8000,   # segment start
            18.30, 49.8000,   # segment end
        )
        # Should be roughly 11 metres (0.0001 deg latitude ≈ 11m)
        self.assertLess(d, 20)
        self.assertGreater(d, 5)
