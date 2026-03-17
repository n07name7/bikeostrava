# BikeOstrava

Cycling route planner for Ostrava, Czech Republic.
Enter start and destination, get the safest route with a 0-100 safety score based on bike paths, green zones, and traffic accident data.

---

## Features

- **Safety scoring** - composite 0-100 score from bike path coverage, accident density, and green zone proximity
- **Up to 3 alternative routes** ranked by safety
- **Accident heatmap** - real Czech Police data (policie.gov.cz), auto-refreshed monthly
- **Elevation profile** with interactive map hover
- **PDF/GPX export** for offline use
- **Geocoding** via Photon/Nominatim (OpenStreetMap) - no API key needed
- **Routing** via local GraphHopper (optional) with OSRM as free fallback
- **Rate limiting** - 20 requests/hour per IP
- **Route caching** - identical queries served from DB

---

## Prerequisites

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.11+ | |
| PostgreSQL | 14+ | with PostGIS extension |
| GDAL | 3.x | system library, required by GeoDjango |
| unrar | any | for extracting Czech Police accident archives |
| Java | 11+ | only if running local GraphHopper |

### Install system dependencies

**Ubuntu / Debian:**
```bash
sudo apt-get install -y libgdal-dev gdal-bin postgis postgresql-14-postgis-3 unrar default-jre
```

**macOS (Homebrew):**
```bash
brew install gdal postgis unrar
brew install --cask temurin  # Java, only for GraphHopper
```

**Fedora / RHEL:**
```bash
sudo dnf install gdal gdal-devel postgis unrar java-11-openjdk
```

---

## Setup

```bash
# 1. Clone
git clone https://github.com/n07name7/bikeostrava.git
cd bikeostrava

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install Python dependencies
pip install GDAL=="$(gdal-config --version)"  # must match system GDAL version
pip install -r requirements.txt

# 4. Create PostGIS database
createdb bikeostrava
psql bikeostrava -c "CREATE EXTENSION IF NOT EXISTS postgis;"

# 5. Configure environment
cp .env.example .env
# Edit .env - set PGUSER and PGPASSWORD for your PostgreSQL setup

# 6. Apply migrations and create cache table
python manage.py migrate
python manage.py createcachetable

# 7. Load accident data (optional, downloads from policie.gov.cz, ~2 min)
python manage.py load_accidents
# Loads last 24 months of cycling accidents in Ostrava from Czech Police open data.
# Requires internet access. If policie.gov.cz is unreachable, skip this step -
# the app works without it, but accident scoring will show as unavailable.

# 8. Start dev server
python manage.py runserver
```

Open **http://127.0.0.1:8000**.

> **Note on GDAL:** If `pip install GDAL` fails, try installing via conda instead:
> ```bash
> conda install -c conda-forge gdal
> ```
> GDAL can be tricky to install via pip because it requires the system GDAL library
> headers. The conda package bundles everything together.

---

## GraphHopper (optional)

The app works without GraphHopper by falling back to OSRM (public API). However, the local GraphHopper instance provides:

- `bike_safe` routing profile optimized for safety
- Road class details per segment (cycleway vs residential vs primary)
- Elevation data (SRTM)
- Alternative routes

### Setup

