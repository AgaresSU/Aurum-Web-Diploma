import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.monitoring import record_outbox_worker_heartbeat
from apps.integrations.outbox import process_available_outbound_tasks


class Command(BaseCommand):
    help = "Обрабатывает надежную очередь исходящих уведомлений AurumWeb."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Обработать доступные задания и завершить работу.")
        parser.add_argument("--limit", type=int, default=settings.AURUMWEB_OUTBOX_BATCH_SIZE)
        parser.add_argument("--sleep", type=float, default=settings.AURUMWEB_OUTBOX_POLL_SECONDS)

    def handle(self, *args, **options):
        limit = max(1, options["limit"])
        sleep_seconds = max(0.1, options["sleep"])
        while True:
            record_outbox_worker_heartbeat()
            processed = process_available_outbound_tasks(limit=limit)
            if options["once"]:
                self.stdout.write(self.style.SUCCESS(f"Обработано исходящих операций: {processed}"))
                return
            if processed == 0:
                time.sleep(sleep_seconds)
