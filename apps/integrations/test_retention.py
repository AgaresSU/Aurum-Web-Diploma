from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .models import IntegrationEvent, OutboundTask


class TechnicalEventRetentionTests(TestCase):
    def test_old_payloads_are_cleared_and_recent_payloads_are_sanitized(self):
        old_event = IntegrationEvent.objects.create(
            provider=IntegrationEvent.Provider.TELEGRAM,
            title="Старое событие",
            payload={"chat_id": "111", "text": "private"},
        )
        recent_event = IntegrationEvent.objects.create(
            provider=IntegrationEvent.Provider.ROBOKASSA,
            title="Новое событие",
            payload={"email": "client@example.test", "state": "ok"},
        )
        task = OutboundTask.objects.create(
            task_type=OutboundTask.TaskType.INVOICE_ISSUED_EMAIL,
            status=OutboundTask.Status.SUCCEEDED,
            payload={"email": "client@example.test"},
            completed_at=timezone.now(),
        )
        old_at = timezone.now() - timedelta(days=120)
        IntegrationEvent.objects.filter(pk=old_event.pk).update(
            created_at=old_at,
            payload={"chat_id": "111", "text": "private"},
        )
        OutboundTask.objects.filter(pk=task.pk).update(updated_at=old_at)

        call_command("purge_technical_events", event_days=90, outbox_days=30)

        old_event.refresh_from_db()
        recent_event.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(old_event.payload, {})
        self.assertTrue(recent_event.payload["email"].startswith("sha256:"))
        self.assertEqual(recent_event.payload["state"], "ok")
        self.assertEqual(task.payload, {})

    def test_operational_payload_is_preserved_until_expiry(self):
        event = IntegrationEvent.objects.create(
            provider=IntegrationEvent.Provider.TELEGRAM,
            title="Telegram: ожидание привязки клиента",
            payload={"user_id": 42, "code": "LINK-CODE"},
            payload_expires_at=timezone.now() + timedelta(minutes=20),
        )

        call_command("purge_technical_events", event_days=90, outbox_days=30)
        event.refresh_from_db()
        self.assertEqual(event.payload, {"user_id": 42, "code": "LINK-CODE"})

        IntegrationEvent.objects.filter(pk=event.pk).update(payload_expires_at=timezone.now() - timedelta(minutes=1))
        call_command("purge_technical_events", event_days=90, outbox_days=30)
        event.refresh_from_db()
        self.assertTrue(event.payload["user_id"].startswith("sha256:"))
        self.assertIsNone(event.payload_expires_at)
