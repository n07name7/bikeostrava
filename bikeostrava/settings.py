import os
from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='django-insecure-change-me-in-production-bikeostrava-2024')

DEBUG = config('DEBUG', default=False, cast=bool)

def _allowed_hosts():
    hosts = list(config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv()))
    # Allow Cloudflare Tunnel domains
    hosts.append('.trycloudflare.com')
    # Always allow the auto-detected LAN IP so phones on the same network can connect
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip not in hosts:
            hosts.append(ip)
    except Exception:
        pass
    return hosts

ALLOWED_HOSTS = _allowed_hosts()

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.gis',
    'rest_framework',
    'corsheaders',
    'django_ratelimit',
    'routing',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'bikeostrava.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'routing' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'bikeostrava.wsgi.application'

# Database - PostGIS
DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'NAME':     config('PGDATABASE', default='bikeostrava'),
        'USER':     config('PGUSER',     default='postgres'),
        'PASSWORD': config('PGPASSWORD', default='postgres'),
        'HOST':     config('PGHOST',     default='localhost'),
        'PORT':     config('PGPORT',     default='5432'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'cs'
TIME_ZONE = 'Europe/Prague'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS - allow all origins in dev; tighten in prod via env
CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='', cast=Csv())
CORS_ALLOW_ALL_ORIGINS = config('CORS_ALLOW_ALL_ORIGINS', default=True, cast=bool)

# DRF
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '20/hour',
    },
}

# External service URLs
GRAPHHOPPER_API_URL = config('GRAPHHOPPER_API_URL', default='https://graphhopper.com/api/1/route')
GRAPHHOPPER_API_KEY = config('GRAPHHOPPER_API_KEY', default='')

OSRM_API_URL = config('OSRM_API_URL', default='https://router.project-osrm.org/route/v1')

def _default_site_url():
    """Auto-detect LAN IP so QR codes work on phones during local dev."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            return f"http://{ip}:8000"
    except Exception:
        pass
    return "http://localhost:8000"

SITE_URL = config('SITE_URL', default=_default_site_url())

NOMINATIM_USER_AGENT = config('NOMINATIM_USER_AGENT', default='BikeOstrava/1.0 (contact@bikeostrava.cz)')

# Overpass
OVERPASS_API_URL = config('OVERPASS_API_URL', default='https://overpass-api.de/api/interpreter')

# Accident data is loaded from policie.gov.cz via: python manage.py load_accidents

# Rate-limiting cache - DB cache supports atomic incr, no extra service needed
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'django_cache',
    }
}

RATELIMIT_USE_CACHE = 'default'

# django-ratelimit 4.1 only "officially" supports Memcached/Redis.
# DB cache works fine for our load - silence the startup error.
SILENCED_SYSTEM_CHECKS = ['django_ratelimit.E003', 'django_ratelimit.W001']
