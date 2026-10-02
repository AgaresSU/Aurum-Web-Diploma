import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Invoice, InvoiceItem, Payment
from apps.billing.services import create_robokassa_payment, issue_invoice
from apps.core.legal_acceptance import record_legal_acceptance
from apps.core.models import ConsentAcceptance
from apps.integrations.models import IntegrationEvent
from apps.projects.services import ensure_project_for_instance

from .models import QuickConsultation

QUICK_CONSULTATION_ALIASES = {"consult", "consultation", "quick", "quick-consultation", "quick_consultation"}
FIXED_QUICK_CONSULTATION_AMOUNT = Decimal("5000.00")
MIN_QUICK_PAYMENT_AMOUNT = Decimal("10.00")
MAX_QUICK_PAYMENT_AMOUNT = Decimal("200000.00")
QUICK_PAYMENT_CONSULTATION_ITEM_NAME = "Консультация по поддержке Telegram-бота"
QUICK_PAYMENT_BOT_ITEM_NAME = "Написание и разработка Telegram-бота"
QUICK_PAYMENT_APPLICATION_ITEM_NAME = "Разработка программного приложения"
QUICK_PAYMENT_WEBSITE_ITEM_NAME = "Создание сайта"
QUICK_PAYMENT_BOT_MIN_AMOUNT = Decimal("5001.00")
QUICK_PAYMENT_APPLICATION_MIN_AMOUNT = Decimal("15000.00")
QUICK_PAYMENT_WEBSITE_MIN_AMOUNT = Decimal("30000.00")
QUICK_PAYMENT_ITEM_NAME = QUICK_PAYMENT_CONSULTATION_ITEM_NAME
QUICK_LEGAL_STATUSES = (
    QuickConsultation.Status.WAITING_OFFER,
    QuickConsultation.Status.WAITING_PRIVACY,
    QuickConsultation.Status.WAITING_EMAIL,
    QuickConsultation.Status.WAITING_EMAIL_CONFIRM,
)


def _absolute_url(path):
    base_url = settings.AURUMWEB_SITE_URL.rstrip("/")
    return f"{base_url}{path}" if base_url else path


def _invoice_payment_url(invoice):
    return _absolute_url(reverse("billing:pay-invoice", kwargs={"token": invoice.public_token}))


def quick_payment_item_name_for_amount(amount):
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amount >= QUICK_PAYMENT_WEBSITE_MIN_AMOUNT:
        return QUICK_PAYMENT_WEBSITE_ITEM_NAME
    if amount >= QUICK_PAYMENT_APPLICATION_MIN_AMOUNT:
        return QUICK_PAYMENT_APPLICATION_ITEM_NAME
    if amount >= QUICK_PAYMENT_BOT_MIN_AMOUNT:
        return QUICK_PAYMENT_BOT_ITEM_NAME
    return QUICK_PAYMENT_CONSULTATION_ITEM_NAME


def is_quick_consultation_payload(value):
    return str(value or "").strip().lower() in QUICK_CONSULTATION_ALIASES


def telegram_identity(chat_id, message):
    from_user = message.get("from") or {}
    first_name = (from_user.get("first_name") or "").strip()
    last_name = (from_user.get("last_name") or "").strip()
    username = (from_user.get("username") or "").strip()
    return {
        "telegram_chat_id": str(chat_id),
        "telegram_user_id": str(from_user.get("id") or ""),
        "telegram_username": f"@{username}" if username else "",
        "first_name": first_name,
        "last_name": last_name,
        "client_name": " ".join(part for part in (first_name, last_name) if part).strip(),
    }


def pending_quick_consultation(chat_id):
    return (
        QuickConsultation.objects.filter(
            telegram_chat_id=str(chat_id),
            status=QuickConsultation.Status.WAITING_QUESTION,
        )
        .order_by("-updated_at")
        .first()
    )


def pending_quick_payment(chat_id):
    return (
        QuickConsultation.objects.filter(
            telegram_chat_id=str(chat_id),
            status=QuickConsultation.Status.WAITING_PAYMENT_AMOUNT,
        )
        .order_by("-updated_at")
        .first()
    )


def pending_quick_legal(chat_id):
    return (
        QuickConsultation.objects.filter(
            telegram_chat_id=str(chat_id),
            status__in=QUICK_LEGAL_STATUSES,
        )
        .order_by("-updated_at")
        .first()
    )


