import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
WEBSITE_DIR = BASE_DIR / "website"
WEBSITE_TEMPLATES_DIR = WEBSITE_DIR / "templates"
WEBSITE_STATIC_DIR = WEBSITE_DIR / "static"
WEBSITE_DEMO_SITES_DIR = WEBSITE_DIR / "demo_sites"


def load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


env_file = os.getenv("DJANGO_ENV_FILE", ".env").strip()
if env_file:
    env_file_path = Path(env_file).expanduser()
    if not env_file_path.is_absolute():
        env_file_path = BASE_DIR / env_file_path
    load_env_file(env_file_path)


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_int(name, default=0):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def env_path(name, default):
    path = Path(os.getenv(name, str(default))).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def ensure_trailing_slash(value):
    return value if value.endswith("/") else f"{value}/"


def database_config_from_url(value):
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    query = dict(parse_qsl(parsed.query))

    if scheme == "sqlite":
        if parsed.path in {"", "/:memory:"}:
            name = ":memory:"
        else:
            name = unquote(parsed.path)
            if os.name == "nt" and name.startswith("/") and len(name) > 2 and name[2] == ":":
                name = name[1:]
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": name,
            "CONN_MAX_AGE": env_int("DJANGO_DB_CONN_MAX_AGE", 0),
        }

    if scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
            "CONN_MAX_AGE": env_int("DJANGO_DB_CONN_MAX_AGE", 60),
            "OPTIONS": query,
        }

    raise ImproperlyConfigured("DATABASE_URL supports sqlite:///path or postgres://user:pass@host:port/name")


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
AURUMWEB_TOTP_ENCRYPTION_KEY = os.getenv("AURUMWEB_TOTP_ENCRYPTION_KEY", "").strip()
AURUMWEB_TOTP_ENCRYPTION_OLD_KEYS = tuple(env_list("AURUMWEB_TOTP_ENCRYPTION_OLD_KEYS", ""))
AURUMWEB_ALLOW_LEGACY_SECRET_KEY_DECRYPTION = env_bool("AURUMWEB_ALLOW_LEGACY_SECRET_KEY_DECRYPTION", DEBUG)
for encryption_key in (AURUMWEB_TOTP_ENCRYPTION_KEY, *AURUMWEB_TOTP_ENCRYPTION_OLD_KEYS):
    if not encryption_key:
        continue
    try:
        Fernet(encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ImproperlyConfigured("AURUMWEB encryption keys must be valid Fernet keys.") from exc
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")
AURUMWEB_SITE_URL = os.getenv("AURUMWEB_SITE_URL", "").strip().rstrip("/")
PREPEND_WWW = env_bool("DJANGO_PREPEND_WWW", urlparse(AURUMWEB_SITE_URL).netloc.startswith("www."))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.projects.apps.ProjectsConfig",
    "apps.accounts",
    "apps.client_portal",
    "apps.content",
    "apps.office",
    "apps.leads",
    "apps.messaging",
    "apps.billing",
    "apps.integrations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.ContentSecurityPolicyReportOnlyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.AuditContextMiddleware",
    "apps.accounts.middleware.RequireStaffTotpMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "devforma.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [WEBSITE_DIR, WEBSITE_TEMPLATES_DIR],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.public_seller",
                "apps.client_portal.context_processors.client_portal",
                "apps.projects.context_processors.workspace_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "devforma.wsgi.application"

database_url = os.getenv("DATABASE_URL") or os.getenv("DJANGO_DATABASE_URL")
if database_url:
    DATABASES = {"default": database_config_from_url(database_url)}
elif not DEBUG:
    raise ImproperlyConfigured("Set DATABASE_URL to a PostgreSQL database before running with DJANGO_DEBUG=0.")
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": env_path("DJANGO_SQLITE_PATH", BASE_DIR / "db.sqlite3"),
            "CONN_MAX_AGE": env_int("DJANGO_DB_CONN_MAX_AGE", 0),
        }
    }

