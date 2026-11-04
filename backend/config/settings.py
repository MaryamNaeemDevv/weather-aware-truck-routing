"""Django settings for the weather-aware truck routing API.

All configuration comes from environment variables (see .env.example).
The app is stateless: no database models; plans live in the cache.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "corsheaders",
    "rest_framework",
    "apps.planner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# No models: SQLite only satisfies Django internals (contenttypes).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

REDIS_URL = os.getenv("REDIS_URL", "")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {"IGNORE_EXCEPTIONS": True},
            "TIMEOUT": 1800,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "TIMEOUT": 1800,
            "OPTIONS": {"MAX_ENTRIES": 20000},
        }
    }

TEMPLATES = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "plan": os.getenv("THROTTLE_PLAN", "20/min"),
        "geocode": os.getenv("THROTTLE_GEOCODE", "120/min"),
        "heatmap": os.getenv("THROTTLE_HEATMAP", "40/min"),
    },
    "EXCEPTION_HANDLER": "apps.planner.api.errors.exception_handler",
}

# --- Domain configuration -------------------------------------------------
PLANNER = {
    # "here" uses HERE + Open-Meteo; "demo" uses deterministic synthetic data.
    "PROVIDER": os.getenv("PLANNER_PROVIDER", "here" if os.getenv("HERE_API_KEY") else "demo"),
    "HERE_API_KEY": os.getenv("HERE_API_KEY", ""),
    "OPEN_METEO_BASE_URL": os.getenv("OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast"),
    "OPEN_METEO_API_KEY": os.getenv("OPEN_METEO_API_KEY", ""),  # optional commercial key
    "TRACTOR_TRAILER_TARE_LB": float(os.getenv("TRACTOR_TRAILER_TARE_LB", "35000")),
    "HTTP_TIMEOUT_S": float(os.getenv("HTTP_TIMEOUT_S", "15")),
    "PLAN_TTL_S": int(os.getenv("PLAN_TTL_S", "3600")),
    "WEATHER_TTL_S": int(os.getenv("WEATHER_TTL_S", "1800")),
    "HEATMAP_MAX_CELLS": int(os.getenv("HEATMAP_MAX_CELLS", "900")),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_CONTENT_TYPE_NOSNIFF = True
