import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import IntegrationEvent, OutboundTask

logger = logging.getLogger(__name__)


class OutboundDeliveryError(RuntimeError):
    pass


def enqueue_outbound_task(task_type, payload=None, deduplication_key=None):
    values = {
        "task_type": task_type,
        "payload": payload or {},
        "status": OutboundTask.Status.PENDING,
        "available_at": timezone.now(),
    }
    if deduplication_key:
        task, _created = OutboundTask.objects.get_or_create(
            deduplication_key=deduplication_key,
            defaults=values,
        )
        return task
    return OutboundTask.objects.create(**values)


def _require_delivery(result, channel):
    if result is False:
        raise OutboundDeliveryError(f"{channel} delivery returned false")


def _telegram_admin_available(client):
    if not client.notifications_enabled:
        return False
    if not client.configured:
        raise OutboundDeliveryError("Telegram notifications are enabled but admin delivery is not configured")
    return True


def _telegram_client_available(client, user):
    if not user:
        return False
    profile = getattr(user, "profile", None)
    if not profile or not profile.telegram_notifications_enabled or not profile.telegram_chat_id:
        return False
    if not client.token_configured:
        raise OutboundDeliveryError("Client Telegram delivery is enabled but bot token is missing")
    return True


def dispatch_outbound_task(task_type, payload):
    if task_type == OutboundTask.TaskType.TELEGRAM_UPDATE:
        from .telegram_handlers import handle_telegram_update

        update = payload.get("update") or {}
        IntegrationEvent.objects.create(
            provider=IntegrationEvent.Provider.TELEGRAM,
            status=IntegrationEvent.Status.INFO,
            title="Telegram webhook получен",
            payload=update,
        )
        handle_telegram_update(update)
        return

    if task_type == OutboundTask.TaskType.QUICK_WEBHOOK_EVENT:
        IntegrationEvent.objects.create(
            provider=IntegrationEvent.Provider.TELEGRAM,
            status=IntegrationEvent.Status.INFO,
            title="Webhook быстрых консультаций получен",
            payload=payload.get("update") or {},
        )
        return

    if task_type == OutboundTask.TaskType.QUICK_TELEGRAM_METHOD:
        from .telegram import QuickConsultationBotClient

        client = QuickConsultationBotClient()
        if not client.token_configured:
            return
        _require_delivery(client.send_prepared_method(payload.get("method") or {}), "Quick Telegram method")
        return

    if task_type == OutboundTask.TaskType.QUICK_CONSULTATION_CREATED:
        from apps.leads.models import QuickConsultation

        from .telegram import QuickConsultationBotClient

        consultation = QuickConsultation.objects.get(pk=payload["consultation_id"])
        client = QuickConsultationBotClient()
        if not _telegram_admin_available(client):
            return
        _require_delivery(client.notify_quick_consultation_created(consultation), "Quick consultation admin Telegram")
        return

    if task_type in {
        OutboundTask.TaskType.INVOICE_ISSUED_EMAIL,
        OutboundTask.TaskType.INVOICE_ISSUED_ADMIN_TELEGRAM,
        OutboundTask.TaskType.INVOICE_ISSUED_CLIENT_TELEGRAM,
    }:
        from apps.billing.models import Invoice

        from .email import EmailNotificationClient
        from .telegram import TelegramBotClient

        invoice = Invoice.objects.select_related("user").get(pk=payload["invoice_id"])
        if task_type == OutboundTask.TaskType.INVOICE_ISSUED_EMAIL:
            client = EmailNotificationClient()
            if not (client.enabled and settings.AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED and invoice.client_email):
                return
            _require_delivery(client.notify_invoice_issued(invoice), "Invoice email")
            return
        client = TelegramBotClient()
        if task_type == OutboundTask.TaskType.INVOICE_ISSUED_ADMIN_TELEGRAM:
            if not _telegram_admin_available(client):
                return
            _require_delivery(client.notify_invoice_issued(invoice), "Invoice admin Telegram")
            return
        if not _telegram_client_available(client, invoice.user):
            return
        _require_delivery(client.notify_client_invoice_issued(invoice), "Invoice client Telegram")
        return

    if task_type in {
        OutboundTask.TaskType.PAYMENT_SUCCEEDED_ADMIN_EMAIL,
        OutboundTask.TaskType.PAYMENT_SUCCEEDED_CLIENT_EMAIL,
        OutboundTask.TaskType.PAYMENT_SUCCEEDED_ADMIN_TELEGRAM,
        OutboundTask.TaskType.PAYMENT_SUCCEEDED_CLIENT_TELEGRAM,
        OutboundTask.TaskType.QUICK_PAYMENT_SUCCEEDED,
        OutboundTask.TaskType.DUPLICATE_PAYMENT_EMAIL,
        OutboundTask.TaskType.DUPLICATE_PAYMENT_TELEGRAM,
        OutboundTask.TaskType.PAYMENT_REVIEW_EMAIL,
        OutboundTask.TaskType.PAYMENT_REVIEW_TELEGRAM,
    }:
        from apps.billing.models import Payment

        from .email import EmailNotificationClient
        from .telegram import QuickConsultationBotClient, TelegramBotClient

        payment = Payment.objects.select_related("invoice", "invoice__user").get(pk=payload["payment_id"])
        invoice = payment.invoice
        if task_type == OutboundTask.TaskType.PAYMENT_SUCCEEDED_ADMIN_EMAIL:
            client = EmailNotificationClient()
            if not client.configured:
                return
            _require_delivery(client.notify_payment_succeeded_admin(payment), "Payment admin email")
            return
        if task_type == OutboundTask.TaskType.PAYMENT_SUCCEEDED_CLIENT_EMAIL:
            client = EmailNotificationClient()
            if not (client.enabled and settings.AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED and invoice.client_email):
                return
            _require_delivery(client.notify_payment_succeeded_client(payment), "Payment client email")
            return
        if task_type == OutboundTask.TaskType.DUPLICATE_PAYMENT_EMAIL:
            client = EmailNotificationClient()
            if not client.configured:
                return
            _require_delivery(client.notify_duplicate_payment(payment), "Duplicate payment email")
            return
        if task_type == OutboundTask.TaskType.PAYMENT_REVIEW_EMAIL:
            client = EmailNotificationClient()
            if not client.configured:
                return
            _require_delivery(
                client.notify_payment_review(payment, payload.get("reason") or "требуется ручная проверка"),
                "Payment review email",
            )
            return
        if task_type == OutboundTask.TaskType.QUICK_PAYMENT_SUCCEEDED:
            consultation = getattr(invoice, "quick_consultation", None)
            if not consultation:
                return
            client = QuickConsultationBotClient()
            if not client.token_configured:
                return
            _require_delivery(client.notify_quick_consultation_paid(consultation, payment), "Quick payment Telegram")
            return
        client = TelegramBotClient()
        if task_type in {
            OutboundTask.TaskType.PAYMENT_SUCCEEDED_ADMIN_TELEGRAM,
            OutboundTask.TaskType.DUPLICATE_PAYMENT_TELEGRAM,
            OutboundTask.TaskType.PAYMENT_REVIEW_TELEGRAM,
        }:
            if not _telegram_admin_available(client):
                return
            if task_type in {
                OutboundTask.TaskType.DUPLICATE_PAYMENT_TELEGRAM,
                OutboundTask.TaskType.PAYMENT_REVIEW_TELEGRAM,
            }:
                reason = payload.get("reason") or (
                    "повторная оплата уже оплаченного счета; требуется проверка и возможный возврат"
                )
                _require_delivery(
                    client.notify_payment_failed(
                        payment,
                        reason,
                        payload.get("provider_payload") or {},
                    ),
                    "Payment review Telegram",
                )
            else:
                _require_delivery(client.notify_payment_succeeded(payment), "Payment admin Telegram")
            return
        if not _telegram_client_available(client, invoice.user):
            return
        _require_delivery(client.notify_client_payment_succeeded(payment), "Payment client Telegram")
        return

    if task_type in {
        OutboundTask.TaskType.DEADLINE_REMINDER_EMAIL,
        OutboundTask.TaskType.DEADLINE_REMINDER_TELEGRAM,
    }:
        from django.contrib.auth import get_user_model

        from .email import EmailNotificationClient
        from .telegram import TelegramBotClient

        if task_type == OutboundTask.TaskType.DEADLINE_REMINDER_EMAIL:
            client = EmailNotificationClient()
            if not (client.enabled and settings.AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED and payload.get("email")):
                return
            _require_delivery(client.notify_deadline_reminder(payload), "Deadline reminder email")
            return

        user_id = payload.get("user_id")
        user = get_user_model().objects.select_related("profile").filter(pk=user_id).first() if user_id else None
        client = TelegramBotClient()
        if not _telegram_client_available(client, user):
            return
        _require_delivery(client.notify_client_deadline_reminder(user, payload), "Deadline reminder Telegram")
        return

    if task_type == OutboundTask.TaskType.PROJECT_COMPLETED_TELEGRAM:
        from django.contrib.auth import get_user_model

        from apps.projects.models import Project

        from .telegram import TelegramBotClient

        project = Project.objects.select_related("user", "user__profile").get(pk=payload["project_id"])
        user = project.user
        if user is None and project.client_email:
            user = get_user_model().objects.select_related("profile").filter(email__iexact=project.client_email).first()
        client = TelegramBotClient()
        if not _telegram_client_available(client, user):
            return
        _require_delivery(client.notify_client_project_completed(project, user=user), "Project completed Telegram")
        return

    raise ValueError(f"Unsupported outbound task type: {task_type}")


