from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.integrations.models import IntegrationEvent


class Command(BaseCommand):
    help = "Archive old integration errors so resolved incidents do not keep production readiness yellow."

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            choices=[choice[0] for choice in IntegrationEvent.Provider.choices],
            default=IntegrationEvent.Provider.TELEGRAM,
            help="Provider to archive. Defaults to telegram.",
        )
        parser.add_argument(
            "--older-than-hours",
            type=int,
            default=24,
            help="Archive errors older than this amount of hours.",
        )
        parser.add_argument(
            "--status",
            choices=(IntegrationEvent.Status.INFO, IntegrationEvent.Status.WARNING),
            default=IntegrationEvent.Status.INFO,
            help="Status to assign to archived errors.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many records would be archived without changing them.",
        )

    def handle(self, *args, **options):
        older_than_hours = options["older_than_hours"]
        if older_than_hours < 0:
            raise CommandError("--older-than-hours must be greater than or equal to 0.")

        cutoff = timezone.now() - timedelta(hours=older_than_hours)
        queryset = IntegrationEvent.objects.filter(
            provider=options["provider"],
            status=IntegrationEvent.Status.ERROR,
            created_at__lt=cutoff,
        )
        count = queryset.count()
        if options["dry_run"]:
            self.stdout.write(f"Would archive {count} integration error(s).")
            return

        updated = 0
        for event in queryset.iterator():
            if not event.title.startswith("Архив: "):
                event.title = f"Архив: {event.title}"[:220]
            payload = dict(event.payload or {})
            payload["archived_at"] = timezone.now().isoformat()
            payload["archived_from_status"] = IntegrationEvent.Status.ERROR
            event.payload = payload
            event.status = options["status"]
            event.save(update_fields=("title", "payload", "status"))
            updated += 1

        self.stdout.write(f"Archived {updated} integration error(s).")
