"""Settings used by the test suite (pytest) — deterministic, no .env required."""

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR, DATABASES

if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    # File-based test DB: in-memory SQLite uses table-level locks across
    # threads, which breaks the concurrency tests.
    DATABASES["default"]["TEST"] = {"NAME": BASE_DIR / ".test_db.sqlite3"}

DEBUG = False
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Fast, dependency-free defaults for tests.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
