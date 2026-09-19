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
    "CalSoft",
    "chartjs",
    "machineReports",
    "django_celery_beat",
    "updates",
]

# Only load debug toolbar in debug mode
if DEBUG:
    INSTALLED_APPS += ["debug_toolbar"]

APP_VERSION = "1.5.0"

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
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "users.middleware.ActiveUserMiddleware",
    "users.session_middleware.SessionExpiryMiddleware",
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
    "hq": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config.get("hq_db.database"),
        "USER": config.get("hq_db.user"),
        "PASSWORD": config.get("hq_db.password"),
        "HOST": config.get("hq_db.host"),
        "PORT": config.get("hq_db.port"),
        "CONN_MAX_AGE": 300,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "connect_timeout": 10,
            "options": "-c statement_timeout=30000",
            "sslmode": "require",
        },
    },
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
    "CDC_ENABLED": True,
    "CDC_SLOT_NAME": "equiper_cdc_slot",
    "CDC_PUBLICATION": "equiper_publication",
}

# Online-first write path (core/hq_link.py). Reads stay local; while HQ is
# reachable, the rows each request saves are pushed to HQ's sync API right away
# instead of waiting for the agent. Not named SYNC_API_URL: CalSoft reads that
# as a bare host and appends /api/sync/health itself.
HQ_SYNC_API_URL = config.get("sync.api_url")
SYNC_AUTH_TOKEN = config.get("sync.auth_token")
SYNC_TABLES = config.get("sync_tables", [])
HQ_INSTANT_PUSH = config.get("sync.instant_push", True)

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

