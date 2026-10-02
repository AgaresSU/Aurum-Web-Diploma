from django.utils import timezone

from apps.billing.models import InvoiceItem, ManagedSite, Order
from apps.billing.services import create_invoice_from_lead
from apps.integrations.telegram import QuickConsultationBotClient, TelegramBotClient
from apps.leads.models import Lead
from apps.leads.services import create_quick_consultation_invoice, send_quick_consultation_reply
from apps.messaging.models import Conversation, Message
from apps.projects.models import Project
from apps.projects.services import update_project_status

from .view_support import build_proposal_draft, lead_metadata, qualification_payload

SITE_UPDATE_LABELS = {
    "status": "статус",
    "url": "адрес сайта",
    "domain_name": "домен",
    "domain_expires_at": "оплата домена",
    "hosting_provider": "хостинг",
    "hosting_expires_at": "оплата хостинга",
    "ssl_expires_at": "SSL",
    "support_until": "поддержка",
}


def save_lead_qualification(lead, cleaned_data):
    metadata = lead_metadata(lead)
    metadata["qualification"] = qualification_payload(cleaned_data)
    lead.metadata = metadata

    update_fields = ["metadata", "updated_at"]
    if lead.status == Lead.Status.NEW:
        lead.status = Lead.Status.QUALIFYING
        update_fields.append("status")

    lead.save(update_fields=tuple(update_fields))
    return lead


def store_proposal_draft(lead, qualification, selected_template, user):
    metadata = lead_metadata(lead)
    draft = build_proposal_draft(lead, qualification, selected_template, user)
    metadata["proposal_draft"] = draft
    lead.metadata = metadata
    lead.status = Lead.Status.PROPOSAL
    lead.save(update_fields=("metadata", "status", "updated_at"))
    return draft


def close_lead(lead):
    if lead.status != Lead.Status.LOST:
        lead.status = Lead.Status.LOST
        lead.save(update_fields=("status", "updated_at"))
    return lead


def close_conversation(conversation):
    if conversation.status != Conversation.Status.CLOSED:
        conversation.status = Conversation.Status.CLOSED
        conversation.save(update_fields=("status", "updated_at"))
    return conversation


def create_invoice_draft_for_lead(lead, cleaned_data):
    invoice = create_invoice_from_lead(lead, amount=cleaned_data["amount"])
    invoice.title = cleaned_data["title"]
    invoice.description = cleaned_data["description"]
    invoice.due_date = cleaned_data["due_date"]
    invoice.save(update_fields=("title", "description", "due_date", "updated_at"))
    InvoiceItem.objects.create(
        invoice=invoice,
        name=cleaned_data.get("item_name") or invoice.title,
        note=cleaned_data.get("item_note", ""),
        quantity=1,
        unit_price=invoice.amount,
    )

    lead.status = Lead.Status.PROPOSAL
    lead.save(update_fields=("status", "updated_at"))
    return invoice


def add_manager_reply(conversation, author, body):
    message = Message.objects.create(
        conversation=conversation,
        author=author,
        author_role=Message.AuthorRole.MANAGER,
        body=body,
    )
    conversation.status = Conversation.Status.WAITING_CLIENT
    conversation.manager_last_read_at = timezone.now()
    conversation.save(update_fields=("status", "manager_last_read_at", "updated_at"))
    TelegramBotClient().notify_client_manager_message(conversation, message)
    return message


def send_client_telegram_test(profile):
    return TelegramBotClient().notify_client_test(profile)


def refresh_client_telegram_code(profile):
    profile.refresh_telegram_link_code()
    profile.save(update_fields=("telegram_link_code", "telegram_link_code_created_at"))
    return profile


def disconnect_client_telegram(profile):
    profile.telegram_chat_id = ""
    profile.telegram_notifications_enabled = False
    profile.refresh_telegram_link_code()
    profile.save(
        update_fields=(
            "telegram_chat_id",
            "telegram_notifications_enabled",
            "telegram_link_code",
            "telegram_link_code_created_at",
        )
    )
    return profile


def save_order_with_notifications(form, previous_status):
    order = form.save()
    if previous_status != order.status:
        project_statuses = {
            Order.Status.DISCOVERY: Project.Status.DISCOVERY,
            Order.Status.PROPOSAL: Project.Status.DISCOVERY,
            Order.Status.WAITING_PAYMENT: Project.Status.WAITING_PAYMENT,
            Order.Status.IN_PROGRESS: Project.Status.IN_PROGRESS,
            Order.Status.COMPLETED: Project.Status.COMPLETED,
            Order.Status.CANCELLED: Project.Status.CANCELLED,
        }
        update_project_status(
            order.project,
            project_statuses.get(order.status, Project.Status.DRAFT),
            title=f"Заказ: {order.get_status_display()}",
        )
        TelegramBotClient().notify_client_order_updated(order, previous_status=previous_status)
    return order


def complete_order(order):
    previous_status = order.status
    if previous_status != Order.Status.COMPLETED:
        order.status = Order.Status.COMPLETED
        order.save(update_fields=("status", "updated_at"))
        update_project_status(order.project, Project.Status.COMPLETED, title="Проект завершен")
        TelegramBotClient().notify_client_order_updated(order, previous_status=previous_status)
    return order


def create_managed_site_from_order(order):
    site = order.managed_sites.first()
    if site is not None:
        return site, False

    site = ManagedSite.objects.create(
        project=order.project,
        user=order.user,
        lead=order.lead,
        order=order,
        title=order.title,
        status=ManagedSite.Status.PLANNING,
        support_plan=ManagedSite.SupportPlan.BASIC,
        notes="Карточка создана из заказа. Заполните домен, хостинг, SSL, поддержку и контрольные даты.",
    )
    TelegramBotClient().notify_client_site_created(site)
    return site, True


def managed_site_update_snapshot(site):
    return {
        "status": site.status,
        "values": {field: getattr(site, field) for field in SITE_UPDATE_LABELS},
    }


def save_managed_site_with_notifications(form, snapshot):
    site = form.save()
    previous_status = snapshot["status"]
    previous_values = snapshot["values"]
    changed_fields = [
        label for field, label in SITE_UPDATE_LABELS.items() if previous_values.get(field) != getattr(site, field)
    ]
    if changed_fields:
        project_status = (
            Project.Status.SUPPORT
            if site.status in (ManagedSite.Status.LIVE, ManagedSite.Status.MAINTENANCE)
            else Project.Status.IN_PROGRESS
        )
        update_project_status(
            site.project,
            project_status,
            title=f"Сайт: {site.get_status_display()}",
            description=", ".join(changed_fields),
        )
        TelegramBotClient().notify_client_site_updated(
            site,
            changed_fields=changed_fields,
            previous_status=previous_status,
        )
    return site, changed_fields


def send_quick_consultation_message(consultation, message):
    return send_quick_consultation_reply(consultation, message)


def invoice_quick_consultation(consultation, cleaned_data):
    invoice, payment_url = create_quick_consultation_invoice(
        consultation,
        cleaned_data["quoted_amount"],
        manager_response=cleaned_data.get("manager_response", ""),
        internal_note=cleaned_data.get("internal_note", ""),
    )
    QuickConsultationBotClient().send_quick_consultation_payment_link(consultation, invoice, payment_url)
    return invoice, payment_url
