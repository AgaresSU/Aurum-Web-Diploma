from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.integrations.email import EmailNotificationClient
from apps.integrations.models import IntegrationEvent, OutboundTask
from apps.integrations.outbox import enqueue_outbound_task
from apps.integrations.robokassa import RobokassaClient
from apps.integrations.telegram import QuickConsultationBotClient, TelegramBotClient
from apps.projects.models import Project
from apps.projects.services import update_project_status

from .models import Invoice, Order, Payment


def _enqueue_payment_review_notifications(payment, reason, payload):
    task_payload = {
        "payment_id": payment.pk,
        "reason": reason,
        "provider_payload": payload,
    }
    enqueue_outbound_task(
        OutboundTask.TaskType.PAYMENT_REVIEW_EMAIL,
        task_payload,
        f"payment-review-email:{payment.pk}",
    )
    enqueue_outbound_task(
        OutboundTask.TaskType.PAYMENT_REVIEW_TELEGRAM,
        task_payload,
        f"payment-review-telegram:{payment.pk}",
    )


def _notify_payment_review(payment, reason, payload):
    if settings.AURUMWEB_OUTBOX_ENABLED:
        _enqueue_payment_review_notifications(payment, reason, payload)
        return
    EmailNotificationClient().notify_payment_review(payment, reason)
    TelegramBotClient().notify_payment_failed(payment, reason, payload)


def mark_payment_for_review(payment, payload, reason, *, signature_valid=False):
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related("invoice").get(pk=payment.pk)
        if payment.status in (Payment.Status.SUCCEEDED, Payment.Status.DUPLICATE, Payment.Status.REVIEW):
            return payment
        payment.status = Payment.Status.REVIEW
        payment.signature_valid = signature_valid
        payment.provider_payload = payload
        payment.paid_at = timezone.now()
        payment.reconciliation_note = reason
        payment.save(
            update_fields=(
                "status",
                "signature_valid",
                "provider_payload",
                "paid_at",
                "reconciliation_note",
            )
        )
        IntegrationEvent.objects.create(
            provider=IntegrationEvent.Provider.ROBOKASSA,
            status=IntegrationEvent.Status.ERROR,
            title="Оплата требует ручной проверки",
            payload={
                "invoice_id": payment.invoice_id,
                "payment_id": payment.pk,
                "reason": reason,
            },
        )
        _notify_payment_review(payment, reason, payload)
        return payment


def _proposal_body(lead):
    return (lead.metadata or {}).get("proposal_draft", {}).get("body") or lead.task


def _user_requisites(user):
    if not user:
        return {}
    profile = getattr(user, "profile", None)
    if not profile:
        return {}
    return profile.requisites_payload()


def client_requisites_from_lead(lead):
    profile_requisites = _user_requisites(lead.user)
    metadata = lead.metadata or {}
    brief = metadata.get("public_brief", {})
    qualification = metadata.get("qualification", {})
    contact = qualification.get("contact") or brief.get("contact", "")
    fallback = {
        "client_type": "unknown",
        "client_type_display": "Не уточнен",
        "payer_name": lead.name,
        "legal_name": lead.name,
        "company": "",
        "phone": "",
        "telegram_username": "",
        "inn": "",
        "kpp": "",
        "ogrn": "",
        "billing_address": "",
        "contract_contact": contact,
        "requisites_comment": "Реквизиты нужно уточнить перед выставлением финального счета.",
        "personal_data_consent": False,
        "complete": False,
    }
    return {**fallback, **profile_requisites}


def issue_invoice(invoice):
    previous_status = invoice.status
    notify = previous_status != Invoice.Status.ISSUED
    with transaction.atomic():
        invoice.status = Invoice.Status.ISSUED
        update_fields = ["status", "updated_at"]
        if notify:
            link_days = max(int(getattr(settings, "AURUMWEB_PUBLIC_INVOICE_LINK_DAYS", 30)), 1)
            invoice.public_access_expires_at = timezone.now() + timedelta(days=link_days)
            invoice.public_access_revoked_at = None
            update_fields.extend(("public_access_expires_at", "public_access_revoked_at"))
        invoice.save(update_fields=update_fields)
        if notify and settings.AURUMWEB_OUTBOX_ENABLED:
            payload = {"invoice_id": invoice.pk}
            enqueue_outbound_task(
                OutboundTask.TaskType.INVOICE_ISSUED_EMAIL,
                payload,
                f"invoice-issued-email:{invoice.pk}",
            )
            enqueue_outbound_task(
                OutboundTask.TaskType.INVOICE_ISSUED_ADMIN_TELEGRAM,
                payload,
                f"invoice-issued-admin-telegram:{invoice.pk}",
            )
            enqueue_outbound_task(
                OutboundTask.TaskType.INVOICE_ISSUED_CLIENT_TELEGRAM,
                payload,
                f"invoice-issued-client-telegram:{invoice.pk}",
            )
    if notify and not settings.AURUMWEB_OUTBOX_ENABLED:
        EmailNotificationClient().notify_invoice_issued(invoice)
        telegram = TelegramBotClient()
        telegram.notify_invoice_issued(invoice)
        telegram.notify_client_invoice_issued(invoice)
    if notify and invoice.project_id:
        update_project_status(
            invoice.project,
            Project.Status.WAITING_PAYMENT,
            title="Счет выставлен",
            description=f"{invoice.title}: {invoice.amount} ₽",
            next_action="Оплатить выставленный счет",
        )
    return invoice