for log_file in ["auth.log", "sync.log", "errors.log", "redpanda.log", "django.log"]:
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
        "redpanda_file": {
            "level": "INFO",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOGS_DIR / "redpanda.log"),
            "formatter": "json",
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
        "redpanda": {
            "handlers": ["redpanda_file"],
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
SITE_URL = "http://127.0.0.1:8000"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ============================================================
# 🔐 AUTHENTICATION & SESSIONS
# ============================================================
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/login/"

SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_CACHE_ALIAS = "sessions"
SESSION_COOKIE_NAME = "sessionid"
SESSION_COOKIE_AGE = 3600
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

PASSWORD_RESET_TIMEOUT = 1800
PASSWORD_RESET_CODE_LENGTH = 6

# ============================================================
# 📧 EMAIL CONFIGURATION
# ============================================================
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_USE_SSL = False
EMAIL_HOST_USER = config.get("email.host_user", "")
EMAIL_HOST_PASSWORD = config.get("email.host_password", "")
# Guard: if host_user is blank, Django will try to send from "" and SMTP will reject it.
# Fix the email.host_user value in config.json / CirqenConfig to resolve this.
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER or "no-reply@example.com"
SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_TIMEOUT = 30
EMAIL_MAX_RETRIES = 3

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
    "core.tasks.monitor_redpanda_sync": {"queue": "sync"},
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
# 📡 REDPANDA & EVENT STREAMING
# ============================================================
REDPANDA_CONFIG = {
    "BOOTSTRAP_SERVERS": config.get("redpanda.bootstrap_servers"),
    "CLIENT_ID": "cirqen_desktop_client",
    "GROUP_ID": config.get("redpanda.consumer_group"),
    "AUTO_OFFSET_RESET": "latest",
    "ENABLE_AUTO_COMMIT": True,
    "AUTO_COMMIT_INTERVAL_MS": 5000,
    "SESSION_TIMEOUT_MS": 30000,
    "HEARTBEAT_INTERVAL_MS": 10000,
    "MAX_POLL_RECORDS": 500,
    "MAX_POLL_INTERVAL_MS": 300000,
    "FETCH_MIN_BYTES": 1,
    "FETCH_MAX_WAIT_MS": 500,
    "REQUEST_TIMEOUT_MS": 30000,
    "API_VERSION": (2, 5, 0),
    "PRODUCER_ACKS": "all",
    "PRODUCER_RETRIES": 3,
    "PRODUCER_BATCH_SIZE": 16384,
    "PRODUCER_LINGER_MS": 10,
    "PRODUCER_COMPRESSION_TYPE": "lz4",
}

DEBEZIUM_CONFIG = {
    "CONNECTOR_CLASS": "io.debezium.connector.postgresql.PostgresConnector",
    "LOCAL_DB": {
        "HOSTNAME": config.get("local_db.host"),
        "PORT": str(config.get("local_db.port")),
        "USER": config.get("local_db.user"),
        "PASSWORD": config.get("local_db.password"),
        "DBNAME": config.get("local_db.database"),
        "SERVER_NAME": "postgres_local",
    },
    "HQ_DB": {
        "HOSTNAME": config.get("hq_db.host"),
        "PORT": str(config.get("hq_db.port")),
        "USER": config.get("hq_db.user"),
        "PASSWORD": config.get("hq_db.password"),
        "DBNAME": config.get("hq_db.database"),
        "SERVER_NAME": "postgres_hq",
    },
    "SLOT_NAME_LOCAL": "debezium_local_slot",
    "SLOT_NAME_HQ": "debezium_hq_slot",
    "PUBLICATION_NAME_LOCAL": "debezium_local_publication",
    "PUBLICATION_NAME_HQ": "debezium_hq_publication",
    "INCLUDE_SCHEMA_CHANGES": False,
    "SCHEMA_INCLUDE_LIST": "public",
    "TABLE_INCLUDE_LIST": ",".join(config.get("debezium.table_include_list")),
    "PLUGIN_NAME": "pgoutput",
    "SNAPSHOT_MODE": "initial",
    "TOPIC_PREFIX": "postgres",
}

SYNC_TOPICS = {
    "LOCAL_TO_HQ": [
        "postgres_local_workshop_workshop",
        "postgres_local_accounts_customuser",
        "postgres_local_Inventory_equipment",
        "postgres_local_CalSoft_calibrationsession",
        "postgres_local_CalSoft_calibrationprocedure",
        "postgres_local_jobcard_jobcard",
    ],
    "HQ_TO_LOCAL": [
        "postgres_hq_workshop_workshop",
        "postgres_hq_accounts_customuser",
        "postgres_hq_Inventory_equipment",
        "postgres_hq_CalSoft_calibrationprocedure",
        "postgres_hq_parts_tools_tools",
    ],
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
        "core.health.RedpandaHealthCheck",
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
    "CDC_ENABLED": True,
    "ELASTICSEARCH_ENABLED": False,
    "REDPANDA_ENABLED": config.get("redpanda.enabled"),
}

# ============================================================
# 🚀 PERFORMANCE & OPTIMIZATION
# ============================================================
DATABASE_POOL_SIZE = config.get("system.database_pool_size")
DATABASE_MAX_OVERFLOW = 30
DATABASE_POOL_RECYCLE = 3600

TEMPLATE_LOADERS_CACHE = True
TEMPLATE_DEBUG = DEBUG

# ============================================================
# 🔧 REDPANDA SYNC SERVICE CONFIGURATION
# ============================================================
REDPANDA_SYNC_CONFIG = {
    "ENABLED": config.get("redpanda.enabled"),
    "WORKER_THREADS": config.get("redpanda.worker_count"),
    "MAX_RETRIES": config.get("redpanda.sync_max_retries"),
    "RETRY_INTERVAL": 30,
    "STATS_INTERVAL": config.get("system.stats_print_interval"),
    "DEAD_LETTER_TOPIC": config.get("redpanda.dead_letter_topic"),
    "ENABLE_DEAD_LETTER_QUEUE": config.get("redpanda.enable_dlq"),
}

# Make config instance available to other parts of Django
CIRQEN_CONFIG = config
