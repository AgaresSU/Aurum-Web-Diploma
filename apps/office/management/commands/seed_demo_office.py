from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.billing.models import Invoice, Order, Payment
from apps.leads.models import Lead
from apps.messaging.models import Conversation, Message


class Command(BaseCommand):
    help = "Create demo leads, conversations, orders and invoices for AurumWeb Office."

    def handle(self, *args, **options):
        lead, _created = Lead.objects.update_or_create(
            email="client@example.test",
            subject="Сайт, CRM и прием оплат",
            defaults={
                "name": "Алексей, B2B-консалтинг",
                "service_type": "Бизнес-интеграции",
                "task": (
                    "Нужен премиальный сайт для услуги, форма брифа, кабинет заявок, "
                    "встроенный мессенджер, выставление счетов и подготовка Robokassa."
                ),
                "source": Lead.Source.FORM,
                "status": Lead.Status.QUALIFYING,
            },
        )

        conversation, _created = Conversation.objects.get_or_create(
            lead=lead,
            defaults={
                "client_email": lead.email,
                "title": "Разбор сайта и CRM",
                "status": Conversation.Status.WAITING_MANAGER,
            },
        )
        if not conversation.messages.exists():
            Message.objects.create(
                conversation=conversation,
                author_role=Message.AuthorRole.CLIENT,
                body="Хочу понять бюджет и этапы: сайт, SEO, заявки, оплата.",
            )
            Message.objects.create(
                conversation=conversation,
                author_role=Message.AuthorRole.MANAGER,
                body="Начнем с карты процессов: от заявки до счета и оплаты. После этого соберу вилку стоимости.",
            )

        invoice, _created = Invoice.objects.update_or_create(
            lead=lead,
            title="Диагностика и проектирование бизнес-системы",
            defaults={
                "client_name": lead.name,
                "client_email": lead.email,
                "description": "Разбор задачи, архитектура сайта, CRM, оплат и интеграций.",
                "amount": "35000.00",
                "status": Invoice.Status.ISSUED,
                "due_date": timezone.localdate() + timezone.timedelta(days=7),
            },
        )

        Order.objects.update_or_create(
            lead=lead,
            defaults={
                "invoice": invoice,
                "title": "AurumWeb: сайт, CRM и интеграции",
                "client_name": lead.name,
                "client_email": lead.email,
                "status": Order.Status.PROPOSAL,
                "scope": lead.task,
                "estimated_amount_min": "120000.00",
                "estimated_amount_max": "280000.00",
            },
        )

        if not invoice.payments.exists():
            Payment.objects.create(
                invoice=invoice,
                provider=Payment.Provider.ROBOKASSA,
                status=Payment.Status.PENDING,
                amount=invoice.amount,
            )

        self.stdout.write(self.style.SUCCESS("Demo office workflow is ready."))