def pending_quick_offer(chat_id):
    return (
        QuickConsultation.objects.filter(
            telegram_chat_id=str(chat_id),
            status=QuickConsultation.Status.WAITING_OFFER,
        )
        .order_by("-updated_at")
        .first()
    )


def pending_quick_privacy(chat_id):
    return (
        QuickConsultation.objects.filter(
            telegram_chat_id=str(chat_id),
            status=QuickConsultation.Status.WAITING_PRIVACY,
        )
        .order_by("-updated_at")
        .first()
    )


def pending_quick_email(chat_id):
    return (
        QuickConsultation.objects.filter(
            telegram_chat_id=str(chat_id),
            status=QuickConsultation.Status.WAITING_EMAIL,
        )
        .order_by("-updated_at")
        .first()
    )


def pending_quick_email_confirm(chat_id):
    return (
        QuickConsultation.objects.filter(
            telegram_chat_id=str(chat_id),
            status=QuickConsultation.Status.WAITING_EMAIL_CONFIRM,
        )
        .order_by("-updated_at")
        .first()
    )


def latest_active_quick_consultation(chat_id):
    closed_statuses = (
        QuickConsultation.Status.PAID,
        QuickConsultation.Status.CLOSED,
        QuickConsultation.Status.CANCELLED,
    )
    return (
        QuickConsultation.objects.filter(telegram_chat_id=str(chat_id))
        .exclude(status__in=closed_statuses)
        .order_by("-updated_at", "-created_at")
        .first()
    )


def latest_quick_client_profile(chat_id, exclude_pk=None):
    queryset = QuickConsultation.objects.filter(telegram_chat_id=str(chat_id)).exclude(
        status=QuickConsultation.Status.CANCELLED
    )
    if exclude_pk:
        queryset = queryset.exclude(pk=exclude_pk)
    for consultation in queryset.order_by("-updated_at", "-created_at"):
        if consultation.client_email or consultation.offer_accepted_at or consultation.privacy_accepted_at:
            return consultation
    return None


def latest_quick_onboarding(chat_id):
    return (
        QuickConsultation.objects.filter(
            telegram_chat_id=str(chat_id),
            status=QuickConsultation.Status.CLOSED,
            invoice__isnull=True,
            question="",
        )
        .order_by("-updated_at", "-created_at")
        .first()
    )


def _has_complete_quick_profile(profile):
    return bool(profile and profile.offer_accepted_at and profile.privacy_accepted_at and profile.client_email)


def _reuse_quick_client_profile(consultation, update_fields):
    profile = latest_quick_client_profile(consultation.telegram_chat_id, exclude_pk=consultation.pk)
    if not profile:
        return
    if not consultation.client_email and profile.client_email:
        consultation.client_email = profile.client_email
        update_fields.append("client_email")
    if not consultation.offer_accepted_at and profile.offer_accepted_at:
        consultation.offer_accepted_at = profile.offer_accepted_at
        update_fields.append("offer_accepted_at")
    if not consultation.privacy_accepted_at and profile.privacy_accepted_at:
        consultation.privacy_accepted_at = profile.privacy_accepted_at
        update_fields.append("privacy_accepted_at")


def _apply_telegram_identity(consultation, message, update_fields):
    for field, value in telegram_identity(consultation.telegram_chat_id, message).items():
        if value:
            setattr(consultation, field, value)
            update_fields.append(field)


def _quick_post_legal_status(consultation):
    if consultation.pending_action == QuickConsultation.PendingAction.PAYMENT:
        return QuickConsultation.Status.WAITING_PAYMENT_AMOUNT
    if consultation.pending_action == QuickConsultation.PendingAction.CONSULTATION:
        return QuickConsultation.Status.WAITING_QUESTION
    return QuickConsultation.Status.CLOSED


def quick_legal_entry_status(consultation):
    if not consultation.offer_accepted_at:
        return QuickConsultation.Status.WAITING_OFFER
    if not consultation.privacy_accepted_at:
        return QuickConsultation.Status.WAITING_PRIVACY
    if not consultation.client_email:
        return QuickConsultation.Status.WAITING_EMAIL
    return _quick_post_legal_status(consultation)


