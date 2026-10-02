import secrets

from django.conf import settings

from .audit import reset_audit_context, set_audit_context
from .legal_acceptance import request_ip


def _serialize_csp(directives, nonce=""):
    parts = []
    for name, values in directives.items():
        if values is None:
            parts.append(name)
            continue
        rendered_values = (value.replace("{nonce}", nonce) for value in values)
        parts.append(f"{name} {' '.join(rendered_values)}")
    return "; ".join(parts)


class ContentSecurityPolicyReportOnlyMiddleware:
    """Add enforcing and report-only CSP headers configured by environment."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(24)
        response = self.get_response(request)
        enforce_directives = getattr(settings, "CSP_ENFORCE_DIRECTIVES", None)
        if getattr(settings, "CSP_ENFORCE_ENABLED", False) and enforce_directives:
            response.setdefault("Content-Security-Policy", _serialize_csp(enforce_directives, request.csp_nonce))
        directives = getattr(settings, "CSP_REPORT_ONLY_DIRECTIVES", None)
        if getattr(settings, "CSP_REPORT_ONLY_ENABLED", False) and directives:
            response.setdefault("Content-Security-Policy-Report-Only", _serialize_csp(directives, request.csp_nonce))
        return response


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        actor_id = None
        if request.user.is_authenticated and request.user.is_staff:
            actor_id = request.user.pk
        if request.path.startswith("/admin/"):
            channel = "django_admin"
        elif request.path.startswith("/office/"):
            channel = "office"
        else:
            channel = "web"
        token = set_audit_context(actor_id=actor_id, ip_address=request_ip(request), channel=channel)
        try:
            return self.get_response(request)
        finally:
            reset_audit_context(token)
