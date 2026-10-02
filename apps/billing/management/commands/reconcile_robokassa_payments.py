import json

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.billing.reconciliation import reconcile_pending_payments


class Command(BaseCommand):
    help = "Сверяет ожидающие платежи с состоянием операций Robokassa."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-minutes",
            type=int,
            default=settings.AURUMWEB_ROBOKASSA_RECONCILE_AFTER_MINUTES,
        )
        parser.add_argument("--limit", type=int, default=settings.AURUMWEB_ROBOKASSA_RECONCILE_LIMIT)
        parser.add_argument("--payment-id", type=int)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        summary = reconcile_pending_payments(
            older_than_minutes=options["older_than_minutes"],
            limit=options["limit"],
            payment_id=options["payment_id"],
            dry_run=options["dry_run"],
        )
        self.stdout.write(json.dumps(summary.as_dict(), ensure_ascii=False, sort_keys=True))
