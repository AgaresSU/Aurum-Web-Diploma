import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Profile
from apps.billing.models import Invoice, Payment
from apps.billing.services import mark_payment_succeeded
from apps.projects.models import Project

from .models import IntegrationEvent, OutboundTask
from .outbox import (
    claim_next_outbound_task,
    dispatch_outbound_task,
    enqueue_outbound_task,
    process_available_outbound_tasks,
    process_outbound_task,
)


class OutboundTaskTests(TestCase):
    def test_deduplication_key_prevents_duplicate_task(self):
        first = enqueue_outbound_task(
            OutboundTask.TaskType.QUICK_WEBHOOK_EVENT,
            {"update": {"update_id": 10}},
            "quick-webhook-event:10",
        )
        second = enqueue_outbound_task(
            OutboundTask.TaskType.QUICK_WEBHOOK_EVENT,
            {"update": {"update_id": 10}},
            "quick-webhook-event:10",
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OutboundTask.objects.count(), 1)

    def test_worker_processes_available_task(self):
        task = enqueue_outbound_task(
            OutboundTask.TaskType.QUICK_WEBHOOK_EVENT,
            {"update": {"update_id": 11}},
            "quick-webhook-event:11",
        )

        self.assertEqual(process_available_outbound_tasks(), 1)

        task.refresh_from_db()
        self.assertEqual(task.status, OutboundTask.Status.SUCCEEDED)
        self.assertEqual(task.attempts, 1)
        self.assertIsNotNone(task.completed_at)
        self.assertTrue(IntegrationEvent.objects.filter(title="Webhook быстрых консультаций получен").exists())

    @override_settings(AURUMWEB_OUTBOX_MAX_ATTEMPTS=2, AURUMWEB_OUTBOX_RETRY_BASE_SECONDS=1)
    def test_failed_task_is_retried_and_then_requires_attention(self):
        task = enqueue_outbound_task(
            OutboundTask.TaskType.QUICK_WEBHOOK_EVENT,
            {"update": {"update_id": 12}},
            "quick-webhook-event:12",
        )

        with patch("apps.integrations.outbox.dispatch_outbound_task", side_effect=RuntimeError("network down")):
            task_id = claim_next_outbound_task()
            self.assertFalse(process_outbound_task(task_id))
            task.refresh_from_db()
            self.assertEqual(task.status, OutboundTask.Status.RETRY)
            task.available_at = timezone.now()
            task.save(update_fields=("available_at",))

            task_id = claim_next_outbound_task()
            self.assertFalse(process_outbound_task(task_id))

        task.refresh_from_db()
        self.assertEqual(task.status, OutboundTask.Status.FAILED)
        self.assertEqual(task.attempts, 2)
        self.assertIn("network down", task.last_error)
        self.assertTrue(IntegrationEvent.objects.filter(title="Исходящая операция требует внимания").exists())

    @override_settings(AURUMWEB_OUTBOX_ENABLED=True, TELEGRAM_WEBHOOK_SECRET="expected-secret")
    def test_telegram_webhook_is_durably_queued(self):
        response = self.client.post(
            "/integrations/telegram/webhook/",
            data=json.dumps({"update_id": 100, "message": {"chat": {"id": 42}, "text": "/status"}}),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="expected-secret",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "Webhook queued.")
        task = OutboundTask.objects.get()
        self.assertEqual(task.task_type, OutboundTask.TaskType.TELEGRAM_UPDATE)
        self.assertEqual(task.deduplication_key, "telegram-update:100")

    @override_settings(
        AURUMWEB_OUTBOX_ENABLED=True,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_payment_is_committed_before_notifications_are_processed(self, urlopen):
        invoice = Invoice.objects.create(
            title="Консультация",
            client_name="Client",
            client_email="client@example.test",
            amount="5000.00",
            status=Invoice.Status.ISSUED,
        )
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)

        mark_payment_succeeded(payment, {"OutSum": "5000.00"}, signature_valid=True)

        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(OutboundTask.objects.count(), 4)
        urlopen.assert_not_called()

    @override_settings(
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=True,
        AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED=True,
    )
    @patch("apps.integrations.email.EmailNotificationClient.notify_deadline_reminder", return_value=True)
    def test_deadline_email_task_uses_notification_client(self, notify):
        payload = {
            "title": "Срок проекта «Сайт»",
            "deadline": "2026-08-01",
            "days_left": 3,
            "path": "/client/",
            "email": "client@example.test",
        }

        dispatch_outbound_task(OutboundTask.TaskType.DEADLINE_REMINDER_EMAIL, payload)

        notify.assert_called_once_with(payload)

    @patch("apps.integrations.outbox._telegram_client_available", return_value=True)
    @patch("apps.integrations.telegram.TelegramBotClient.notify_client_project_completed", return_value=True)
    def test_project_completion_task_uses_client_telegram(self, notify, available):
        user = User.objects.create_user(
            "completed-project-client",
            email="completed@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(
            user=user,
            role=Profile.Role.CLIENT,
            telegram_chat_id="777",
            telegram_notifications_enabled=True,
        )
        project = Project.objects.create(
            user=user,
            title="Завершенный проект",
            client_email=user.email,
            status=Project.Status.COMPLETED,
        )

        dispatch_outbound_task(
            OutboundTask.TaskType.PROJECT_COMPLETED_TELEGRAM,
            {"project_id": project.pk},
        )

        available.assert_called_once()
        notify.assert_called_once_with(project, user=user)
