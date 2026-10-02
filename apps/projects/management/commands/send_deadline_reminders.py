import json

from django.core.management.base import BaseCommand

from apps.projects.reminders import collect_deadline_reminders, enqueue_deadline_reminders


class Command(BaseCommand):
    help = "Ставит в очередь напоминания клиентам о сроках за 7, 3 и 1 день."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        if options["dry_run"]:
            reminders = collect_deadline_reminders()
            result = {
                "candidates": len(reminders),
                "items": [
                    {
                        "title": item["title"],
                        "deadline": item["deadline"].isoformat(),
                        "days_left": item["days_left"],
                        "email": bool(item["email"]),
                        "telegram": bool(item["user"] and getattr(item["user"], "profile", None)),
                    }
                    for item in reminders
                ],
            }
        else:
            result = enqueue_deadline_reminders()

        if options["as_json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Сроки проверены: найдено {result['candidates']}, задач в очереди {result.get('tasks', 0)}."
                )
            )
