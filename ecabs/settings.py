"""
Django settings for the Vai Drive internal insurance claim system.

Internal staff-only tool. SQLite for now; the DATABASES block is isolated
so switching to PostgreSQL later is a one-block change (plus psycopg install).
"""

from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# Load a local .env file if one exists (handy on hosts like PythonAnywhere where
# setting real environment variables is awkward). Values already set in the real
# environment win, so cloud dashboards keep working. No-op if python-dotenv or
# the file is absent — nothing here is required for local development.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

# SECURITY: read from environment in production; the fallback is for local dev only.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-change-me-in-production",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

# Refuse to boot in production with the throwaway development key. (W009)
if not DEBUG and SECRET_KEY.startswith("django-insecure-"):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "Set a strong DJANGO_SECRET_KEY environment variable before running with "
        "DJANGO_DEBUG=0. Generate one with:\n"
        "  python -c \"from django.core.management.utils import get_random_secret_key as k; print(k())\""
    )

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "simple_history",
    "claims",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves compressed, far-future-cached static files in production without a
    # separate web server. Must sit directly after SecurityMiddleware.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "ecabs.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "claims.context_processors.due_reminders",
            ],
        },
    },
]

WSGI_APPLICATION = "ecabs.wsgi.application"

# --- Database ---------------------------------------------------------------
# SQLite by default. Setting DB_NAME switches to PostgreSQL with no code change
# — just provide DB_NAME/DB_USER/DB_PASSWORD (and install psycopg). SQLite is
# fine for two staff; move to PostgreSQL when you want concurrent access or
# managed backups.
if os.environ.get("DB_NAME"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["DB_NAME"],
            "USER": os.environ.get("DB_USER", ""),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "CONN_MAX_AGE": 600,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/Malta"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# In production WhiteNoise compresses static files and serves them with hashed,
# far-future cache headers straight from the app process — no separate web
# server needed. That storage needs a `collectstatic` manifest, so dev, tests
# and the CI preview build (which don't run collectstatic) use plain storage.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if not DEBUG
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}

# Local fallback storage for uploads while Google Drive credentials are not
# configured. Only the Drive file ID + metadata live in the database either way.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# --- Production security -------------------------------------------------------
# These only switch on when DEBUG is off, so local development and the CI
# preview build (which run with DEBUG=1) are unaffected. They clear the
# `manage.py check --deploy` warnings once the app is served over HTTPS.
if not DEBUG:
    # Redirect any http:// request to https:// (W008). Disable only if a proxy
    # already forces HTTPS: DJANGO_SECURE_SSL_REDIRECT=0.
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"
    # Hosts terminate TLS at a proxy and forward this header (PythonAnywhere,
    # Railway, Render, etc.) — trust it so Django knows the request was secure.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # Cookies only ever travel over HTTPS (W012, W016).
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # HTTP Strict Transport Security (W004): tell browsers to use HTTPS only.
    # Start modest and raise once you're confident everything is on HTTPS.
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "3600"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # Preload is a hard-to-reverse commitment (submits the domain to browsers'
    # built-in HTTPS-only list) — inappropriate for a small internal tool, so
    # we deliberately opt out and silence the advisory check.
    SECURE_HSTS_PRELOAD = False
    SILENCED_SYSTEM_CHECKS = ["security.W021"]
    # Extra hardening.
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_HTTPONLY = True
    # Trust the host's forwarded origin for CSRF (comma-separated https URLs).
    _csrf = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "")
    if _csrf:
        CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf.split(",") if o.strip()]

# --- Email --------------------------------------------------------------------
# Development prints emails to the console; production sends over SMTP once the
# host credentials below are supplied via environment variables. Set
# DJANGO_EMAIL_BACKEND explicitly to override (e.g. to force real sending
# while DEBUG is on for a test).
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "0") == "1"
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "motorclaims@ecabs.com.mt")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# --- Google Drive integration -------------------------------------------------
# Path to a service-account JSON key. When unset, uploads fall back to
# MEDIA_ROOT so the app works in development without Drive access.
GOOGLE_DRIVE_CREDENTIALS_FILE = os.environ.get("GOOGLE_DRIVE_CREDENTIALS_FILE", "")
# Parent folder in Drive under which one folder per claim is created.
GOOGLE_DRIVE_ROOT_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
