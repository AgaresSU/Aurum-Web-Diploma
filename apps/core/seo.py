from urllib.parse import urlsplit

from django.conf import settings


def public_site_root(request=None):
    configured = (getattr(settings, "AURUMWEB_SITE_URL", "") or "").strip().rstrip("/")
    if configured:
        return configured
    if request is None:
        return ""
    return request.build_absolute_uri("/").rstrip("/")


def public_absolute_url(path="/", request=None):
    value = str(path or "/")
    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc:
        return value
    if not value.startswith("/"):
        value = f"/{value}"
    root = public_site_root(request)
    return f"{root}{value}" if root else value
