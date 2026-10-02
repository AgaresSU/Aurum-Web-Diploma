from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.billing.models import Invoice, InvoiceItem, Order, Payment
from apps.content.models import Service
from apps.leads.models import QuickConsultation

from .audit import reset_audit_context, set_audit_context
from .legal_acceptance import record_legal_acceptance
from .models import AuditEvent, ConsentAcceptance
from .redaction import sanitize_log_data


class DataConstraintTests(TestCase):
    def setUp(self):
        self.invoice = Invoice.objects.create(title="Рабочий счет", amount=Decimal("1000.00"))

    def assert_integrity_error(self, callback):
        with self.assertRaises(IntegrityError), transaction.atomic():
            callback()

    def test_financial_models_reject_nonpositive_values(self):
        self.assert_integrity_error(lambda: Invoice.objects.create(title="Ошибка", amount=Decimal("0.00")))
        self.assert_integrity_error(
            lambda: InvoiceItem.objects.create(
                invoice=self.invoice,
                name="Ошибка",
                quantity=Decimal("0.00"),
                unit_price=Decimal("100.00"),
            )
        )
        self.assert_integrity_error(lambda: Payment.objects.create(invoice=self.invoice, amount=Decimal("-1.00")))

    def test_estimates_quotes_and_prices_reject_invalid_ranges(self):
        self.assert_integrity_error(
            lambda: Order.objects.create(
                title="Ошибка",
                estimated_amount_min=Decimal("2000.00"),
                estimated_amount_max=Decimal("1000.00"),
            )
        )

        self.assert_integrity_error(
            lambda: QuickConsultation.objects.create(
                telegram_chat_id="100",
                quoted_amount=Decimal("0.00"),
            )
        )
        self.assert_integrity_error(
            lambda: Service.objects.create(
                title="Ошибка",
                slug="invalid-zero-price",
                service_type=Service.ServiceType.CONSULTING,
                short_description="Проверка ограничения",
                price_amount=0,
            )
        )

    def test_integrity_preflight_accepts_valid_data(self):
        call_command("check_data_integrity")


class AuditEventTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="auditor", password="StrongPass123!", is_staff=True)

    def test_staff_changes_are_recorded_without_personal_fields(self):
        token = set_audit_context(actor_id=self.staff.pk, ip_address="127.0.0.1", channel="office")
        try:
            invoice = Invoice.objects.create(
                title="Разработка сайта",
                amount=Decimal("48000.00"),
                client_email="private@example.test",
                description="Закрытое описание клиента",
            )
            invoice.status = Invoice.Status.ISSUED
            invoice.save(update_fields=("status", "updated_at"))
        finally:
            reset_audit_context(token)

        events = AuditEvent.objects.filter(object_type="billing.Invoice", object_id=str(invoice.pk))
        self.assertEqual(events.count(), 2)
        update_event = events.get(action=AuditEvent.Action.UPDATE)
        self.assertEqual(update_event.actor, self.staff)
        self.assertEqual(update_event.ip_address, "127.0.0.1")
        self.assertEqual(update_event.before["status"], Invoice.Status.DRAFT)
        self.assertEqual(update_event.after["status"], Invoice.Status.ISSUED)
        self.assertNotIn("client_email", update_event.after)
        self.assertNotIn("description", update_event.after)

    def test_audit_event_cannot_be_changed_or_deleted(self):
        event = AuditEvent.objects.create(
            actor=self.staff,
            action=AuditEvent.Action.CREATE,
            object_type="billing.Invoice",
            object_id="1",
            object_label="Счет #1",
        )
        event.object_label = "Изменено"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(TypeError):
            AuditEvent.objects.filter(pk=event.pk).update(object_label="Изменено")


class ConsentAcceptanceTests(TestCase):
    def test_document_version_checksum_channel_and_deduplication_are_saved(self):
        user = User.objects.create_user(username="client", email="client@example.test")
        first = record_legal_acceptance(
            document_type=ConsentAcceptance.DocumentType.PRIVACY,
            subject=user,
            channel=ConsentAcceptance.Channel.ACCOUNT,
            user=user,
        )
        second = record_legal_acceptance(
            document_type=ConsentAcceptance.DocumentType.PRIVACY,
            subject=user,
            channel=ConsentAcceptance.Channel.ACCOUNT,
            user=user,
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.document_version, "02.06.2026")
        self.assertEqual(len(first.document_checksum), 64)
        self.assertEqual(first.channel, ConsentAcceptance.Channel.ACCOUNT)

    def test_log_redaction_removes_secrets_and_personal_values(self):
        sanitized = sanitize_log_data(
            {
                "bot_token": "secret-token",
                "chat_id": "123456",
                "email": "client@example.test",
                "result": {"status": "ok"},
            }
        )
        self.assertEqual(sanitized["bot_token"], "[скрыто]")
        self.assertTrue(sanitized["chat_id"].startswith("sha256:"))
        self.assertTrue(sanitized["email"].startswith("sha256:"))
        self.assertEqual(sanitized["result"], {"status": "ok"})
