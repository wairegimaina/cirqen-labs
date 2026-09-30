"""
Django settings for Equiper project.

Desktop Application Configuration using config.py
This version uses the CirqenConfig manager instead of .env files
Perfect for packaged desktop applications with embedded PostgreSQL
"""

import os
import sys
import logging
from pathlib import Path

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent

# ============================================================
# 📂 USER DATA PATH — must be defined first (writable location)
# Set CIRQEN_DATA_DIR env var to override (useful for multi-user or custom installs)
# ============================================================
if os.environ.get("CIRQEN_DATA_DIR"):
    DATA_PATH = Path(os.environ["CIRQEN_DATA_DIR"])
elif os.name == "nt":  # Windows
    app_data = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    DATA_PATH = app_data / "Cirqen" / "data"
else:  # macOS/Linux
    DATA_PATH = Path.home() / ".cirqen" / "data"

# Create all writable directories under DATA_PATH (not BASE_DIR)
DATA_PATH.mkdir(parents=True, exist_ok=True)
(DATA_PATH / "logs").mkdir(exist_ok=True)
(DATA_PATH / "media").mkdir(exist_ok=True)
(DATA_PATH / "offline_cache").mkdir(exist_ok=True)
(DATA_PATH / "staticfiles").mkdir(exist_ok=True)

MEDIA_URL = "/media/"
MEDIA_ROOT = DATA_PATH / "media"

# ============================================================
# 🔧 LOAD CONFIGURATION FROM config.py
# ============================================================
sync_path = BASE_DIR / "sync"
if sync_path not in sys.path:
    sys.path.insert(0, str(sync_path))

from config import CirqenConfig

# Load configuration (will use config.json for packaged app)
config = CirqenConfig(DATA_PATH, use_env_file=False)

# Setup all environment variables for compatibility
config.setup_environment_variables()

# ============================================================
# 🔒 CORE DJANGO SETTINGS
# ============================================================
# DEBUG: an explicit DJANGO_DEBUG env var always wins. Running Django with
# DEBUG=True leaks stack traces + settings and disables ALLOWED_HOSTS enforcement.
# A packaged (PyInstaller) build ignores config.json's app.debug: clients
# installed from older builds have "debug": true saved there, so the only way to
# turn DEBUG on in the field is to set DJANGO_DEBUG=1 deliberately.
_debug_env = os.getenv("DJANGO_DEBUG")
if _debug_env is not None:
    DEBUG = _debug_env.strip().lower() in ("1", "true", "yes", "on")
elif getattr(sys, "frozen", False):
    DEBUG = False
else:
    DEBUG = bool(config.get("app.debug"))
if DEBUG:
    import sys as _sys
    print("⚠️  SECURITY: Django DEBUG is ON — do not run production this way "
          "(set DJANGO_DEBUG=0).", file=_sys.stderr)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")

# Error monitoring: no-op unless SENTRY_DSN is set (core/monitoring.py).
from core.monitoring import init_sentry  # noqa: E402

init_sentry("django", client_name=config.get("client.name"), with_django=True)
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "Inventory",
    "core",
    "ppms",
    "parts_tools",
    "workshop",
    "users",
    "accounts",
    "jobcard",
    "Archives",
    "audit_log",
    "reporthub",
    "calSchedules",
    "scheduling",
    "CalSoft",
    "machineReports",
    "notifications",
    "django_celery_beat",
    "updates",
    "assets",
]

# Only load debug toolbar in debug mode
if DEBUG:
    INSTALLED_APPS += ["debug_toolbar"]

APP_VERSION = "1.6.0"

UPDATE_SYSTEM = {
    "enabled": True,
    "server_url": config.get("update.server_url"),
    "api_key": config.get("update.api_key"),
    # Ed25519 trust anchor for update packages AND the fleet endpoint document
    # (endpoint_sync.py). Empty = unsigned mode; see config.py.
    "public_key": config.get("update.public_key", ""),
    "check_interval_hours": config.get("update.check_interval_hours", 24),
    "auto_apply_updates": config.get("update.auto_apply", True),
    "components": {
        "templates": config.get("update.components.templates", True),
        "static": config.get("update.components.static", True),
        "django_apps": config.get("update.components.django_apps", True),
        "python_code": config.get("update.components.python_code", True),
        "migrations": config.get("update.components.migrations", True),
    },
}