AURUMWEB_ALLOW_PRODUCTION_SQLITE = env_bool("AURUMWEB_ALLOW_PRODUCTION_SQLITE", False)
if (
    not DEBUG
    and DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    and not AURUMWEB_ALLOW_PRODUCTION_SQLITE
):
    raise ImproperlyConfigured(
        "SQLite is disabled for production. Use PostgreSQL or explicitly set "
        "AURUMWEB_ALLOW_PRODUCTION_SQLITE=1 for emergency recovery."
    )

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": os.getenv("DJANGO_CACHE_KEY_PREFIX", "aurumweb-local"),
    }
}

AURUMWEB_TRUST_X_FORWARDED_FOR = env_bool("AURUMWEB_TRUST_X_FORWARDED_FOR", not DEBUG)
AURUMWEB_LOGIN_MAX_ATTEMPTS = env_int("AURUMWEB_LOGIN_MAX_ATTEMPTS", 10)
AURUMWEB_LOGIN_LOCK_SECONDS = env_int("AURUMWEB_LOGIN_LOCK_SECONDS", 300)
AURUMWEB_REGISTER_RATE_LIMIT = env_int("AURUMWEB_REGISTER_RATE_LIMIT", 5)
AURUMWEB_REGISTER_RATE_WINDOW = env_int("AURUMWEB_REGISTER_RATE_WINDOW", 300)
AURUMWEB_PASSWORD_RESET_RATE_LIMIT = env_int("AURUMWEB_PASSWORD_RESET_RATE_LIMIT", 5)
AURUMWEB_PASSWORD_RESET_RATE_WINDOW = env_int("AURUMWEB_PASSWORD_RESET_RATE_WINDOW", 300)
AURUMWEB_PUBLIC_CONVERSATION_LINK_DAYS = env_int("AURUMWEB_PUBLIC_CONVERSATION_LINK_DAYS", 30)
AURUMWEB_PUBLIC_INVOICE_LINK_DAYS = env_int("AURUMWEB_PUBLIC_INVOICE_LINK_DAYS", 30)
AURUMWEB_PROJECT_UPLOAD_MAX_FILES = env_int("AURUMWEB_PROJECT_UPLOAD_MAX_FILES", 100)
AURUMWEB_PROJECT_UPLOAD_MAX_BYTES = env_int("AURUMWEB_PROJECT_UPLOAD_MAX_BYTES", 250 * 1024 * 1024)
AURUMWEB_REGISTRATION_EMAIL_ALLOWED_DOMAINS = tuple(
    domain.lower()
    for domain in env_list(
        "AURUMWEB_REGISTRATION_EMAIL_ALLOWED_DOMAINS",
        "mail.ru,inbox.ru,list.ru,bk.ru,internet.ru,xmail.ru,yandex.ru,ya.ru,"
        "rambler.ru,lenta.ru,autorambler.ru,myrambler.ru,ro.ru,vk.com,ok.ru",
    )
)
AURUMWEB_REGISTRATION_EMAIL_ALLOWED_SUFFIXES = tuple(
    suffix.lower() for suffix in env_list("AURUMWEB_REGISTRATION_EMAIL_ALLOWED_SUFFIXES", ".ru,.su,.рф,.рус,.moscow")
)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = ensure_trailing_slash(os.getenv("DJANGO_STATIC_URL", "/assets/"))
STATICFILES_DIRS = [WEBSITE_STATIC_DIR]
STATIC_ROOT = env_path("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles")
MEDIA_URL = ensure_trailing_slash(os.getenv("DJANGO_MEDIA_URL", "/media/"))
MEDIA_ROOT = env_path("DJANGO_MEDIA_ROOT", BASE_DIR / "media")
AURUMWEB_PRIVATE_MEDIA_ROOT = env_path("AURUMWEB_PRIVATE_MEDIA_ROOT", BASE_DIR / "private_media")
AURUMWEB_BACKUP_DIR = env_path("AURUMWEB_BACKUP_DIR", BASE_DIR / "backups")
AURUMWEB_BACKUP_S3_URI = os.getenv("AURUMWEB_BACKUP_S3_URI", "").strip().rstrip("/")
AURUMWEB_BACKUP_S3_ENDPOINT_URL = os.getenv("AURUMWEB_BACKUP_S3_ENDPOINT_URL", "").strip()
AURUMWEB_BACKUP_RCLONE_REMOTE = os.getenv("AURUMWEB_BACKUP_RCLONE_REMOTE", "").strip().rstrip(":")
AURUMWEB_BACKUP_RCLONE_PATH = os.getenv("AURUMWEB_BACKUP_RCLONE_PATH", "").strip().strip("/")
AURUMWEB_BACKUP_AGE_RECIPIENT = os.getenv("AURUMWEB_BACKUP_AGE_RECIPIENT", "").strip()
AURUMWEB_BACKUP_RPO_HOURS = env_int("AURUMWEB_BACKUP_RPO_HOURS", 24)
AURUMWEB_BACKUP_RTO_HOURS = env_int("AURUMWEB_BACKUP_RTO_HOURS", 4)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/client/"
LOGOUT_REDIRECT_URL = "/"

SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", not DEBUG)
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 31536000 if not DEBUG else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", not DEBUG)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = env_bool("DJANGO_SECURE_CONTENT_TYPE_NOSNIFF", True)
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = os.getenv("DJANGO_SECURE_CROSS_ORIGIN_OPENER_POLICY", "same-origin")
X_FRAME_OPTIONS = os.getenv("DJANGO_X_FRAME_OPTIONS", "DENY")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
YANDEX_METRIKA_HTTPS_SOURCES = (
    "https://mc.yandex.ru",
    "https://mc.yandex.com",
    "https://mc.webvisor.com",
    "https://mc.webvisor.org",
    "https://yastatic.net",
)
YANDEX_METRIKA_WSS_SOURCES = (
    "wss://mc.yandex.ru",
    "wss://mc.yandex.com",
    "wss://mc.webvisor.com",
    "wss://mc.webvisor.org",
)
CSP_DIRECTIVES = {
    "default-src": ("'self'",),
    "script-src": ("'self'", "'nonce-{nonce}'", *YANDEX_METRIKA_HTTPS_SOURCES),
    "script-src-attr": ("'none'",),
    "style-src": ("'self'", "'nonce-{nonce}'"),
    "style-src-attr": ("'none'",),
    "img-src": ("'self'", "data:", "blob:", *YANDEX_METRIKA_HTTPS_SOURCES),
    "font-src": ("'self'", "data:"),
    "connect-src": ("'self'", *YANDEX_METRIKA_HTTPS_SOURCES, *YANDEX_METRIKA_WSS_SOURCES),
    "child-src": ("'self'", "blob:", *YANDEX_METRIKA_HTTPS_SOURCES),
    "frame-src": ("'self'", "blob:", *YANDEX_METRIKA_HTTPS_SOURCES),
    "media-src": ("'self'",),
    "object-src": ("'none'",),
    "frame-ancestors": ("'none'",),
    "base-uri": ("'self'",),
    "form-action": ("'self'",),
}
CSP_ENFORCE_ENABLED = env_bool("DJANGO_CSP_ENFORCE_ENABLED", not DEBUG)
CSP_ENFORCE_DIRECTIVES = dict(CSP_DIRECTIVES)
CSP_REPORT_ONLY_ENABLED = env_bool("DJANGO_CSP_REPORT_ONLY_ENABLED", DEBUG)
CSP_REPORT_ONLY_DIRECTIVES = dict(CSP_DIRECTIVES)
csp_report_uri = os.getenv("DJANGO_CSP_REPORT_URI", "").strip()
if csp_report_uri:
    CSP_REPORT_ONLY_DIRECTIVES["report-uri"] = (csp_report_uri,)

if env_bool("DJANGO_USE_X_FORWARDED_PROTO", not DEBUG):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if not DEBUG:
    if SECRET_KEY == "dev-only-change-me":
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY before running with DJANGO_DEBUG=0.")
    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS before running with DJANGO_DEBUG=0.")
    if not AURUMWEB_TOTP_ENCRYPTION_KEY:
        raise ImproperlyConfigured("Set AURUMWEB_TOTP_ENCRYPTION_KEY before running with DJANGO_DEBUG=0.")
    if not CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS = [
            f"https://{host}"
            for host in ALLOWED_HOSTS
            if host not in {"*", "localhost", "127.0.0.1"} and not host.startswith(".")
        ]

