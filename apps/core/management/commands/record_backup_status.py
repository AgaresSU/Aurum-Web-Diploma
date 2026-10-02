from django.core.management.base import BaseCommand, CommandError

from apps.integrations.email import EmailNotificationClient
from apps.integrations.models import IntegrationEvent
from apps.integrations.telegram import TelegramBotClient


class Command(BaseCommand):
    help = "Записывает результат резервного копирования или тестового восстановления."

    def add_arguments(self, parser):
        parser.add_argument("--operation", choices=("backup", "restore"), required=True)
        parser.add_argument("--status", choices=("success", "error"), required=True)
        parser.add_argument("--identifier", required=True)
        parser.add_argument("--local-path", default="")
        parser.add_argument("--remote-uri", default="")
        parser.add_argument("--checksum", default="")
        parser.add_argument("--message", default="")

    def handle(self, *args, **options):
        operation = options["operation"]
        failed = options["status"] == "error"
        title = {
            ("backup", False): "Резервное копирование завершено",
            ("backup", True): "Ошибка резервного копирования",
            ("restore", False): "Тестовое восстановление завершено",
            ("restore", True): "Ошибка тестового восстановления",
        }[(operation, failed)]
        payload = {
            "operation": operation,
            "identifier": options["identifier"][:120],
            "local_path": options["local_path"][:500],
            "remote_uri": options["remote_uri"][:500],
            "checksum": options["checksum"][:128],
            "message": options["message"][:1000],
        }
        IntegrationEvent.objects.create(
            provider=IntegrationEvent.Provider.SYSTEM,
            status=IntegrationEvent.Status.ERROR if failed else IntegrationEvent.Status.SUCCESS,
            title=title,
            payload=payload,
        )
        if failed:
            message = options["message"] or title
            EmailNotificationClient().notify_system_alert(title, message, payload)
            telegram = TelegramBotClient()
            if telegram.notifications_enabled:
                telegram.send_message(f"{title}\n{message}")
            raise CommandError(title)
        self.stdout.write(self.style.SUCCESS(title))
