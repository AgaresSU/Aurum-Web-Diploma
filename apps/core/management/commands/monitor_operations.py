import json

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import OperationalAlert
from apps.core.monitoring import collect_operational_conditions, synchronize_operational_alerts
from apps.projects.reminders import enqueue_deadline_reminders


class Command(BaseCommand):
    help = "Проверяет эксплуатационные риски и отправляет дедуплицированные уведомления."

    def add_arguments(self, parser):
        parser.add_argument("--no-notify", action="store_true")
        parser.add_argument("--fail-on-error", action="store_true")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        conditions = collect_operational_conditions()
        result = synchronize_operational_alerts(conditions, notify=not options["no_notify"])
        reminder_result = {"candidates": 0, "tasks": 0}
        if not options["no_notify"]:
            reminder_result = enqueue_deadline_reminders()
        errors = sum(1 for condition in conditions if condition["severity"] == OperationalAlert.Severity.ERROR)
        warnings = len(conditions) - errors
        result.update(
            {
                "errors": errors,
                "warnings": warnings,
                "deadline_candidates": reminder_result["candidates"],
                "deadline_tasks": reminder_result["tasks"],
            }
        )

        if options["as_json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Контроль завершен: активных {result['active']}, критических {errors}, "
                    f"предупреждений {warnings}, уведомлений {result['notified']}, "
                    f"задач по срокам {result['deadline_tasks']}."
                )
            )
        if options["fail_on_error"] and errors:
            raise CommandError(f"Обнаружено критических эксплуатационных проблем: {errors}")
