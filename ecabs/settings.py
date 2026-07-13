"""
Django settings for the eCabs internal insurance claim system.

Internal staff-only tool. SQLite for now; the DATABASES block is isolated
so switching to PostgreSQL later is a one-block change (plus psycopg install).
"""

from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY: read from environment in production; the fallback is for local dev only.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-change-me-in-production",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

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
            ],
        },
    },
]

WSGI_APPLICATION = "ecabs.wsgi.application"

# --- Database ---------------------------------------------------------------
# SQLite for development. To move to PostgreSQL later, replace this block:
#
#     DATABASES = {
#         "default": {
#             "ENGINE": "django.db.backends.postgresql",
#             "NAME": os.environ["DB_NAME"],
#             "USER": os.environ["DB_USER"],
#             "PASSWORD": os.environ["DB_PASSWORD"],
#             "HOST": os.environ.get("DB_HOST", "localhost"),
#             "PORT": os.environ.get("DB_PORT", "5432"),
#         }
#     }
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

# Local fallback storage for uploads while Google Drive credentials are not
# configured. Only the Drive file ID + metadata live in the database either way.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# --- Google Drive integration -------------------------------------------------
# Path to a service-account JSON key. When unset, uploads fall back to
# MEDIA_ROOT so the app works in development without Drive access.
GOOGLE_DRIVE_CREDENTIALS_FILE = os.environ.get("GOOGLE_DRIVE_CREDENTIALS_FILE", "")
# Parent folder in Drive under which one folder per claim is created.
GOOGLE_DRIVE_ROOT_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