AUTH_USER_MODEL = "accounts.CustomUser"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Closes modules the hospital's profile from Control switches off.
    "core.module_gate.ModuleGateMiddleware",
    # Read-only when the hospital's licence has lapsed (never locked out).
    "core.licence_gate.LicenceGateMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "users.middleware.ActiveUserMiddleware",
    "users.middleware.FirstLoginSetupMiddleware",
    "users.session_middleware.SessionExpiryMiddleware",
    "users.session_middleware.IdleTimeoutMiddleware",
    "core.hq_link.HQInstantPushMiddleware",
]

if DEBUG:
    MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]

# N+1 guard for development (core/query_budget.py): warn when a request runs
# more than this many queries. Off in packaged builds.
QUERY_BUDGET = int(os.getenv("CIRQEN_QUERY_BUDGET", "100" if DEBUG else "0")) or None
QUERY_BUDGET_STRICT = os.getenv("CIRQEN_QUERY_BUDGET_STRICT", "0") == "1"
MIDDLEWARE.insert(0, "core.query_budget.QueryBudgetMiddleware")

ROOT_URLCONF = "Equiper.urls"

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
                "Equiper.context_processors.nav_context",
                "Equiper.context_processors.module_tabs",
            ],
        },
    },
]

WSGI_APPLICATION = "Equiper.wsgi.application"

# ============================================================
# 🗄️ DATABASE CONFIGURATION
# ============================================================
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config.get("local_db.database"),
        "USER": config.get("local_db.user"),
        "PASSWORD": config.get("local_db.password"),
        "HOST": config.get("local_db.host"),
        "PORT": config.get("local_db.port"),
        "CONN_MAX_AGE": 300,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "connect_timeout": 10,
            "options": "-c statement_timeout=30000",
        },
    },
    # No "hq" alias in normal running: installed PCs reach HQ through its sync
    # API only and never hold its database password (commit 9abdf3e).
}

# Operator only: HQ_DATABASE_URL, set in the shell of the person migrating HQ
# (never in config.json, provisioning or a build), adds an "hq" database for
# `manage.py migrate_hq`. Example:
#   HQ_DATABASE_URL='postgresql://user:password@host:5432/postgres?sslmode=require'
if os.getenv("HQ_DATABASE_URL"):
    from urllib.parse import parse_qsl, unquote, urlparse

    _hq = urlparse(os.environ["HQ_DATABASE_URL"])
    DATABASES["hq"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(_hq.path.lstrip("/")) or "postgres",
        "USER": unquote(_hq.username or ""),
        "PASSWORD": unquote(_hq.password or ""),
        "HOST": _hq.hostname or "",
        "PORT": str(_hq.port or 5432),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            "connect_timeout": 20,
            "sslmode": "require",
            # Schema changes on a large table take longer than the app's 30 s.
            "options": "-c statement_timeout=900000",
            **dict(parse_qsl(_hq.query)),
        },
        # Never used by tests (they would build a test copy on HQ).
        "TEST": {"MIRROR": "default"},
    }

# ============================================================
# 🔄 OFFLINE-FIRST & SYNCHRONIZATION SETTINGS
# ============================================================
DATABASE_ROUTERS = ["Equiper.db_router.EquiperDatabaseRouter"]

SYNC_CONFIG = {
    "ENABLED": config.get("sync.enabled"),
    "INTERVAL": config.get("sync.download_interval"),
    "BATCH_SIZE": config.get("sync.upload_batch_size"),
    "RETRY_ATTEMPTS": config.get("sync.max_retries"),
    "RETRY_DELAY": config.get("sync.retry_backoff"),
    "USE_TRANSACTIONS": True,
    "ENABLE_COMPRESSION": True,
    "MAX_QUEUE_SIZE": 10000,
}