def quick_payment_entry_status(consultation):
    if not consultation.offer_accepted_at:
        return QuickConsultation.Status.WAITING_OFFER
    if not consultation.privacy_accepted_at:
        return QuickConsultation.Status.WAITING_PRIVACY
    if not consultation.client_email:
        return QuickConsultation.Status.WAITING_EMAIL
    return QuickConsultation.Status.WAITING_PAYMENT_AMOUNT


def start_quick_entry(chat_id, message, pending_action=QuickConsultation.PendingAction.MENU):
    consultation = pending_quick_legal(chat_id)
    if consultation:
        update_fields = ["status", "pending_action", "updated_at"]
        consultation.pending_action = pending_action
        _apply_telegram_identity(consultation, message, update_fields)
        if pending_action == QuickConsultation.PendingAction.PAYMENT and not consultation.question:
            consultation.question = "Оплата быстрой консультации."
            update_fields.append("question")
        _reuse_quick_client_profile(consultation, update_fields)
        consultation.status = quick_legal_entry_status(consultation)
        consultation.save(update_fields=tuple(dict.fromkeys(update_fields)))
        return consultation

    identity = telegram_identity(chat_id, message)
    profile = latest_quick_client_profile(chat_id)
    if pending_action == QuickConsultation.PendingAction.MENU and _has_complete_quick_profile(profile):
        consultation = latest_quick_onboarding(chat_id)
        if consultation:
            update_fields = ["status", "pending_action", "updated_at"]
            consultation.pending_action = pending_action
            _apply_telegram_identity(consultation, message, update_fields)
            _reuse_quick_client_profile(consultation, update_fields)
            consultation.status = QuickConsultation.Status.CLOSED
            consultation.save(update_fields=tuple(dict.fromkeys(update_fields)))
            return consultation

    consultation = QuickConsultation.objects.create(
        **identity,
        question="Оплата быстрой консультации." if pending_action == QuickConsultation.PendingAction.PAYMENT else "",
        status=QuickConsultation.Status.WAITING_OFFER,
        pending_action=pending_action,
    )
    update_fields = ["status", "updated_at"]
    _reuse_quick_client_profile(consultation, update_fields)
    consultation.status = quick_legal_entry_status(consultation)
    consultation.save(update_fields=tuple(dict.fromkeys(update_fields)))
    return consultation


def start_quick_consultation(chat_id, message):
    consultation = pending_quick_consultation(chat_id)
    if consultation:
        return consultation
    return start_quick_entry(chat_id, message, QuickConsultation.PendingAction.CONSULTATION)


def cancel_quick_consultation(chat_id):
    consultation = pending_quick_consultation(chat_id) or pending_quick_payment(chat_id) or pending_quick_legal(chat_id)
    if not consultation:
        return None
    consultation.status = QuickConsultation.Status.CANCELLED
    consultation.save(update_fields=("status", "updated_at"))
    return consultation


def start_quick_payment(chat_id, message):
    consultation = (
        pending_quick_legal(chat_id) or pending_quick_payment(chat_id) or latest_active_quick_consultation(chat_id)
    )
    if consultation:
        update_fields = ["status", "pending_action", "updated_at"]
        consultation.pending_action = QuickConsultation.PendingAction.PAYMENT
        _apply_telegram_identity(consultation, message, update_fields)
        if not consultation.question:
            consultation.question = "Оплата быстрой консультации."
            update_fields.append("question")
        _reuse_quick_client_profile(consultation, update_fields)
        consultation.status = quick_payment_entry_status(consultation)
        consultation.save(update_fields=tuple(dict.fromkeys(update_fields)))
        return consultation
    consultation = QuickConsultation.objects.create(
        **telegram_identity(chat_id, message),
        question="Оплата быстрой консультации.",
        status=QuickConsultation.Status.WAITING_OFFER,
        pending_action=QuickConsultation.PendingAction.PAYMENT,
    )
    update_fields = ["status", "updated_at"]
    _reuse_quick_client_profile(consultation, update_fields)
    consultation.status = quick_payment_entry_status(consultation)
    consultation.save(update_fields=tuple(dict.fromkeys(update_fields)))
    return consultation