def create_order_from_lead(lead):
    order, _created = Order.objects.get_or_create(
        lead=lead,
        defaults={
            "project": lead.project,
            "user": lead.user,
            "title": lead.subject or lead.service_type or f"Заказ по заявке #{lead.id}",
            "client_name": lead.name,
            "client_email": lead.email,
            "scope": _proposal_body(lead),
            "status": Order.Status.DISCOVERY,
        },
    )
    return order


def create_invoice_from_lead(lead, amount="0.00"):
    requisites = client_requisites_from_lead(lead)
    invoice = Invoice.objects.create(
        project=lead.project,
        lead=lead,
        user=lead.user,
        client_name=requisites.get("payer_name") or lead.name,
        client_email=(lead.user.email if lead.user and lead.user.email else lead.email),
        client_requisites=requisites,
        title=lead.subject or lead.service_type or f"Счет по заявке #{lead.id}",
        description=_proposal_body(lead),
        amount=amount,
        status=Invoice.Status.DRAFT,
    )
    order = create_order_from_lead(lead)
    order.invoice = invoice
    order.status = Order.Status.PROPOSAL
    order.save(update_fields=("invoice", "status", "updated_at"))
    return invoice


def create_robokassa_payment(invoice, *, reuse_pending=False):
    if invoice.status != Invoice.Status.ISSUED:
        raise ValueError("Robokassa payment can be created only for issued invoices.")
    if reuse_pending:
        payment = invoice.payments.filter(
            provider=Payment.Provider.ROBOKASSA,
            status=Payment.Status.PENDING,
            amount=invoice.amount,
        ).first()
        if payment:
            return payment
    return Payment.objects.create(
        invoice=invoice,
        provider=Payment.Provider.ROBOKASSA,
        status=Payment.Status.PENDING,
        amount=invoice.amount,
    )


def payment_url_for_invoice(invoice):
    payment = create_robokassa_payment(invoice, reuse_pending=True)
    client = RobokassaClient()
    if not client.configured:
        IntegrationEvent.objects.create(
            provider=IntegrationEvent.Provider.ROBOKASSA,
            status=IntegrationEvent.Status.WARNING,
            title="Robokassa не настроена",
            payload={"invoice_id": invoice.id, "payment_id": payment.id},
        )
    return client.build_payment_url(payment, invoice.title)


