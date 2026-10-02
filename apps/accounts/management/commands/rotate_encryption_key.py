from cryptography.fernet import InvalidToken
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.crypto import rotate_encrypted_value
from apps.accounts.models import Profile
from apps.integrations.models import QuickConsultationBotSettings, RobokassaSettings, TelegramBotSettings


class Command(BaseCommand):
    help = "Повторно шифрует сохраненные секреты основным ключом из текущего окружения."

    def handle(self, *args, **options):
        models_and_fields = (
            (Profile, ("totp_secret",)),
            (TelegramBotSettings, ("bot_token", "webhook_secret")),
            (QuickConsultationBotSettings, ("bot_token", "webhook_secret")),
            (RobokassaSettings, ("password1", "password2")),
        )
        changed = 0
        try:
            with transaction.atomic():
                for model, fields in models_and_fields:
                    for instance in model.objects.select_for_update().all():
                        update_fields = []
                        for field in fields:
                            value = getattr(instance, field)
                            if not value:
                                continue
                            rotated = rotate_encrypted_value(value)
                            if rotated != value:
                                setattr(instance, field, rotated)
                                update_fields.append(field)
                        if update_fields:
                            instance.save(update_fields=update_fields)
                            changed += len(update_fields)
        except (InvalidToken, ValueError) as exc:
            raise CommandError(
                "Ротация отменена: один из секретов не удалось расшифровать. " "Проверьте основной и предыдущие ключи."
            ) from exc
        self.stdout.write(self.style.SUCCESS(f"Секреты повторно зашифрованы: {changed}."))
