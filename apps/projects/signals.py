from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.billing.models import Invoice, ManagedSite, Order
from apps.leads.models import Lead, QuickConsultation
from apps.messaging.models import Conversation, Message

from .models import ProjectEvent
from .services import ensure_project_for_instance


@receiver(post_save, sender=Lead)
@receiver(post_save, sender=Conversation)
@receiver(post_save, sender=Invoice)
@receiver(post_save, sender=Order)
@receiver(post_save, sender=ManagedSite)
def attach_project(sender, instance, created, raw=False, **kwargs):
    if raw or instance.project_id:
        return
    ensure_project_for_instance(instance)


@receiver(post_save, sender=QuickConsultation)
def attach_quick_consultation_project(sender, instance, raw=False, **kwargs):
    if raw or instance.project_id:
        return
    meaningful_statuses = (
        QuickConsultation.Status.NEW,
        QuickConsultation.Status.IN_DISCUSSION,
        QuickConsultation.Status.INVOICED,
        QuickConsultation.Status.PAID,
    )
    if instance.invoice_id or instance.status in meaningful_statuses:
        ensure_project_for_instance(instance)


@receiver(post_save, sender=Message)
def record_message_event(sender, instance, created, raw=False, **kwargs):
    if raw or not created:
        return
    conversation = instance.conversation
    if conversation.project_id is None:
        ensure_project_for_instance(conversation)
    ProjectEvent.objects.create(
        project=conversation.project,
        actor=instance.author,
        kind=ProjectEvent.Kind.MESSAGE,
        title="Новое сообщение клиента" if instance.author_role == Message.AuthorRole.CLIENT else "Ответ менеджера",
        client_visible=True,
        manager_seen_at=None if instance.author_role == Message.AuthorRole.CLIENT else timezone.now(),
    )