# Online-first write path (core/hq_link.py). Reads stay local; while HQ is
# reachable, the rows each request saves are pushed to HQ's sync API right away
# instead of waiting for the agent. Not named SYNC_API_URL: CalSoft reads that
# as a bare host and appends /api/sync/health itself.
HQ_SYNC_API_URL = config.get("sync.api_url")
SYNC_AUTH_TOKEN = config.get("sync.auth_token")
SYNC_TABLES = config.get("sync_tables", [])
HQ_INSTANT_PUSH = config.get("sync.instant_push", True)


def _certificate_verification_url():
    """Base of the public page a certificate's QR code opens (hq_server
    certificate_verify.py). CIRQEN_CERT_VERIFY_URL, then
    certificates.verification_url in config.json, then HQ's own address:
    https://<hq>/api/sync -> https://<hq>/verify. Empty when nothing is known,
    in which case certificates keep the data-only QR payload."""
    explicit = os.getenv("CIRQEN_CERT_VERIFY_URL") or config.get("certificates.verification_url")
    if explicit:
        return explicit.rstrip("/")
    sync_url = (HQ_SYNC_API_URL or "").rstrip("/")
    if sync_url.startswith("https://") and sync_url.endswith("/api/sync"):
        return sync_url[: -len("/api/sync")] + "/verify"
    return ""


CERTIFICATE_VERIFICATION_URL = _certificate_verification_url()

# Certificate numbers are allocated by HQ with its CERT_PREFIX; this must
# match it (the conflict guard takes HQ's value from each reply).
CERTIFICATE_PREFIX = os.getenv("CIRQEN_CERT_PREFIX") or config.get("certificates.prefix") or "BNH-"

# Second backup location (core.backups.copy_dir); CIRQEN_BACKUP_COPY_DIR wins.
BACKUP_COPY_DIR = config.get("backups.copy_dir") or ""
BACKUP_KEEP = int(os.getenv("CIRQEN_BACKUP_KEEP") or config.get("backups.keep") or 14)
# Sign-in security log retention in days (docs/legal/DATA_PROTECTION.md);
# 0 keeps it forever. HQ prunes its copy on its own schedule.
SECURITY_LOG_DAYS = int(os.getenv("CIRQEN_SECURITY_LOG_DAYS") or config.get("security.log_days") or 365)

# ============================================================
# 🔴 REDIS & CACHING
# ============================================================
redis_host = config.get("redis.host")
# PortManager dynamically reallocates this port whenever the configured
# default is still busy (e.g. a just-closed previous session's redis-server
# still releasing it) and publishes the real port via REDIS_PORT — prefer
# that over the static config.json value, or Django's cache/session backend
# ends up pointed at a port nothing is actually listening on.
redis_port = os.getenv("REDIS_PORT") or config.get("redis.port")
redis_password = config.get("redis.password")

if redis_password:
    redis_base = f"redis://:{redis_password}@{redis_host}:{redis_port}"
else:
    redis_base = f"redis://{redis_host}:{redis_port}"

# Redis is an accelerator, never a dependency. On a single offline machine the
# bundled redis-server can start late or die; with IGNORE_EXCEPTIONS and short
# socket timeouts every cache call degrades to a miss instead of raising or
# hanging, and sessions (cached_db) fall back to the database.
_REDIS_FAIL_SOFT = {
    "IGNORE_EXCEPTIONS": True,
    "SOCKET_CONNECT_TIMEOUT": 0.3,
    "SOCKET_TIMEOUT": 0.5,
}
DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True
DJANGO_REDIS_LOGGER = "cirqen.cache"

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": f"{redis_base}/0",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 50,
            },
            "COMPRESSOR": "django_redis.compressors.zlib.ZlibCompressor",
            "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
            **_REDIS_FAIL_SOFT,
        },
        "TIMEOUT": 300,
        "KEY_PREFIX": "cirqen",
    },
    "sessions": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": f"{redis_base}/1",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 50,
            },
            **_REDIS_FAIL_SOFT,
        },
    },
    # Login / reset-code attempt counters (users.throttle). File-based on
    # purpose: brute-force protection must not switch off when Redis is down.
    "throttle": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": DATA_PATH / "throttle_cache",
        "TIMEOUT": 3600,
    },
    "offline": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": DATA_PATH / "offline_cache",
        "TIMEOUT": 3600 * 24,
        "OPTIONS": {
            "MAX_ENTRIES": 10000,
        },
    },
}

