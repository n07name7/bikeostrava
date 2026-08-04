"""
Unit tests for the safety scoring engine.
"""
from django.test import TestCase

from routing.services.scorer import (
    _bike_path_score,
    _accident_score,
    _green_zone_score,
    bike_pct_from_segments,
    compute_safety_score,
)


class BikePthScoreTests(TestCase):
    """Piecewise linear: 0%→20, 50%→60, 90%+→100."""

    def test_zero_coverage(self):
        self.assertEqual(_bike_path_score(0), 20)

    def test_mid_coverage(self):
        self.assertEqual(_bike_path_score(50), 60)

    def test_high_coverage(self):
        self.assertEqual(_bike_path_score(90), 100)

    def test_full_coverage(self):
        self.assertEqual(_bike_path_score(100), 100)

    def test_quarter_coverage(self):
        score = _bike_path_score(25)
        self.assertGreater(score, 20)
        self.assertLess(score, 60)


class AccidentScoreTests(TestCase):
    """Piecewise linear: 0→100, 3→70, 10+→30."""

    def test_no_accidents(self):
        self.assertEqual(_accident_score(0), 100)

    def test_three_accidents(self):
        self.assertEqual(_accident_score(3), 70)

    def test_ten_plus_accidents(self):
        self.assertEqual(_accident_score(10), 30)
        self.assertEqual(_accident_score(50), 30)

    def test_one_accident(self):
        score = _accident_score(1)
        self.assertGreater(score, 70)
        self.assertLess(score, 100)


class GreenZoneScoreTests(TestCase):
    """Piecewise linear: 0%→40, 50%→70, 80%+→100."""

    def test_zero_coverage(self):
        self.assertEqual(_green_zone_score(0), 40)

    def test_mid_coverage(self):
        self.assertEqual(_green_zone_score(50), 70)

    def test_high_coverage(self):
        self.assertEqual(_green_zone_score(80), 100)

    def test_full_coverage(self):
        self.assertEqual(_green_zone_score(100), 100)


class BikePctFromSegmentsTests(TestCase):
    """Test bike percentage calculation from road segments."""

    def test_empty_segments(self):
        self.assertEqual(bike_pct_from_segments([]), 0.0)

    def test_all_bike(self):
        segments = [
            {"coords": [[18.2, 49.8], [18.3, 49.8]], "category": "bike"},
        ]
        self.assertAlmostEqual(bike_pct_from_segments(segments), 100.0, delta=1)

    def test_no_bike(self):
        segments = [
            {"coords": [[18.2, 49.8], [18.3, 49.8]], "category": "busy"},
        ]
        self.assertAlmostEqual(bike_pct_from_segments(segments), 0.0, delta=1)

    def test_mixed(self):
        segments = [
            {"coords": [[18.2, 49.8], [18.25, 49.8]], "category": "bike"},
            {"coords": [[18.25, 49.8], [18.3, 49.8]], "category": "neutral"},
        ]
        pct = bike_pct_from_segments(segments)
        self.assertGreater(pct, 40)
        self.assertLess(pct, 60)


class ComputeSafetyScoreTests(TestCase):
    """Integration tests for the full compute_safety_score function."""

    def _make_route(self, n=10):
        """Generate a simple east-west route with n points."""
        return [[18.2 + i * 0.01, 49.8] for i in range(n)]

    def test_returns_required_keys(self):
        result = compute_safety_score(
            self._make_route(),
            road_segments=[],
            green_zones=[],
            accident_count=0,
        )
        self.assertIn("overall", result)
        self.assertIn("bike_path_coverage", result)
        self.assertIn("accident_density", result)
        self.assertIn("green_zone_coverage", result)
        self.assertIn("highlights", result)

    def test_overall_in_range(self):
        result = compute_safety_score(
            self._make_route(),
            road_segments=[],
            green_zones=[],
            accident_count=5,
        )
        self.assertGreaterEqual(result["overall"], 0)
        self.assertLessEqual(result["overall"], 100)

    def test_no_accidents_gives_high_accident_score(self):
        result = compute_safety_score(
            self._make_route(),
            road_segments=[],
            green_zones=[],
            accident_count=0,
        )
        self.assertEqual(result["accident_density"], 100)

    def test_null_accidents_gives_none(self):
        result = compute_safety_score(
            self._make_route(),
            road_segments=[],
            green_zones=[],
            accident_count=None,
        )
        self.assertIsNone(result["accident_density"])

    def test_highlights_is_list_of_strings(self):
        result = compute_safety_score(
            self._make_route(),
            road_segments=[],
            green_zones=[],
            accident_count=2,
        )
        self.assertIsInstance(result["highlights"], list)
        for h in result["highlights"]:
            self.assertIsInstance(h, str)

    def test_all_bike_segments_gives_high_bike_score(self):
        coords = [[18.2, 49.8], [18.25, 49.8], [18.3, 49.8]]
        segments = [
            {"coords": [[18.2, 49.8], [18.25, 49.8]], "category": "bike", "road_class": "cycleway"},
            {"coords": [[18.25, 49.8], [18.3, 49.8]], "category": "bike", "road_class": "cycleway"},
        ]
        result = compute_safety_score(coords, segments, [], 0)
        self.assertGreaterEqual(result["bike_path_coverage"], 90)