def dispatch_or_enqueue(task_type, payload=None, deduplication_key=None):
    if getattr(settings, "AURUMWEB_OUTBOX_ENABLED", not settings.DEBUG):
        return enqueue_outbound_task(task_type, payload, deduplication_key)
    dispatch_outbound_task(task_type, payload or {})
    return None


def recover_stale_outbound_tasks():
    stale_before = timezone.now() - timedelta(seconds=settings.AURUMWEB_OUTBOX_STALE_SECONDS)
    return OutboundTask.objects.filter(
        status=OutboundTask.Status.PROCESSING,
        locked_at__lt=stale_before,
    ).update(
        status=OutboundTask.Status.RETRY,
        available_at=timezone.now(),
        locked_at=None,
        last_error="Worker stopped before completing the operation.",
    )


def claim_next_outbound_task():
    with transaction.atomic():
        task = (
            OutboundTask.objects.select_for_update(skip_locked=True)
            .filter(
                status__in=(OutboundTask.Status.PENDING, OutboundTask.Status.RETRY),
                available_at__lte=timezone.now(),
            )
            .order_by("available_at", "created_at")
            .first()
        )
        if not task:
            return None
        task.status = OutboundTask.Status.PROCESSING
        task.attempts += 1
        task.locked_at = timezone.now()
        task.save(update_fields=("status", "attempts", "locked_at", "updated_at"))
        return task.pk