1. Download [GraphHopper Web JAR](https://github.com/graphhopper/graphhopper/releases) into `graphhopper/`:
   ```bash
   cd graphhopper
   wget https://repo1.maven.org/maven2/com/graphhopper/graphhopper-web/11.0/graphhopper-web-11.0.jar \
        -O graphhopper-web.jar
   ```

2. Download Czech Republic OSM data (~870 MB):
   ```bash
   wget https://download.geofabrik.de/europe/czech-republic-latest.osm.pbf
   ```

3. Start GraphHopper (first run builds the graph, takes a few minutes):
   ```bash
   ./start.sh
   # Runs on http://localhost:8991
   ```

The app auto-detects the local instance at `localhost:8991`. No config changes needed.

---

## API

### POST `/api/route/`

Calculate a cycling route with safety score.

**Request:**
```json
{
  "start": "VSB-TUO Ostrava Poruba",
  "end":   "Masarykovo namesti Ostrava"
}
```

**Response:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "start":  { "address": "...", "lat": 49.83, "lng": 18.16 },
  "end":    { "address": "...", "lat": 49.82, "lng": 18.26 },
  "safety_score": 78,
  "distance_km": 6.2,
  "duration_min": 22,
  "route_geojson": { "type": "Feature", "geometry": { "type": "LineString", "coordinates": ["..."] } },
  "score_breakdown": {
    "bike_path_coverage": 85,
    "accident_density": 70,
    "green_zone_coverage": 65
  },
  "highlights": [
    "82% trasy vede po cyklostezce nebo cyklopruhu",
    "2 nehody zaznamenany v okoli trasy - budte opatrni"
  ],
  "elevation_profile": [[0.0, 245.3], [0.1, 247.1], "..."],
  "road_segments": ["..."],
  "accident_points": [
    { "lat": 49.83, "lng": 18.22, "severity": "lehka", "date": "2024-07-14" }
  ],
  "alternatives": ["..."]
}
```

Rate limit: **20 requests/hour per IP**.

### GET `/api/route/<id>/`
Retrieve a cached route by UUID.

### GET `/api/route/<id>/pdf/`
Download PDF summary.

### GET `/api/route/<id>/gpx/`
Download GPX track (compatible with Garmin, Wahoo, Strava).

---

## Safety Scoring

| Signal | Weight | Formula |
|---|---|---|
| Bike path coverage | 45% | 0% -> 20, 50% -> 60, 90%+ -> 100 |
| Accident density (inverse) | 35% | 0 accidents -> 100, 3 -> 70, 10+ -> 30 |
| Green zone proximity | 20% | 0% -> 40, 50% -> 70, 80%+ -> 100 |

**Overall = bike * 0.45 + accidents * 0.35 + green * 0.20**

Bike path coverage is calculated from GraphHopper road class data when available (cycleway, path, footway = bike-friendly), or from Overpass API proximity as fallback.

---

## Project Structure

```
bikeostrava/
├── bikeostrava/
│   ├── settings.py          # Django settings (python-decouple for env vars)
│   ├── urls.py              # Root URL config
│   └── wsgi.py
├── routing/
│   ├── models.py            # AccidentPoint, RouteCache, SavedRoute
│   ├── views.py             # API endpoints
│   ├── urls.py
│   ├── pdf.py               # ReportLab PDF generator
│   ├── gpx.py               # GPX track export
│   ├── services/
│   │   ├── geocoder.py      # Photon + Nominatim geocoding
│   │   ├── overpass.py      # OSM bike paths + green zones
│   │   ├── router.py        # GraphHopper / OSRM routing
│   │   ├── accidents.py     # PostGIS accident queries
│   │   └── scorer.py        # Safety scoring engine
│   ├── templates/
│   │   └── index.html       # Single-page frontend (Leaflet.js)
│   └── management/commands/
│       └── load_accidents.py # Czech Police data importer
├── graphhopper/
│   ├── config.yml           # GraphHopper config (bike + bike_safe profiles)
│   └── start.sh             # GH launcher script
├── static/
├── Procfile                 # Gunicorn start command
├── railway.toml             # Railway deploy config
├── requirements.txt
├── start.sh                 # Full-stack dev launcher (GH + Django)
└── .env.example
```

---

## Deployment (Railway)

1. Fork this repo and push to GitHub.
2. Create a new Railway project and connect the repo.
3. Add a **PostgreSQL** plugin (Railway auto-provisions PostGIS).
4. Set environment variables:
   ```
   SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_urlsafe(50))">
   DEBUG=False
   ALLOWED_HOSTS=<your-app>.railway.app
   PGDATABASE=railway
   PGUSER=postgres
   PGPASSWORD=<from Railway Postgres plugin>
   PGHOST=<from Railway Postgres plugin>
   PGPORT=5432
   ```
5. Deploy. Railway runs the Procfile:
   ```
   python manage.py migrate && python manage.py collectstatic --noinput && gunicorn bikeostrava.wsgi:application
   ```

> **Note:** GraphHopper is not available on Railway (needs JVM + large OSM files).
> Routes will use the OSRM fallback, which works but without road class details and elevation data.

---

## Tech Stack

- **Backend:** Django 4.2, Django REST Framework
- **Database:** PostgreSQL 14+ with PostGIS
- **Geocoding:** Photon / Nominatim (OpenStreetMap)
- **Routing:** GraphHopper (local, optional) / OSRM (public fallback)
- **Map data:** Overpass API (bike paths, green zones from OSM)
- **Accidents:** Czech Police open data (policie.gov.cz)
- **Frontend:** Leaflet.js, vanilla JS, dark theme
- **PDF:** ReportLab with QR codes
- **Deployment:** Railway.app (Nixpacks)

---

## License

MIT
