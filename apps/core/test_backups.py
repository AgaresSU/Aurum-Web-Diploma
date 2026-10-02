import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.prod_readiness import _backup_readiness
from apps.integrations.models import IntegrationEvent


class BackupStatusCommandTests(TestCase):
    @override_settings(EMAIL_NOTIFICATIONS_ENABLED=False)
    def test_success_is_written_to_integration_journal(self):
        call_command(
            "record_backup_status",
            operation="backup",
            status="success",
            identifier="20260722-033000",
            remote_uri="s3://private/daily/backup.age",
            checksum="abc123",
            stdout=StringIO(),
        )

        event = IntegrationEvent.objects.get(title="Резервное копирование завершено")
        self.assertEqual(event.status, IntegrationEvent.Status.SUCCESS)
        self.assertEqual(event.payload["checksum"], "abc123")

    @override_settings(EMAIL_NOTIFICATIONS_ENABLED=False, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="")
    def test_failure_is_written_and_command_fails(self):
        with self.assertRaises(CommandError):
            call_command(
                "record_backup_status",
                operation="restore",
                status="error",
                identifier="broken-backup",
                message="checksum mismatch",
                stdout=StringIO(),
                stderr=StringIO(),
            )

        event = IntegrationEvent.objects.get(title="Ошибка тестового восстановления")
        self.assertEqual(event.status, IntegrationEvent.Status.ERROR)
        self.assertEqual(event.payload["message"], "checksum mismatch")


class BackupReadinessTests(TestCase):
    @override_settings(
        AURUMWEB_BACKUP_S3_URI="s3://private-aurumweb",
        AURUMWEB_BACKUP_AGE_RECIPIENT="age1example",
        AURUMWEB_BACKUP_RPO_HOURS=24,
    )
    def test_external_backup_and_restore_are_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            backup = Path(temp_dir) / "encrypted" / "aurumweb-test.tar.gz.age"
            backup.parent.mkdir()
            backup.touch()
            IntegrationEvent.objects.create(
                provider=IntegrationEvent.Provider.SYSTEM,
                status=IntegrationEvent.Status.SUCCESS,
                title="Резервное копирование завершено",
            )
            IntegrationEvent.objects.create(
                provider=IntegrationEvent.Provider.SYSTEM,
                status=IntegrationEvent.Status.SUCCESS,
                title="Тестовое восстановление завершено",
                created_at=timezone.now(),
            )
            with patch.dict("os.environ", {"AURUMWEB_BACKUP_DIR": temp_dir}):
                items = _backup_readiness()

        states = {item["title"]: item["state"] for item in items}
        self.assertEqual(states["Резервное копирование"], "ok")
        self.assertEqual(states["Свежий бэкап"], "ok")
        self.assertEqual(states["Внешняя зашифрованная копия"], "ok")
        self.assertEqual(states["Тестовое восстановление"], "ok")

    @override_settings(
        AURUMWEB_BACKUP_S3_URI="",
        AURUMWEB_BACKUP_RCLONE_REMOTE="aurum_yandex",
        AURUMWEB_BACKUP_RCLONE_PATH="Бэкапы/AurumWeb Backups",
        AURUMWEB_BACKUP_AGE_RECIPIENT="age1example",
    )
    def test_rclone_destination_is_ready_for_external_backup(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.dict("os.environ", {"AURUMWEB_BACKUP_DIR": temp_dir}),
        ):
            items = _backup_readiness()

        states = {item["title"]: item["state"] for item in items}
        self.assertEqual(states["Внешняя зашифрованная копия"], "ok")
