"""
Management command: load_accidents

Loads real traffic accident data from the Czech Police portal (policie.gov.cz).

The police publish monthly RAR archives with XLS files:
  https://policie.gov.cz/soubor/data-web-{MM}-{YYYY}-rar.aspx

Inside each archive:
  IntGPS.xls   - coordinates (S-JTSK EPSG:5514), join by p1
  Inehody.xls  - date (p2a), severity (p4a), join by p1

S-JTSK -> WGS84 conversion via pyproj.
Filtered by Ostrava bounding box.

Usage:
  python manage.py load_accidents [--months 3] [--clear]
"""
import json
import logging
import os
from django.core.management.base import BaseCommand
from django.db import transaction
from routing.models import AccidentPoint

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Load accident data from pre-fetched accidents.json"

    def add_arguments(self, parser):
        parser.add_argument("--months", type=int, default=24)
        parser.add_argument("--clear", action="store_true")

    def handle(self, *args, **options):
        # Clear existing
        count = AccidentPoint.objects.count()
        AccidentPoint.objects.all().delete()
        self.stdout.write(self.style.WARNING(f"Deleted {count} records."))

        json_path = os.path.join(os.path.dirname(__file__), "accidents.json")
        if not os.path.exists(json_path):
            self.stdout.write(self.style.ERROR(f"File not found: {json_path}"))
            return

        with open(json_path, "r") as f:
            records = json.load(f)

        points = []
        for r in records:
            try:
                points.append(AccidentPoint(
                    latitude=float(r["lat"]),
                    longitude=float(r["lng"]),
                    date=r.get("date"),
                    severity=r.get("severity", ""),
                ))
            except Exception as exc:
                logger.debug("Skipping record %s: %s", r, exc)

        with transaction.atomic():
            AccidentPoint.objects.bulk_create(points, batch_size=500, ignore_conflicts=True)
            
        self.stdout.write(self.style.SUCCESS(f"Saved {len(points)} records to DB."))

