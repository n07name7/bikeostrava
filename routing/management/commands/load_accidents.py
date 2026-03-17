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
import io
import logging
from datetime import date, datetime
from html.parser import HTMLParser

import rarfile
import requests
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from pyproj import Transformer

from routing.models import AccidentPoint

logger = logging.getLogger(__name__)

# Czech Police data URL pattern
POLICE_URL = "https://policie.gov.cz/soubor/data-web-{mm:02d}-{yyyy}-rar.aspx"

HEADERS = {"User-Agent": "Mozilla/5.0 BikeOstrava/1.0 (research)"}

# Ostrava bounding box with margin (WGS84)
OSTRAVA_LAT_MIN, OSTRAVA_LAT_MAX = 49.70, 50.00
OSTRAVA_LNG_MIN, OSTRAVA_LNG_MAX = 17.90, 18.60

# S-JTSK (EPSG:5514) -> WGS84 (EPSG:4326) transformer
# always_xy=True: transform(easting, northing) -> (lng, lat)
_TRANSFORMER = Transformer.from_crs("EPSG:5514", "EPSG:4326", always_xy=True)


class Command(BaseCommand):
    help = "Load accident data from policie.gov.cz (Czech Police real data)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--months", type=int, default=24,
            help="How many recent months to load (default 24)",
        )
        parser.add_argument(
            "--clear", action="store_true",
            help="Clear existing data before import",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            count = AccidentPoint.objects.count()
            AccidentPoint.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Deleted {count} records."))

        existing = AccidentPoint.objects.count()
        if existing > 0 and not options["clear"]:
            self.stdout.write(self.style.SUCCESS(
                f"Data already loaded ({existing} records). "
                "Use --clear for a fresh import."
            ))
            return

        months_to_try = self._months_to_try(options["months"])
        all_records = {}  # p1 -> record (dedup)

        for yyyy, mm in months_to_try:
            self.stdout.write(f"Loading {mm:02d}/{yyyy}...")
            records = self._load_month(yyyy, mm)
            if records is None:
                self.stdout.write(f"  -> file unavailable, skipping")
                continue
            self.stdout.write(f"  -> got {len(records)} accidents in Ostrava area")
            for p1, rec in records.items():
                all_records[p1] = rec

        if not all_records:
            raise CommandError(
                "Failed to load data for any month. "
                "Check internet connection and policie.gov.cz availability."
            )

        self._bulk_import(list(all_records.values()))
        self.stdout.write(self.style.SUCCESS(
            f"Imported {len(all_records)} accident records from Ostrava."
        ))

    # ── Build list of months ──────────────────────────────────────────────────

    def _months_to_try(self, n_months: int) -> list[tuple[int, int]]:
        """List of (year, month) for the last n_months, newest first."""
        today = date.today()
        result = []
        y, m = today.year, today.month
        for _ in range(n_months):
            result.append((y, m))
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        return result

    # ── Load a single month ───────────────────────────────────────────────────

    def _load_month(self, yyyy: int, mm: int) -> dict | None:
        """Download and parse RAR for a given month. None if unavailable."""
        url = POLICE_URL.format(mm=mm, yyyy=yyyy)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Download error %s: %s", url, exc)
            return None

        try:
            rf = rarfile.RarFile(io.BytesIO(resp.content))
        except Exception as exc:
            logger.warning("RAR extraction error (%d/%d): %s", mm, yyyy, exc)
            return None

        # --- Coordinates from IntGPS.xls (all accidents in Ostrava) ---
        gps_data = {}  # p1 -> (lat, lng)
        try:
            raw_gps = rf.read("IntGPS.xls").decode("cp1250", errors="replace")
            rows_gps = _parse_html_table(raw_gps)
            if rows_gps:
                header = rows_gps[0]
                idx = {h: i for i, h in enumerate(header)}
                for row in rows_gps[1:]:
                    try:
                        p1 = row[idx["p1"]].strip()
                        d_val = float(row[idx["d"]].replace(",", "."))  # northing
                        e_val = float(row[idx["e"]].replace(",", "."))  # easting
                        # transform(easting, northing) -> (lng, lat)
                        lng, lat = _TRANSFORMER.transform(e_val, d_val)
                        if _in_ostrava(lat, lng):
                            gps_data[p1] = (lat, lng)
                    except (KeyError, ValueError, IndexError):
                        continue
        except Exception as exc:
            logger.warning("IntGPS parse error (%d/%d): %s", mm, yyyy, exc)
            return None

        if not gps_data:
            return {}

        # --- Filter: only accidents involving cyclists (Ichodci p29=2) ---
        # Cyclists are in Ichodci.xls (not IVozidla), p29=2 = cyklista
        try:
            raw_c = rf.read("Ichodci.xls").decode("cp1250", errors="replace")
            rows_c = _parse_html_table(raw_c)
            if rows_c:
                hc = rows_c[0]
                ic = {h: i for i, h in enumerate(hc)}
                bike_p1 = {
                    row[ic["p1"]].strip()
                    for row in rows_c[1:]
                    if len(row) > ic.get("p29", 999)
                    and row[ic["p29"]].strip() == "2"  # 2 = cyklista
                }
                # Keep only Ostrava cycling accidents
                gps_data = {p1: coords for p1, coords in gps_data.items() if p1 in bike_p1}
        except Exception as exc:
            logger.warning("Ichodci filter error (%d/%d): %s", mm, yyyy, exc)
            # If file unavailable - keep all Ostrava accidents as fallback

        if not gps_data:
            return {}

        # --- Date and severity from Inehody.xls ---
        meta_data = {}  # p1 -> (date, severity)
        try:
            raw_n = rf.read("Inehody.xls").decode("cp1250", errors="replace")
            rows_n = _parse_html_table(raw_n)
            if rows_n:
                header = rows_n[0]
                idx = {h: i for i, h in enumerate(header)}
                for row in rows_n[1:]:
                    try:
                        p1 = row[idx["p1"]].strip()
                        if p1 not in gps_data:
                            continue
                        date_str = row[idx.get("p2a", -1)].strip() if "p2a" in idx else ""
                        d_obj = _parse_date(date_str)
                        # Severity by casualties: p13a=fatal, p13b=serious injury, p13c=minor injury
                        def _int(col):
                            try: return int(row[idx[col]].strip() or "0")
                            except: return 0
                        if "p13a" in idx and _int("p13a") > 0:
                            severity = "smrtelna"
                        elif "p13b" in idx and _int("p13b") > 0:
                            severity = "tezka"
                        elif "p13c" in idx and _int("p13c") > 0:
                            severity = "lehka"
                        else:
                            severity = "s hmotnou skodou"
                        meta_data[p1] = (d_obj, severity)
                    except (KeyError, IndexError):
                        continue
        except Exception as exc:
            logger.warning("Inehody parse error (%d/%d): %s", mm, yyyy, exc)

        # --- Assemble results ---
        result = {}
        for p1, (lat, lng) in gps_data.items():
            acc_date, severity = meta_data.get(p1, (None, ""))
            result[p1] = {"lat": lat, "lng": lng, "date": acc_date, "severity": severity}
        return result

    # ── Persist to DB ─────────────────────────────────────────────────────────

    def _bulk_import(self, records: list):
        points = []
        for r in records:
            try:
                points.append(AccidentPoint(
                    location=Point(float(r["lng"]), float(r["lat"]), srid=4326),
                    date=r.get("date"),
                    severity=r.get("severity", ""),
                ))
            except Exception as exc:
                logger.debug("Skipping record %s: %s", r, exc)

        with transaction.atomic():
            AccidentPoint.objects.bulk_create(points, batch_size=500, ignore_conflicts=True)
        self.stdout.write(f"Saved {len(points)} records to DB.")


# ── Helper functions ──────────────────────────────────────────────────────────

def _in_ostrava(lat: float, lng: float) -> bool:
    return (OSTRAVA_LAT_MIN <= lat <= OSTRAVA_LAT_MAX and
            OSTRAVA_LNG_MIN <= lng <= OSTRAVA_LNG_MAX)


def _parse_date(s: str) -> date | None:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _parse_html_table(html: str) -> list[list[str]]:
    """Parse an HTML table (XLS format from Czech Police) into a list of rows."""
    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows: list[list[str]] = []
            self._cur: list[str] = []
            self._in_cell = False
            self._text = ""

        def handle_starttag(self, tag, attrs):
            if tag in ("td", "th"):
                self._in_cell = True
                self._text = ""
            elif tag == "tr":
                self._cur = []

        def handle_endtag(self, tag):
            if tag in ("td", "th"):
                self._cur.append(self._text.strip())
                self._in_cell = False
            elif tag == "tr" and self._cur:
                self.rows.append(self._cur)
                self._cur = []

        def handle_data(self, data):
            if self._in_cell:
                self._text += data

    p = _P()
    p.feed(html)
    return p.rows
