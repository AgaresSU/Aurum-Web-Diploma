import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing.models import ManagedSite

from .logging_handlers import SafeErrorEmailHandler
from .models import OperationalAlert
from .monitoring import (
    collect_operational_conditions,
    outbox_worker_heartbeat_age,
    record_outbox_worker_heartbeat,
    synchronize_operational_alerts,
)


@override_settings(
    AURUMWEB_OUTBOX_ENABLED=False,
    AURUMWEB_MONITOR_BACKUPS_ENABLED=False,
    AURUMWEB_MONITOR_DISK_PATH=".",
    AURUMWEB_MONITOR_DISK_WARNING_PERCENT=85,
    AURUMWEB_MONITOR_DISK_CRITICAL_PERCENT=95,
    AURUMWEB_MONITOR_SITE_WARNING_DAYS=30,
    AURUMWEB_MONITOR_ALERT_REPEAT_HOURS=24,
)
class OperationalMonitoringTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_worker_heartbeat_records_age(self):
        moment = timezone.now()

        record_outbox_worker_heartbeat(moment)

        self.assertLess(outbox_worker_heartbeat_age(moment + timedelta(seconds=4)), 5)

    @patch("apps.core.monitoring.shutil.disk_usage")
    def test_collects_critical_disk_condition(self, disk_usage_mock):
        disk_usage_mock.return_value = SimpleNamespace(total=100, used=96, free=4)

        conditions = collect_operational_conditions()

        disk_condition = next(item for item in conditions if item["code"] == "disk-capacity")
        self.assertEqual(disk_condition["severity"], OperationalAlert.Severity.ERROR)

    @patch("apps.core.monitoring.shutil.disk_usage")
    def test_collects_managed_site_expiry(self, disk_usage_mock):
        disk_usage_mock.return_value = SimpleNamespace(total=100, used=20, free=80)
        site = ManagedSite.objects.create(
            title="Клиентский сайт",
            domain_expires_at=timezone.localdate() + timedelta(days=5),
        )

        conditions = collect_operational_conditions()

        self.assertTrue(any(item["code"] == f"site:{site.pk}:domain" for item in conditions))

    @patch("apps.core.monitoring._send_alert_notification", return_value=True)
    def test_alerts_are_deduplicated_and_resolved(self, notify_mock):
        now = timezone.now()
        condition = {
            "code": "test-condition",
            "severity": OperationalAlert.Severity.WARNING,
            "title": "Проверка",
            "message": "Требуется внимание.",
        }

        first = synchronize_operational_alerts([condition], now=now)
        second = synchronize_operational_alerts([condition], now=now + timedelta(minutes=5))
        resolved = synchronize_operational_alerts([], now=now + timedelta(minutes=10))

        alert = OperationalAlert.objects.get(code="test-condition")
        self.assertEqual(first["notified"], 1)
        self.assertEqual(second["notified"], 0)
        self.assertEqual(resolved["active"], 0)
        self.assertFalse(alert.is_active)
        self.assertEqual(notify_mock.call_count, 1)


@override_settings(
    AURUMWEB_ADMIN_EMAILS=["ops@example.com"],
    AURUMWEB_EMAIL_SUBJECT_PREFIX="[AurumWeb] ",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SERVER_EMAIL="server@example.com",
)
class SafeErrorEmailHandlerTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_sends_sanitized_throttled_summary(self):
        handler = SafeErrorEmailHandler()
        record = logging.LogRecord(
            "django.request",
            logging.ERROR,
            __file__,
            42,
            "Чувствительные подробности не должны попасть в письмо",
            (),
            None,
        )
        record.request = SimpleNamespace(path="/billing/result/")
        record.exc_info = (ValueError, ValueError("секрет"), None)

        handler.emit(record)
        handler.emit(record)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ValueError", mail.outbox[0].body)
        self.assertIn("/billing/result/", mail.outbox[0].body)
        self.assertNotIn("Чувствительные подробности", mail.outbox[0].body)
        self.assertNotIn("секрет", mail.outbox[0].body)
