import uuid
from django.contrib.gis.db import models


class AccidentPoint(models.Model):
    """Single traffic accident location loaded from opendata.ostrava.cz."""

    location = models.PointField(srid=4326)
    date = models.DateField(null=True, blank=True)
    severity = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['date']),
        ]

    def __str__(self):
        return f"Accident at {self.location} on {self.date} [{self.severity}]"


class RouteCache(models.Model):
    """Cache computed routes so repeated identical queries are instant."""

    start_normalized = models.CharField(max_length=500, db_index=True)
    end_normalized = models.CharField(max_length=500, db_index=True)
    result_json = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('start_normalized', 'end_normalized')]
        indexes = [
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"Cache: {self.start_normalized} → {self.end_normalized}"


class SavedRoute(models.Model):
    """Persisted route with full scoring data, keyed by UUID for PDF download."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    start_address = models.CharField(max_length=500)
    end_address = models.CharField(max_length=500)
    start_lat = models.FloatField()
    start_lng = models.FloatField()
    end_lat = models.FloatField()
    end_lng = models.FloatField()
    safety_score = models.IntegerField()
    distance_km = models.FloatField(default=0)
    duration_min = models.IntegerField(default=0)
    route_data = models.JSONField()  # full API response stored here
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Route {self.id}: {self.start_address} → {self.end_address} (score {self.safety_score})"