def accept_quick_offer(chat_id, message):
    consultation = (
        pending_quick_offer(chat_id)
        or pending_quick_privacy(chat_id)
        or pending_quick_email(chat_id)
        or pending_quick_email_confirm(chat_id)
        or start_quick_entry(chat_id, message)
    )
    update_fields = ["status", "offer_accepted_at", "updated_at"]
    _apply_telegram_identity(consultation, message, update_fields)
    consultation.offer_accepted_at = consultation.offer_accepted_at or timezone.now()
    consultation.status = quick_legal_entry_status(consultation)
    consultation.save(update_fields=tuple(dict.fromkeys(update_fields)))
    record_legal_acceptance(
        document_type=ConsentAcceptance.DocumentType.OFFER,
        subject=consultation,
        channel=ConsentAcceptance.Channel.TELEGRAM,
        scope=ConsentAcceptance.DocumentScope.FAST,
        accepted_at=consultation.offer_accepted_at,
    )
    return consultation


def back_to_quick_offer(chat_id, message):
    consultation = (
        pending_quick_privacy(chat_id) or pending_quick_email(chat_id) or pending_quick_email_confirm(chat_id)
    )
    if not consultation:
        consultation = start_quick_payment(chat_id, message)
    consultation.status = QuickConsultation.Status.WAITING_OFFER
    consultation.save(update_fields=("status", "updated_at"))
    return consultation


def accept_quick_privacy(chat_id, message):
    consultation = (
        pending_quick_privacy(chat_id)
        or pending_quick_email(chat_id)
        or pending_quick_email_confirm(chat_id)
        or accept_quick_offer(chat_id, message)
    )
    update_fields = ["status", "privacy_accepted_at", "updated_at"]
    _apply_telegram_identity(consultation, message, update_fields)
    consultation.privacy_accepted_at = consultation.privacy_accepted_at or timezone.now()
    consultation.status = quick_legal_entry_status(consultation)
    consultation.save(update_fields=tuple(dict.fromkeys(update_fields)))
    record_legal_acceptance(
        document_type=ConsentAcceptance.DocumentType.PRIVACY,
        subject=consultation,
        channel=ConsentAcceptance.Channel.TELEGRAM,
        scope=ConsentAcceptance.DocumentScope.FAST,
        accepted_at=consultation.privacy_accepted_at,
    )
    return consultation


def back_to_quick_privacy(chat_id, message):
    consultation = pending_quick_email(chat_id) or pending_quick_email_confirm(chat_id)
    if not consultation:
        consultation = accept_quick_offer(chat_id, message)
    consultation.status = QuickConsultation.Status.WAITING_PRIVACY
    consultation.save(update_fields=("status", "updated_at"))
    return consultation


def set_quick_email(chat_id, message, email):
    consultation = pending_quick_email(chat_id) or accept_quick_privacy(chat_id, message)
    identity = telegram_identity(chat_id, message)
    update_fields = ["client_email", "status", "updated_at"]
    for field, value in identity.items():
        if value:
            setattr(consultation, field, value)
            update_fields.append(field)
    consultation.client_email = str(email or "").strip().lower()
    consultation.status = QuickConsultation.Status.WAITING_EMAIL_CONFIRM
    consultation.save(update_fields=tuple(dict.fromkeys(update_fields)))
    return consultation


def confirm_quick_email(chat_id, message):
    consultation = pending_quick_email_confirm(chat_id)
    if not consultation:
        return restart_quick_email(chat_id, message)
    if not consultation.client_email:
        consultation.status = QuickConsultation.Status.WAITING_EMAIL
        consultation.save(update_fields=("status", "updated_at"))
        return consultation
    consultation.status = quick_legal_entry_status(consultation)
    consultation.save(update_fields=("status", "updated_at"))
    return consultation


def restart_quick_email(chat_id, message):
    consultation = (
        pending_quick_email_confirm(chat_id) or pending_quick_email(chat_id) or accept_quick_privacy(chat_id, message)
    )
    consultation.status = QuickConsultation.Status.WAITING_EMAIL
    consultation.save(update_fields=("status", "updated_at"))
    return consultation