ROBOKASSA = {
    "MERCHANT_LOGIN": os.getenv("ROBOKASSA_MERCHANT_LOGIN", ""),
    "PASSWORD1": os.getenv("ROBOKASSA_PASSWORD1", ""),
    "PASSWORD2": os.getenv("ROBOKASSA_PASSWORD2", ""),
    "TEST_MODE": env_bool("ROBOKASSA_TEST_MODE", True),
    "HASH_ALGORITHM": os.getenv("ROBOKASSA_HASH_ALGORITHM", "sha256"),
    "PAYMENT_URL": os.getenv("ROBOKASSA_PAYMENT_URL", "https://auth.robokassa.ru/Merchant/Index.aspx"),
    "RECEIPT_ENABLED": env_bool("ROBOKASSA_RECEIPT_ENABLED", True),
    "RECEIPT_SNO": os.getenv("ROBOKASSA_RECEIPT_SNO", ""),
    "RECEIPT_TAX": os.getenv("ROBOKASSA_RECEIPT_TAX", "none"),
    "RECEIPT_PAYMENT_METHOD": os.getenv("ROBOKASSA_RECEIPT_PAYMENT_METHOD", "full_payment"),
    "RECEIPT_PAYMENT_OBJECT": os.getenv("ROBOKASSA_RECEIPT_PAYMENT_OBJECT", "service"),
}
AURUMWEB_ROBOKASSA_RECONCILE_AFTER_MINUTES = env_int("AURUMWEB_ROBOKASSA_RECONCILE_AFTER_MINUTES", 30)
AURUMWEB_ROBOKASSA_RECONCILE_LIMIT = env_int("AURUMWEB_ROBOKASSA_RECONCILE_LIMIT", 100)
AURUMWEB_ROBOKASSA_STATE_TIMEOUT_SECONDS = env_int("AURUMWEB_ROBOKASSA_STATE_TIMEOUT_SECONDS", 10)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TELEGRAM_WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "")
TELEGRAM_WEBHOOK_ASYNC = env_bool("TELEGRAM_WEBHOOK_ASYNC", not DEBUG)
AURUMWEB_OUTBOX_ENABLED = env_bool("AURUMWEB_OUTBOX_ENABLED", not DEBUG)
AURUMWEB_OUTBOX_MAX_ATTEMPTS = env_int("AURUMWEB_OUTBOX_MAX_ATTEMPTS", 8)
AURUMWEB_OUTBOX_RETRY_BASE_SECONDS = env_int("AURUMWEB_OUTBOX_RETRY_BASE_SECONDS", 30)
AURUMWEB_OUTBOX_STALE_SECONDS = env_int("AURUMWEB_OUTBOX_STALE_SECONDS", 300)
AURUMWEB_OUTBOX_BATCH_SIZE = env_int("AURUMWEB_OUTBOX_BATCH_SIZE", 20)
AURUMWEB_OUTBOX_POLL_SECONDS = env_int("AURUMWEB_OUTBOX_POLL_SECONDS", 2)
AURUMWEB_INTEGRATION_EVENT_RETENTION_DAYS = env_int("AURUMWEB_INTEGRATION_EVENT_RETENTION_DAYS", 90)
AURUMWEB_OUTBOX_PAYLOAD_RETENTION_DAYS = env_int("AURUMWEB_OUTBOX_PAYLOAD_RETENTION_DAYS", 30)
AURUMWEB_MONITOR_WORKER_STALE_SECONDS = env_int("AURUMWEB_MONITOR_WORKER_STALE_SECONDS", 120)
AURUMWEB_MONITOR_DISK_PATH = env_path("AURUMWEB_MONITOR_DISK_PATH", BASE_DIR)
AURUMWEB_MONITOR_DISK_WARNING_PERCENT = env_int("AURUMWEB_MONITOR_DISK_WARNING_PERCENT", 85)
AURUMWEB_MONITOR_DISK_CRITICAL_PERCENT = env_int("AURUMWEB_MONITOR_DISK_CRITICAL_PERCENT", 95)
AURUMWEB_MONITOR_BACKUPS_ENABLED = env_bool("AURUMWEB_MONITOR_BACKUPS_ENABLED", not DEBUG)
AURUMWEB_MONITOR_OUTBOX_BACKLOG_MINUTES = env_int("AURUMWEB_MONITOR_OUTBOX_BACKLOG_MINUTES", 15)
AURUMWEB_MONITOR_ERROR_WINDOW_MINUTES = env_int("AURUMWEB_MONITOR_ERROR_WINDOW_MINUTES", 30)
AURUMWEB_MONITOR_SITE_WARNING_DAYS = env_int("AURUMWEB_MONITOR_SITE_WARNING_DAYS", 30)
AURUMWEB_MONITOR_ALERT_REPEAT_HOURS = env_int("AURUMWEB_MONITOR_ALERT_REPEAT_HOURS", 24)
QUICK_TELEGRAM_BOT_TOKEN = os.getenv("QUICK_TELEGRAM_BOT_TOKEN", "")
QUICK_TELEGRAM_BOT_USERNAME = os.getenv("QUICK_TELEGRAM_BOT_USERNAME", "")
QUICK_TELEGRAM_ADMIN_CHAT_ID = os.getenv("QUICK_TELEGRAM_ADMIN_CHAT_ID", "")
QUICK_TELEGRAM_WEBHOOK_SECRET = os.getenv("QUICK_TELEGRAM_WEBHOOK_SECRET", "")
QUICK_TELEGRAM_WEBHOOK_URL = os.getenv("QUICK_TELEGRAM_WEBHOOK_URL", "")
QUICK_TELEGRAM_CARD_VERSION = os.getenv("QUICK_TELEGRAM_CARD_VERSION", "20260603-awfast-cards-v2")

