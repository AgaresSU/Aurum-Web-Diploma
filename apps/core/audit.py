from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.apps import apps
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save

from .models import AuditEvent

_audit_context = ContextVar("aurumweb_audit_context", default=None)

AUDIT_FIELDS = {
    "accounts.Profile": (
        "role",
        "client_type",
        "personal_data_consent",
        "telegram_notifications_enabled",
        "totp_enabled",
    ),
    "billing.Invoice": ("title", "amount", "status", "due_date"),
    "billing.InvoiceItem": ("name", "quantity", "unit_price"),
    "billing.Order": (
        "title",
        "status",
        "estimated_amount_min",
        "estimated_amount_max",
        "starts_at",
        "due_at",
    ),
    "billing.Payment": (
        "provider",
        "status",
        "amount",
        "signature_valid",
        "provider_operation_id",
        "provider_state_code",
        "last_reconciled_at",
        "paid_at",
    ),
    "billing.ManagedSite": (
        "title",
        "status",
        "support_plan",
        "domain_name",
        "domain_expires_at",
        "hosting_expires_at",
        "ssl_expires_at",
        "support_until",
        "analytics_connected",
        "seo_baseline_ready",
    ),
    "content.Service": (
        "title",
        "price_prefix",
        "price_amount",
        "price_unit",
        "is_featured",
        "is_published",
        "sort_order",
    ),
    "integrations.TelegramBotSettings": (
        "is_enabled",
        "bot_username",
        "webhook_url",
    ),
    "integrations.QuickConsultationBotSettings": (
        "is_enabled",
        "bot_username",
        "webhook_url",
    ),
    "integrations.RobokassaSettings": (
        "is_enabled",
        "merchant_login",
        "test_mode",
        "hash_algorithm",
        "payment_url",
        "receipt_enabled",
        "receipt_sno",
        "receipt_tax",
        "receipt_payment_method",
        "receipt_payment_object",
    ),
    "leads.Lead": ("subject", "service_type", "source", "status"),
    "leads.QuickConsultation": (
        "status",
        "quoted_amount",
        "invoice_id",
        "offer_accepted_at",
        "privacy_accepted_at",
        "pending_action",
    ),
    "messaging.Conversation": ("title", "status"),
}


def set_audit_context(actor_id=None, ip_address=None, channel="office"):
    return _audit_context.set(
        {
            "actor_id": actor_id,
            "ip_address": ip_address,
            "channel": channel,
        }
    )


def reset_audit_context(token):
    _audit_context.reset(token)


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _model_key(instance):
    return instance._meta.label


def _snapshot(instance):
    values = {}
    for field_name in AUDIT_FIELDS.get(_model_key(instance), ()):
        values[field_name] = _json_value(getattr(instance, field_name, None))
    return values


def _should_audit(instance):
    context = _audit_context.get()
    return bool(context and context.get("actor_id") and _model_key(instance) in AUDIT_FIELDS)


def _label(instance):
    return f"{instance._meta.verbose_name} #{instance.pk}"


def _record(instance, action, before=None, after=None):
    context = _audit_context.get()
    if not context:
        return
    AuditEvent.objects.create(
        actor_id=context["actor_id"],
        action=action,
        object_type=_model_key(instance),
        object_id=str(instance.pk),
        object_label=_label(instance),
        channel=context["channel"],
        ip_address=context["ip_address"],
        before=before or {},
        after=after or {},
    )


def _pre_save(sender, instance, raw=False, **kwargs):
    if raw or not _should_audit(instance):
        return
    instance._audit_before = {}
    if instance.pk:
        previous = sender._base_manager.filter(pk=instance.pk).first()
        if previous:
            instance._audit_before = _snapshot(previous)


def _post_save(sender, instance, created=False, raw=False, **kwargs):
    if raw or not _should_audit(instance):
        return
    before = getattr(instance, "_audit_before", {})
    after = _snapshot(instance)
    if created:
        _record(instance, AuditEvent.Action.CREATE, after=after)
    elif before != after:
        _record(instance, AuditEvent.Action.UPDATE, before=before, after=after)


def _pre_delete(sender, instance, **kwargs):
    if _should_audit(instance):
        instance._audit_before_delete = _snapshot(instance)


def _post_delete(sender, instance, **kwargs):
    if _should_audit(instance):
        _record(
            instance,
            AuditEvent.Action.DELETE,
            before=getattr(instance, "_audit_before_delete", _snapshot(instance)),
        )


def connect_audit_signals():
    for model_label in AUDIT_FIELDS:
        model = apps.get_model(model_label)
        signal_id = model_label.lower()
        pre_save.connect(_pre_save, sender=model, dispatch_uid=f"audit.pre_save.{signal_id}")
        post_save.connect(_post_save, sender=model, dispatch_uid=f"audit.post_save.{signal_id}")
        pre_delete.connect(_pre_delete, sender=model, dispatch_uid=f"audit.pre_delete.{signal_id}")
        post_delete.connect(_post_delete, sender=model, dispatch_uid=f"audit.post_delete.{signal_id}")