def start_quick_email_update(chat_id, message):
    consultation = (
        pending_quick_email_confirm(chat_id)
        or pending_quick_email(chat_id)
        or latest_active_quick_consultation(chat_id)
    )
    identity = telegram_identity(chat_id, message)
    if consultation:
        update_fields = ["status", "updated_at"]
        _apply_telegram_identity(consultation, message, update_fields)
        if not consultation.pending_action:
            consultation.pending_action = QuickConsultation.PendingAction.MENU
            update_fields.append("pending_action")
        consultation.status = QuickConsultation.Status.WAITING_EMAIL
        consultation.save(update_fields=tuple(dict.fromkeys(update_fields)))
        return consultation
    return QuickConsultation.objects.create(
        **identity,
        question="Обновление email для уведомлений.",
        status=QuickConsultation.Status.WAITING_EMAIL,
        pending_action=QuickConsultation.PendingAction.MENU,
    )


def parse_quick_payment_amount(text):
    normalized = re.sub(r"[^\d,.]", "", str(text or "")).replace(",", ".")
    try:
        amount = Decimal(normalized).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    if amount < MIN_QUICK_PAYMENT_AMOUNT or amount > MAX_QUICK_PAYMENT_AMOUNT:
        return None
    if amount != amount.quantize(Decimal("1")):
        return None
    return amount


def successful_quick_payments(chat_id, limit=5):
    return list(
        QuickConsultation.objects.filter(
            telegram_chat_id=str(chat_id),
            invoice__payments__status=Payment.Status.SUCCEEDED,
        )
        .select_related("invoice")
        .prefetch_related("invoice__payments")
        .order_by("-invoice__payments__paid_at", "-updated_at")[:limit]
    )


def create_quick_payment_from_fixed_amount(chat_id, message):
    consultation = (
        pending_quick_payment(chat_id)
        or latest_active_quick_consultation(chat_id)
        or start_quick_payment(chat_id, message)
    )
    consultation.status = QuickConsultation.Status.WAITING_PAYMENT_AMOUNT
    consultation.pending_action = QuickConsultation.PendingAction.PAYMENT
    consultation.save(update_fields=("status", "pending_action", "updated_at"))
    return create_quick_payment_from_client_amount(chat_id, message, FIXED_QUICK_CONSULTATION_AMOUNT)


def create_quick_payment_from_client_amount(chat_id, message, amount):
    consultation = pending_quick_payment(chat_id)
    if not consultation:
        consultation = start_quick_payment(chat_id, message)
        return consultation, None, ""
    identity = telegram_identity(chat_id, message)
    update_fields = ["updated_at"]
    for field, value in identity.items():
        if value:
            setattr(consultation, field, value)
            update_fields.append(field)
    if not consultation.question:
        consultation.question = "Оплата быстрой консультации."
        update_fields.append("question")
    if update_fields:
        consultation.save(update_fields=tuple(dict.fromkeys(update_fields)))
    item_name = quick_payment_item_name_for_amount(amount)
    invoice, payment_url = create_quick_consultation_invoice(
        consultation,
        amount,
        manager_response=item_name,
        internal_note="Сумма введена клиентом в Telegram-боте AurumWeb Fast.",
    )
    IntegrationEvent.objects.create(
        provider=IntegrationEvent.Provider.TELEGRAM,
        status=IntegrationEvent.Status.SUCCESS,
        title="Клиент сформировал счет в AurumWeb Fast",
        payload={
            "quick_consultation_id": consultation.pk,
            "invoice_id": invoice.pk,
            "telegram_chat_id": consultation.telegram_chat_id,
            "amount": str(amount),
            "item_name": item_name,
        },
    )
    return consultation, invoice, payment_url


def submit_quick_consultation_question(chat_id, message, question):
    consultation = pending_quick_consultation(chat_id) or start_quick_consultation(chat_id, message)
    identity = telegram_identity(chat_id, message)
    for field, value in identity.items():
        if value:
            setattr(consultation, field, value)
    consultation.question = str(question or "").strip()
    consultation.status = QuickConsultation.Status.NEW
    consultation.save(
        update_fields=(
            "telegram_user_id",
            "telegram_username",
            "first_name",
            "last_name",
            "client_name",
            "question",
            "status",
            "updated_at",
        )
    )
    IntegrationEvent.objects.create(
        provider=IntegrationEvent.Provider.TELEGRAM,
        status=IntegrationEvent.Status.SUCCESS,
        title="Создана быстрая консультация",
        payload={"quick_consultation_id": consultation.pk, "telegram_chat_id": consultation.telegram_chat_id},
    )
    return consultation