DEFAULT_FROM_EMAIL = os.getenv("DJANGO_DEFAULT_FROM_EMAIL", "AurumWeb <no-reply@localhost>")
SERVER_EMAIL = os.getenv("DJANGO_SERVER_EMAIL", DEFAULT_FROM_EMAIL)
EMAIL_BACKEND = os.getenv(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.getenv("DJANGO_EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("DJANGO_EMAIL_PORT", 25)
EMAIL_HOST_USER = os.getenv("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", False)
EMAIL_USE_SSL = env_bool("DJANGO_EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = env_int("DJANGO_EMAIL_TIMEOUT", 10)
AURUMWEB_GOOGLE_SITE_VERIFICATION = os.getenv("AURUMWEB_GOOGLE_SITE_VERIFICATION", "").strip()
AURUMWEB_YANDEX_VERIFICATION = os.getenv("AURUMWEB_YANDEX_VERIFICATION", "").strip()
AURUMWEB_ADMIN_EMAILS = env_list("AURUMWEB_ADMIN_EMAILS", "")
AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED = env_bool("AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED", bool(AURUMWEB_ADMIN_EMAILS))
AURUMWEB_ERROR_EMAIL_ALERTS_ENABLED = env_bool(
    "AURUMWEB_ERROR_EMAIL_ALERTS_ENABLED",
    bool(AURUMWEB_ADMIN_EMAILS) and not DEBUG,
)
AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED = env_bool("AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED", False)
AURUMWEB_EMAIL_SUBJECT_PREFIX = os.getenv("AURUMWEB_EMAIL_SUBJECT_PREFIX", "[AurumWeb] ")
if AURUMWEB_EMAIL_SUBJECT_PREFIX and not AURUMWEB_EMAIL_SUBJECT_PREFIX.endswith(" "):
    AURUMWEB_EMAIL_SUBJECT_PREFIX = f"{AURUMWEB_EMAIL_SUBJECT_PREFIX} "
AURUMWEB_EMAIL_CODE_TTL_MINUTES = env_int("AURUMWEB_EMAIL_CODE_TTL_MINUTES", 20)
AURUMWEB_PUBLIC_EMAIL = os.getenv("AURUMWEB_PUBLIC_EMAIL", EMAIL_HOST_USER or "aurumweb@aurumweb.ru")
AURUMWEB_PUBLIC_PHONE = os.getenv("AURUMWEB_PUBLIC_PHONE", "")
AURUMWEB_SELLER_NAME = os.getenv("AURUMWEB_SELLER_NAME", "")
AURUMWEB_SELLER_STATUS = os.getenv("AURUMWEB_SELLER_STATUS", "Самозанятый исполнитель")
AURUMWEB_SELLER_INN = os.getenv("AURUMWEB_SELLER_INN", "")
AURUMWEB_SELLER_ADDRESS = os.getenv("AURUMWEB_SELLER_ADDRESS", "")
AURUMWEB_YANDEX_METRIKA_ID = "".join(ch for ch in os.getenv("AURUMWEB_YANDEX_METRIKA_ID", "").strip() if ch.isdigit())
LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO")
REQUEST_LOG_LEVEL = os.getenv("DJANGO_REQUEST_LOG_LEVEL", "WARNING")
INTEGRATIONS_LOG_LEVEL = os.getenv("AURUMWEB_INTEGRATIONS_LOG_LEVEL", LOG_LEVEL)
TELEGRAM_LOG_LEVEL = os.getenv("AURUMWEB_TELEGRAM_LOG_LEVEL", INTEGRATIONS_LOG_LEVEL)
ROBOKASSA_LOG_LEVEL = os.getenv("AURUMWEB_ROBOKASSA_LOG_LEVEL", INTEGRATIONS_LOG_LEVEL)
SECURITY_LOG_LEVEL = os.getenv("AURUMWEB_SECURITY_LOG_LEVEL", LOG_LEVEL)
LOG_FILE = os.getenv("DJANGO_LOG_FILE", "").strip()
LOG_MAX_BYTES = env_int("DJANGO_LOG_MAX_BYTES", 5 * 1024 * 1024)
LOG_BACKUP_COUNT = env_int("DJANGO_LOG_BACKUP_COUNT", 5)

LOG_HANDLERS = {
    "console": {
        "class": "logging.StreamHandler",
        "formatter": "compact",
    },
}
ACTIVE_LOG_HANDLERS = ["console"]
if LOG_FILE:
    log_file_path = env_path("DJANGO_LOG_FILE", BASE_DIR / "logs" / "aurumweb.log")
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    LOG_HANDLERS["file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "formatter": "verbose",
        "filename": str(log_file_path),
        "maxBytes": LOG_MAX_BYTES,
        "backupCount": LOG_BACKUP_COUNT,
        "encoding": "utf-8",
    }
    ACTIVE_LOG_HANDLERS.append("file")

ERROR_LOG_HANDLERS = list(ACTIVE_LOG_HANDLERS)
if AURUMWEB_ERROR_EMAIL_ALERTS_ENABLED:
    LOG_HANDLERS["safe_error_email"] = {
        "class": "apps.core.logging_handlers.SafeErrorEmailHandler",
        "level": "ERROR",
    }
    ERROR_LOG_HANDLERS.append("safe_error_email")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "compact": {
            "format": "%(levelname)s %(asctime)s %(name)s %(message)s",
        },
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(name)s %(process)d %(thread)d %(message)s",
        },
    },
    "handlers": LOG_HANDLERS,
    "root": {
        "handlers": ACTIVE_LOG_HANDLERS,
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ACTIVE_LOG_HANDLERS,
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "django.request": {
            "handlers": ERROR_LOG_HANDLERS,
            "level": REQUEST_LOG_LEVEL,
            "propagate": False,
        },
        "apps": {
            "handlers": ACTIVE_LOG_HANDLERS,
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "apps.integrations": {
            "handlers": ACTIVE_LOG_HANDLERS,
            "level": INTEGRATIONS_LOG_LEVEL,
            "propagate": False,
        },
        "apps.integrations.telegram": {
            "handlers": ACTIVE_LOG_HANDLERS,
            "level": TELEGRAM_LOG_LEVEL,
            "propagate": False,
        },
        "apps.integrations.robokassa": {
            "handlers": ACTIVE_LOG_HANDLERS,
            "level": ROBOKASSA_LOG_LEVEL,
            "propagate": False,
        },
        "apps.accounts.security": {
            "handlers": ACTIVE_LOG_HANDLERS,
            "level": SECURITY_LOG_LEVEL,
            "propagate": False,
        },
    },
}
