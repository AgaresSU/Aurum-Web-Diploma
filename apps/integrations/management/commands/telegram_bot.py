import json

from django.core.management.base import BaseCommand, CommandError

from apps.integrations.telegram import TelegramBotClient
from apps.integrations.telegram_handlers import process_pending_telegram_updates


class Command(BaseCommand):
    help = "Check and manage the AurumWeb Telegram bot integration."

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            choices=(
                "check",
                "send-test",
                "updates",
                "process-updates",
                "webhook-info",
                "set-webhook",
                "delete-webhook",
            ),
            help="Action to run.",
        )
        parser.add_argument("--chat-id", help="Target chat id for send-test.")
        parser.add_argument(
            "--text", default="AurumWeb: тестовое уведомление Telegram.", help="Message text for send-test."
        )
        parser.add_argument("--limit", type=int, default=10, help="Updates limit.")
        parser.add_argument("--webhook-url", help="Full webhook URL for set-webhook.")
        parser.add_argument("--secret", help="Webhook secret token for set-webhook.")

    def handle(self, *args, **options):
        client = TelegramBotClient()
        action = options["action"]

        if action == "check":
            result = client.get_me()
        elif action == "send-test":
            ok = client.send_message(options["text"], chat_id=options.get("chat_id"))
            result = {"ok": ok}
        elif action == "updates":
            result = client.get_updates(limit=options["limit"])
        elif action == "process-updates":
            result = process_pending_telegram_updates(limit=options["limit"])
        elif action == "webhook-info":
            result = client.get_webhook_info()
        elif action == "set-webhook":
            result = client.set_webhook(webhook_url=options.get("webhook_url"), secret=options.get("secret"))
        elif action == "delete-webhook":
            result = client.delete_webhook()
        else:
            raise CommandError(f"Unknown action: {action}")

        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
        if not result.get("ok"):
            raise CommandError("Telegram action failed.")
