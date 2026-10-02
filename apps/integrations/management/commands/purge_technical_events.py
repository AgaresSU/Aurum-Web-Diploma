from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import models, transaction
from django.utils import timezone

from apps.core.redaction import sanitize_log_data
from apps.integrations.models import IntegrationEvent, OutboundTask


class Command(BaseCommand):
    help = "Обезличивает старые технические события и payload завершенных исходящих операций."

    def add_arguments(self, parser):
        parser.add_argument(
            "--event-days",
            type=int,
            default=settings.AURUMWEB_INTEGRATION_EVENT_RETENTION_DAYS,
        )
        parser.add_argument(
            "--outbox-days",
            type=int,
            default=settings.AURUMWEB_OUTBOX_PAYLOAD_RETENTION_DAYS,
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if options["event_days"] < 1 or options["outbox_days"] < 1:
            raise ValueError("Срок хранения должен быть не меньше одного дня.")

        event_cutoff = timezone.now() - timedelta(days=options["event_days"])
        outbox_cutoff = timezone.now() - timedelta(days=options["outbox_days"])
        technical_events = IntegrationEvent.objects.filter(
            provider__in=(IntegrationEvent.Provider.TELEGRAM, IntegrationEvent.Provider.ROBOKASSA)
        )
        old_events = technical_events.filter(created_at__lt=event_cutoff).exclude(payload={})
        old_tasks = OutboundTask.objects.filter(
            status__in=(OutboundTask.Status.SUCCEEDED, OutboundTask.Status.FAILED),
            updated_at__lt=outbox_cutoff,
        ).exclude(payload={})

        old_event_count = old_events.count()
        old_task_count = old_tasks.count()
        sanitized_count = 0
        expired_payloads = models.Q(payload_expires_at__isnull=True) | models.Q(payload_expires_at__lte=timezone.now())
        for event in technical_events.filter(created_at__gte=event_cutoff).filter(expired_payloads).exclude(payload={}):
            sanitized = sanitize_log_data(event.payload)
            if sanitized != event.payload:
                sanitized_count += 1
                if not options["dry_run"]:
                    IntegrationEvent.objects.filter(pk=event.pk).update(payload=sanitized, payload_expires_at=None)

        if not options["dry_run"]:
            with transaction.atomic():
                old_events.update(payload={})
                old_tasks.update(payload={}, last_error="")

        mode = "Проверка" if options["dry_run"] else "Готово"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: очищено событий {old_event_count}, операций {old_task_count}, "
                f"обезличено свежих событий {sanitized_count}."
            )
        )
