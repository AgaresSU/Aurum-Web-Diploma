from django.conf import settings
from django.core.cache import cache


def client_ip(request):
    """Return the address added by the nearest trusted reverse proxy.

    Nginx overwrites ``X-Forwarded-For`` in the supplied production
    configuration.  Reading the right-most value also keeps the application
    safe when an older proxy configuration appends to a client-supplied list.
    """
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if getattr(settings, "AURUMWEB_TRUST_X_FORWARDED_FOR", False) and forwarded_for:
        return forwarded_for.rsplit(",", 1)[-1].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _client_key(request):
    if request.user.is_authenticated:
        return f"user:{request.user.pk}"
    return f"ip:{client_ip(request)}"


def rate_limited(request, scope, limit=8, window=60):
    key = f"rate:{scope}:{_client_key(request)}"
    if cache.add(key, 1, timeout=window):
        return False
    try:
        current = cache.incr(key)
    except ValueError:
        current = 1 if cache.add(key, 1, timeout=window) else cache.incr(key)
    return int(current) > limit
