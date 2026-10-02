import hashlib
import json
from ipaddress import ip_address

from django.contrib.auth import get_user_model

from .models import ConsentAcceptance


def request_ip(request):
    if request is None:
        return None
    value = request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR") or ""
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def _document(document_type, scope):
    from .views import FAST_LEGAL_PAGES, LEGAL_PAGES, LEGAL_UPDATED_AT

    pages = FAST_LEGAL_PAGES if scope == ConsentAcceptance.DocumentScope.FAST else LEGAL_PAGES
    body = pages[document_type]
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return LEGAL_UPDATED_AT, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def record_legal_acceptance(
    *,
    document_type,
    subject,
    channel,
    scope=ConsentAcceptance.DocumentScope.GENERAL,
    request=None,
    user=None,
    accepted_at=None,
):
    version, checksum = _document(document_type, scope)
    subject_type = subject._meta.label
    subject_id = str(subject.pk)
    if user is None and isinstance(subject, get_user_model()):
        user = subject
    defaults = {
        "user": user,
        "document_version": version,
        "ip_address": request_ip(request),
    }
    if accepted_at is not None:
        defaults["accepted_at"] = accepted_at
    acceptance, _ = ConsentAcceptance.objects.get_or_create(
        subject_type=subject_type,
        subject_id=subject_id,
        document_type=document_type,
        document_scope=scope,
        document_checksum=checksum,
        channel=channel,
        defaults=defaults,
    )
    return acceptance