def send_quick_consultation_reply(consultation, text):
    from apps.integrations.telegram import QuickConsultationBotClient

    message = str(text or "").strip()
    if not message:
        return False
    consultation.manager_response = message
    if consultation.status == QuickConsultation.Status.NEW:
        consultation.status = QuickConsultation.Status.IN_DISCUSSION
    consultation.save(update_fields=("manager_response", "status", "updated_at"))
    return QuickConsultationBotClient().send_quick_consultation_reply(consultation, message)


@transaction.atomic
def create_quick_consultation_invoice(consultation, amount, manager_response="", internal_note=""):
    if consultation.project_id is None:
        ensure_project_for_instance(consultation)
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    item_name = quick_payment_item_name_for_amount(amount)
    response = str(manager_response or consultation.manager_response or "").strip()
    note = str(internal_note or "").strip()
    description = response or consultation.question or item_name
    title = item_name

    pending_payment = None
    if consultation.invoice_id:
        invoice = Invoice.objects.select_for_update().get(pk=consultation.invoice_id)
        if invoice.status == Invoice.Status.PAID:
            detail_url = _absolute_url(reverse("billing:invoice-detail", kwargs={"token": invoice.public_token}))
            return invoice, detail_url
        invoice.title = title
        invoice.description = description
        invoice.amount = amount
        invoice.client_name = consultation.display_name
        invoice.client_email = consultation.client_email
        invoice.client_requisites = {
            **(invoice.client_requisites or {}),
            "client_type": "telegram",
            "client_type_display": "Telegram",
            "telegram_chat_id": consultation.telegram_chat_id,
            "telegram_username": consultation.telegram_username,
            "client_email": consultation.client_email,
            "item_name": item_name,
            "offer_accepted_at": consultation.offer_accepted_at.isoformat() if consultation.offer_accepted_at else "",
            "privacy_accepted_at": (
                consultation.privacy_accepted_at.isoformat() if consultation.privacy_accepted_at else ""
            ),
        }
        invoice.project = consultation.project
        invoice.save(
            update_fields=(
                "project",
                "title",
                "description",
                "amount",
                "client_name",
                "client_email",
                "client_requisites",
                "updated_at",
            )
        )
        pending_payment = (
            invoice.payments.filter(provider=Payment.Provider.ROBOKASSA, status=Payment.Status.PENDING)
            .order_by("-created_at")
            .first()
        )
        if pending_payment:
            invoice.payments.filter(provider=Payment.Provider.ROBOKASSA, status=Payment.Status.PENDING).exclude(
                pk=pending_payment.pk
            ).update(status=Payment.Status.CANCELLED)
            pending_payment.amount = amount
            pending_payment.signature_valid = False
            pending_payment.provider_payload = {}
            pending_payment.save(update_fields=("amount", "signature_valid", "provider_payload"))
        invoice.items.all().delete()
    else:
        invoice = Invoice.objects.create(
            project=consultation.project,
            client_name=consultation.display_name,
            client_email=consultation.client_email,
            client_requisites={
                "client_type": "telegram",
                "client_type_display": "Telegram",
                "telegram_chat_id": consultation.telegram_chat_id,
                "telegram_username": consultation.telegram_username,
                "client_email": consultation.client_email,
                "item_name": item_name,
                "offer_accepted_at": (
                    consultation.offer_accepted_at.isoformat() if consultation.offer_accepted_at else ""
                ),
                "privacy_accepted_at": (
                    consultation.privacy_accepted_at.isoformat() if consultation.privacy_accepted_at else ""
                ),
            },
            title=title,
            description=description,
            amount=amount,
            status=Invoice.Status.DRAFT,
        )
        consultation.invoice = invoice

    InvoiceItem.objects.create(
        invoice=invoice,
        name=item_name,
        note="",
        quantity=1,
        unit_price=amount,
    )
    consultation.quoted_amount = amount
    consultation.manager_response = response
    consultation.internal_note = note or consultation.internal_note
    consultation.status = QuickConsultation.Status.INVOICED
    consultation.save(
        update_fields=("invoice", "quoted_amount", "manager_response", "internal_note", "status", "updated_at")
    )
    issue_invoice(invoice)
    if pending_payment is None:
        create_robokassa_payment(invoice, reuse_pending=True)
    return invoice, _invoice_payment_url(invoice)