# ============================================================
# 📊 LOGGING CONFIGURATION — production: files only, no console
# ============================================================
LOGS_DIR = DATA_PATH / "logs"
LOGS_DIR.mkdir(exist_ok=True)

for log_file in ["auth.log", "sync.log", "errors.log", "django.log"]:
    log_path = LOGS_DIR / log_file
    if not log_path.exists():
        log_path.touch()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} [{name}] {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {asctime} {message}",
            "style": "{",
        },
        "json": {
            "format": '{{"level": "{levelname}", "time": "{asctime}", "name": "{name}", "module": "{module}", "message": "{message}"}}',
            "style": "{",
        },
    },
    "handlers": {
        "auth_file": {
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOGS_DIR / "auth.log"),
            "formatter": "verbose",
            "maxBytes": 10485760,
            "backupCount": 5,
            "encoding": "utf-8",
        },
        "sync_file": {
            "level": "INFO",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOGS_DIR / "sync.log"),
            "formatter": "json",
            "maxBytes": 10485760,
            "backupCount": 10,
            "encoding": "utf-8",
        },
        "error_file": {
            "level": "ERROR",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOGS_DIR / "errors.log"),
            "formatter": "verbose",
            "maxBytes": 10485760,
            "backupCount": 10,
            "encoding": "utf-8",
        },
        "django_file": {
            "level": "INFO",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOGS_DIR / "django.log"),
            "formatter": "verbose",
            "maxBytes": 10485760,
            "backupCount": 5,
            "encoding": "utf-8",
        },
    },
    "loggers": {
        # ---- users app: routes to auth_file so all email/login activity is captured ----
        "users": {
            "handlers": ["auth_file", "error_file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "users.backends": {
            "handlers": ["auth_file"],
            "level": "DEBUG",
            "propagate": False,
        },
        # ---- Django's own email machinery — surfaces SMTP-level errors ----
        "django.core.mail": {
            "handlers": ["auth_file", "error_file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "sync": {
            "handlers": ["sync_file"],
            "level": "INFO",
            "propagate": False,
        },
        "django": {
            "handlers": ["django_file"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["error_file"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["django_file"],
            "level": "INFO",
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": [],
            "level": "WARNING",
            "propagate": False,
        },
        "django.contrib.staticfiles": {
            "handlers": [],
            "level": "WARNING",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["error_file"],
        "level": "WARNING",
    },
}

STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

X_FRAME_OPTIONS = "SAMEORIGIN"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 6},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
# The UI is English-only: no template or view marks strings for translation.
# Turn this back on together with {% translate %} tags and LocaleMiddleware
# if a second language is ever needed (IMPROVEMENT_PLAN.md section 8).
USE_I18N = False
USE_TZ = True
# Celery defaults to UTC, so crontab(hour=8) ran at 11:00 in Nairobi.
CELERY_TIMEZONE = TIME_ZONE

# Static files — source dirs stay in BASE_DIR (read-only OK), output goes to DATA_PATH
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = DATA_PATH / "staticfiles"
# Content-hash query strings on {% static %} URLs so an update never leaves a
# browser on stale CSS/JS (see core/static_storage.py for why not Manifest).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "core.static_storage.VersionedStaticFilesStorage"},
}

SITE_NAME = config.get("client.name", "Cirqen Desktop")
REPORT_CONTACT = {
    "email": config.get("client.email", ""),
    "phone": config.get("client.phone", ""),
}
# Address people use to reach this site, for links in emails and QR labels.
SITE_URL = (os.getenv("CIRQEN_SITE_URL") or "http://127.0.0.1:8000").rstrip("/")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ============================================================
# 🔐 AUTHENTICATION & SESSIONS
# ============================================================
AUTHENTICATION_BACKENDS = [
    # Username or email; see the class for how shared emails are handled.
    "users.backends.EmailOrUsernameBackend",
    # Kept so sessions signed in before this change stay valid.
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/login/"

SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_CACHE_ALIAS = "sessions"
SESSION_COOKIE_NAME = "sessionid"
# Signed out after this long without a person's activity (IdleTimeoutMiddleware).
SESSION_IDLE_SECONDS = int(os.getenv("CIRQEN_IDLE_MINUTES", "30")) * 60
SESSION_COOKIE_AGE = max(3600, SESSION_IDLE_SECONDS)
# Heads of department must sign in with an authenticator app as well.
REQUIRE_HOD_TWO_FACTOR = os.getenv("CIRQEN_REQUIRE_HOD_2FA", "0").lower() in ("1", "true", "yes")
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = True
# Transport security. The desktop build serves plain HTTP on 127.0.0.1, so these
# stay off by default; a deployment reachable over HTTPS sets CIRQEN_HTTPS=1 and
# gets secure cookies, an HTTPS redirect and HSTS without a code change.
# CIRQEN_BEHIND_PROXY=1 additionally trusts X-Forwarded-Proto from a TLS proxy.
CIRQEN_HTTPS = os.getenv("CIRQEN_HTTPS", "0").strip().lower() in ("1", "true", "yes", "on")
SESSION_COOKIE_SECURE = CIRQEN_HTTPS
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

CSRF_COOKIE_SECURE = CIRQEN_HTTPS
SECURE_SSL_REDIRECT = CIRQEN_HTTPS
SECURE_HSTS_SECONDS = int(os.getenv("CIRQEN_HSTS_SECONDS", "31536000")) if CIRQEN_HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = CIRQEN_HTTPS
if os.getenv("CIRQEN_BEHIND_PROXY", "0").strip().lower() in ("1", "true", "yes", "on"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]
# Server mode (deploy/server): browsers post from https://<server name>, which
# Django refuses unless the origin is trusted. Every non-local ALLOWED_HOSTS name
# is trusted over HTTPS when CIRQEN_HTTPS is on; CIRQEN_CSRF_TRUSTED_ORIGINS
# adds explicit origins (comma-separated, with scheme).
if CIRQEN_HTTPS:
    CSRF_TRUSTED_ORIGINS += [
        f"https://{host.lstrip('.')}" for host in ALLOWED_HOSTS
        if host and host not in ("localhost", "127.0.0.1", "*") and not host.startswith("*")
    ]
CSRF_TRUSTED_ORIGINS += [o.strip() for o in os.getenv("CIRQEN_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

PASSWORD_RESET_TIMEOUT = 1800
PASSWORD_RESET_CODE_LENGTH = 6

# ============================================================
# 📧 EMAIL CONFIGURATION
# ============================================================
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
# Server mode sets these in cirqen.env; desktops use config.json.
EMAIL_HOST = os.getenv("EMAIL_HOST") or config.get("email.host", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT") or config.get("email.port", 587))
EMAIL_USE_TLS = (os.getenv("EMAIL_USE_TLS") or str(config.get("email.use_tls", True))).lower() in ("1", "true", "yes")
EMAIL_USE_SSL = False
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER") or config.get("email.host_user", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD") or config.get("email.host_password", "")
# Guard: if host_user is blank, Django will try to send from "" and SMTP will reject it.
# Fix the email.host_user value in config.json / CirqenConfig to resolve this.
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER or "no-reply@example.com"
SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_TIMEOUT = 30
EMAIL_MAX_RETRIES = 3

# Notifications (notifications app). Event mail (work order submitted /
# decided) is sent by the desktop where the event happened. The daily digest
# must be sent by ONE machine per site: set notifications.digest_sender to
# true on that machine only, or every user gets one copy per desktop.
NOTIFICATIONS_EMAIL_ENABLED = bool(config.get("notifications.email_enabled", True))
NOTIFICATIONS_DIGEST_SENDER = bool(config.get("notifications.digest_sender", False))
NOTIFICATIONS_DIGEST_HOUR = int(config.get("notifications.digest_hour", 7))  # EAT
NOTIFICATIONS_APP_NAME = "Cirqen"
# One email per work order is noise at a busy hospital: off unless asked for.
# The bell notification is always made; the HOD gets the weekly report.
NOTIFICATIONS_WORK_ORDER_EMAIL = bool(config.get("notifications.work_order_email", False))
# Unsent email older than this is marked expired instead of delivered late.
NOTIFICATIONS_OUTBOX_KEEP_DAYS = int(config.get("notifications.outbox_keep_days", 7))

# Warranties within this many days of expiry show as "Expiring Soon".
WARRANTY_EXPIRING_SOON_DAYS = int(config.get("warranty.expiring_soon_days", 60))

# ============================================================
# 🔄 CELERY & BACKGROUND TASKS
# ============================================================
logger = logging.getLogger(__name__)

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND")

if not CELERY_BROKER_URL:
    CELERY_BROKER_URL = f"redis://{redis_host}:{redis_port}/2"
    os.environ["CELERY_BROKER_URL"] = CELERY_BROKER_URL

if not CELERY_RESULT_BACKEND:
    CELERY_RESULT_BACKEND = f"redis://{redis_host}:{redis_port}/2"
    os.environ["CELERY_RESULT_BACKEND"] = CELERY_RESULT_BACKEND

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_RESULT_EXPIRES = 3600

CELERY_TASK_ROUTES = {
    "core.tasks.run_daily_cleanup": {"queue": "cleanup"},
    "core.tasks.run_annual_cleanup": {"queue": "cleanup"},
    "core.tasks.handle_sync_failures": {"queue": "sync"},
    "core.tasks.batch_sync_changes": {"queue": "sync"},
}

CELERY_TASK_DEFAULT_QUEUE = "default"

# ============================================================
# 🌐 WEBSOCKETS & CHANNELS
# ============================================================
ASGI_APPLICATION = "Equiper.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [os.getenv("CHANNELS_REDIS_URL")],
            "capacity": 1500,
            "expiry": 10,
        },
    },
}

# ============================================================
# 📊 MONITORING & HEALTH CHECKS
# ============================================================
HEALTH_CHECK_CONFIG = {
    "ENABLED": True,
    "TIMEOUT": 30,
    "CACHE_TIMEOUT": 60,
    "CHECKS": [
        "core.health.PostgreSQLHealthCheck",
        "core.health.RedisHealthCheck",
        "core.health.SyncServiceHealthCheck",
    ],
}

PROMETHEUS_METRICS_ENABLED = False

# ============================================================
# 📁 FILE UPLOAD & MEDIA SETTINGS
# ============================================================
FILE_UPLOAD_MAX_MEMORY_SIZE = 26214400  # 25MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 26214400  # 25MB
FILE_UPLOAD_PERMISSIONS = 0o644
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755


# ============================================================
# 🛠️ DATA MANAGEMENT & CLEANUP
# ============================================================
DATA_RETENTION_CONFIG = {
    "ENABLED": True,
    "EXCLUDED_MODELS": [
        "standards.Standard",
        "inventory.Tool",
        "accounts.CustomUser",
        "django.contrib.auth.models.User",
        "django.contrib.sessions.models.Session",
    ],
    "DEFAULTS": {
        "BATCH_SIZE": 2000,
        "MAX_RECORDS_DAILY": 100000,
        "DRY_RUN": False,
    },
    "RETENTION_PERIODS": {
        "logs": 90,
        "sessions": 30,
        "temporary_files": 7,
        "audit_logs": 365,
    },
}

# ============================================================
# 🎯 FEATURE FLAGS
# ============================================================
FEATURE_FLAGS = {
    "REAL_TIME_SYNC": config.get("sync.enabled"),
    "ADVANCED_ANALYTICS": True,
    "MACHINE_LEARNING": False,
    "API_RATE_LIMITING": True,
    "AUDIT_LOGGING": True,
    "DATA_ENCRYPTION": False,
    "ELASTICSEARCH_ENABLED": False,
}

# ============================================================
# 🚀 PERFORMANCE & OPTIMIZATION
# ============================================================
DATABASE_POOL_SIZE = config.get("system.database_pool_size")
DATABASE_MAX_OVERFLOW = 30
DATABASE_POOL_RECYCLE = 3600

TEMPLATE_LOADERS_CACHE = True
TEMPLATE_DEBUG = DEBUG

# Make config instance available to other parts of Django
CIRQEN_CONFIG = config