def mark_payment_succeeded(payment, payload, signature_valid):
    notify_success = False
    notify_duplicate = False
    quick_consultation = None

    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related("invoice").get(pk=payment.pk)
        invoice = Invoice.objects.select_for_update().get(pk=payment.invoice_id)
        payment.invoice = invoice

        if payment.status in (Payment.Status.SUCCEEDED, Payment.Status.DUPLICATE, Payment.Status.REVIEW):
            return payment

        previous_success = (
            Payment.objects.select_for_update()
            .filter(invoice_id=invoice.pk, status=Payment.Status.SUCCEEDED)
            .exclude(pk=payment.pk)
            .first()
        )

        if (
            payment.provider == Payment.Provider.ROBOKASSA
            and invoice.status != Invoice.Status.ISSUED
            and not previous_success
        ):
            reason = (
                "Robokassa подтвердила оплату счета, который к моменту подтверждения "
                f"имел статус «{invoice.get_status_display()}»"
            )
            payment.status = Payment.Status.REVIEW
            payment.signature_valid = signature_valid
            payment.provider_payload = payload
            payment.paid_at = timezone.now()
            payment.reconciliation_note = reason
            payment.save(
                update_fields=(
                    "status",
                    "signature_valid",
                    "provider_payload",
                    "paid_at",
                    "reconciliation_note",
                )
            )
            IntegrationEvent.objects.create(
                provider=IntegrationEvent.Provider.ROBOKASSA,
                status=IntegrationEvent.Status.ERROR,
                title="Оплата требует ручной проверки",
                payload={**payload, "invoice_id": invoice.pk, "payment_id": payment.pk, "reason": reason},
            )
            _notify_payment_review(payment, reason, payload)
            return payment

        payment.signature_valid = signature_valid
        payment.provider_payload = payload
        payment.paid_at = timezone.now()

        if previous_success:
            payment.status = Payment.Status.DUPLICATE
            payment.save(update_fields=("status", "signature_valid", "provider_payload", "paid_at"))
            IntegrationEvent.objects.create(
                provider=IntegrationEvent.Provider.ROBOKASSA,
                status=IntegrationEvent.Status.ERROR,
                title="Обнаружена повторная оплата счета",
                payload={
                    **payload,
                    "invoice_id": invoice.pk,
                    "payment_id": payment.pk,
                    "original_payment_id": previous_success.pk,
                },
            )
            notify_duplicate = True
        else:
            payment.status = Payment.Status.SUCCEEDED
            payment.save(update_fields=("status", "signature_valid", "provider_payload", "paid_at"))
            invoice.status = Invoice.Status.PAID
            invoice.save(update_fields=("status", "updated_at"))
            invoice.payments.filter(
                provider=Payment.Provider.ROBOKASSA,
                status=Payment.Status.PENDING,
            ).exclude(
                pk=payment.pk
            ).update(status=Payment.Status.CANCELLED)
            IntegrationEvent.objects.create(
                provider=IntegrationEvent.Provider.ROBOKASSA,
                status=IntegrationEvent.Status.SUCCESS,
                title="Оплата подтверждена",
                payload={**payload, "invoice_id": invoice.pk, "payment_id": payment.pk},
            )
            notify_success = True

            if invoice.project_id:
                update_project_status(
                    invoice.project,
                    Project.Status.IN_PROGRESS,
                    title="Оплата получена",
                    description=f"{invoice.amount} ₽",
                    next_action="Ожидайте обновления по текущему этапу",
                )

            quick_consultation = getattr(invoice, "quick_consultation", None)
            if quick_consultation:
                from apps.leads.models import QuickConsultation

                quick_consultation.status = QuickConsultation.Status.PAID
                quick_consultation.save(update_fields=("status", "updated_at"))

        if settings.AURUMWEB_OUTBOX_ENABLED and notify_duplicate:
            task_payload = {"payment_id": payment.pk, "provider_payload": payload}
            enqueue_outbound_task(
                OutboundTask.TaskType.DUPLICATE_PAYMENT_EMAIL,
                task_payload,
                f"duplicate-payment-email:{payment.pk}",
            )
            enqueue_outbound_task(
                OutboundTask.TaskType.DUPLICATE_PAYMENT_TELEGRAM,
                task_payload,
                f"duplicate-payment-telegram:{payment.pk}",
            )
        elif settings.AURUMWEB_OUTBOX_ENABLED and notify_success:
            task_payload = {"payment_id": payment.pk}
            for task_type, suffix in (
                (OutboundTask.TaskType.PAYMENT_SUCCEEDED_ADMIN_EMAIL, "admin-email"),
                (OutboundTask.TaskType.PAYMENT_SUCCEEDED_CLIENT_EMAIL, "client-email"),
                (OutboundTask.TaskType.PAYMENT_SUCCEEDED_ADMIN_TELEGRAM, "admin-telegram"),
                (OutboundTask.TaskType.PAYMENT_SUCCEEDED_CLIENT_TELEGRAM, "client-telegram"),
            ):
                enqueue_outbound_task(task_type, task_payload, f"payment-succeeded-{suffix}:{payment.pk}")
            if quick_consultation:
                enqueue_outbound_task(
                    OutboundTask.TaskType.QUICK_PAYMENT_SUCCEEDED,
                    task_payload,
                    f"quick-payment-succeeded:{payment.pk}",
                )

    if notify_duplicate and not settings.AURUMWEB_OUTBOX_ENABLED:
        EmailNotificationClient().notify_duplicate_payment(payment)
        TelegramBotClient().notify_payment_failed(
            payment,
            "повторная оплата уже оплаченного счета; требуется проверка и возможный возврат",
            payload,
        )
    elif notify_success and not settings.AURUMWEB_OUTBOX_ENABLED:
        EmailNotificationClient().notify_payment_succeeded(payment)
        telegram = TelegramBotClient()
        telegram.notify_payment_succeeded(payment)
        telegram.notify_client_payment_succeeded(payment)
        if quick_consultation:
            QuickConsultationBotClient().notify_quick_consultation_paid(quick_consultation, payment)
    return payment