def process_outbound_task(task_id):
    task = OutboundTask.objects.get(pk=task_id)
    try:
        dispatch_outbound_task(task.task_type, task.payload)
    except Exception as exc:
        logger.exception("Outbound task %s failed", task.pk)
        task.refresh_from_db(fields=("attempts",))
        if task.attempts >= settings.AURUMWEB_OUTBOX_MAX_ATTEMPTS:
            status = OutboundTask.Status.FAILED
            available_at = task.available_at
            IntegrationEvent.objects.create(
                provider=IntegrationEvent.Provider.SYSTEM,
                status=IntegrationEvent.Status.ERROR,
                title="Исходящая операция требует внимания",
                payload={"task_id": task.pk, "task_type": task.task_type, "error": str(exc)},
            )
        else:
            status = OutboundTask.Status.RETRY
            delay = min(settings.AURUMWEB_OUTBOX_RETRY_BASE_SECONDS * (2 ** (task.attempts - 1)), 3600)
            available_at = timezone.now() + timedelta(seconds=delay)
        OutboundTask.objects.filter(pk=task.pk).update(
            status=status,
            available_at=available_at,
            locked_at=None,
            last_error=str(exc)[:4000],
            updated_at=timezone.now(),
        )
        return False

    OutboundTask.objects.filter(pk=task.pk).update(
        status=OutboundTask.Status.SUCCEEDED,
        locked_at=None,
        completed_at=timezone.now(),
        last_error="",
        updated_at=timezone.now(),
    )
    return True


def process_available_outbound_tasks(limit=None):
    recover_stale_outbound_tasks()
    limit = limit or settings.AURUMWEB_OUTBOX_BATCH_SIZE
    processed = 0
    for _index in range(limit):
        task_id = claim_next_outbound_task()
        if task_id is None:
            break
        process_outbound_task(task_id)
        processed += 1
    return processed
