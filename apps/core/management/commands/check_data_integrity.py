import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q

from apps.billing.models import Invoice, InvoiceItem, Order, Payment
from apps.content.models import Service
from apps.leads.models import QuickConsultation


def integrity_counts():
    return {
        "invoice_amount_nonpositive": Invoice.objects.filter(amount__lte=0).count(),
        "invoice_item_quantity_nonpositive": InvoiceItem.objects.filter(quantity__lte=0).count(),
        "invoice_item_price_nonpositive": InvoiceItem.objects.filter(unit_price__lte=0).count(),
        "payment_amount_nonpositive": Payment.objects.filter(amount__lte=0).count(),
        "order_min_negative": Order.objects.filter(estimated_amount_min__lt=0).count(),
        "order_max_negative": Order.objects.filter(estimated_amount_max__lt=0).count(),
        "order_estimate_range_invalid": Order.objects.filter(
            Q(estimated_amount_min__isnull=False),
            Q(estimated_amount_max__isnull=False),
            estimated_amount_max__lt=F("estimated_amount_min"),
        ).count(),
        "quick_consultation_quote_nonpositive": QuickConsultation.objects.filter(quoted_amount__lte=0).count(),
        "service_price_nonpositive": Service.objects.filter(price_amount__lte=0).count(),
    }


class Command(BaseCommand):
    help = "Проверяет исторические данные перед установкой ограничений целостности базы."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        counts = integrity_counts()
        invalid_total = sum(counts.values())
        result = {"ok": invalid_total == 0, "invalid_total": invalid_total, "checks": counts}

        if options["as_json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
        elif invalid_total == 0:
            self.stdout.write(self.style.SUCCESS("Данные совместимы с ограничениями целостности."))
        else:
            for name, count in counts.items():
                if count:
                    self.stdout.write(f"{name}: {count}")

        if invalid_total:
            raise CommandError(f"Найдено некорректных записей: {invalid_total}")
