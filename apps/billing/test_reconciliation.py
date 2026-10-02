from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.integrations.models import IntegrationEvent
from apps.integrations.robokassa import RobokassaClient, RobokassaConfig

from .models import Invoice, Payment
from .reconciliation import reconcile_payment, reconcile_pending_payments
from .services import mark_payment_succeeded

LIVE_CONFIG = RobokassaConfig(
    merchant_login="aurum-live",
    password1="password-one",
    password2="password-two",
    test_mode=False,
    hash_algorithm="sha256",
)


def operation_xml(*, result_code=0, state_code=100, out_sum="5000.00", description=""):
    state = ""
    info = ""
    if result_code == 0:
        state = f"<State><Code>{state_code}</Code><RequestDate>2026-07-22T03:00:00+03:00</RequestDate><StateDate>2026-07-22T02:59:00+03:00</StateDate></State>"
        info = (
            "<Info><OutSum>" + out_sum + "</OutSum><OpKey>operation-key</OpKey>"
            "<PaymentMethod><Code>SBP</Code></PaymentMethod></Info>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<OperationStateResponse xmlns="http://merchant.roboxchange.com/WebService/">'
        f"<Result><Code>{result_code}</Code><Description>{description}</Description></Result>"
        f"{state}{info}</OperationStateResponse>"
    ).encode()


class RobokassaOperationStateTests(TestCase):
    def test_signed_operation_state_request_and_xml_parsing(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = operation_xml()
        client = RobokassaClient(LIVE_CONFIG)

        with patch("apps.integrations.robokassa.urlopen", return_value=response) as urlopen:
            state = client.get_operation_state(42, timeout=3)

        request = urlopen.call_args.args[0]
        self.assertIn("InvoiceID=42", request.full_url)
        self.assertIn(client.operation_state_signature(42), request.full_url)
        self.assertEqual(state.result_code, 0)
        self.assertEqual(state.state_code, 100)
        self.assertEqual(str(state.out_sum), "5000.00")
        self.assertEqual(state.operation_key, "operation-key")
        self.assertEqual(state.payment_method, "SBP")

    def test_test_mode_never_calls_operation_state_api(self):
        client = RobokassaClient(RobokassaConfig(**{**LIVE_CONFIG.__dict__, "test_mode": True}))
        with (
            patch("apps.integrations.robokassa.urlopen") as urlopen,
            self.assertRaisesMessage(ValueError, "unavailable in test mode"),
        ):
            client.get_operation_state(1)
        urlopen.assert_not_called()


@override_settings(
    AURUMWEB_OUTBOX_ENABLED=False,
    AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_ADMIN_CHAT_ID="",
)
class PaymentReconciliationTests(TestCase):
    def setUp(self):
        self.invoice = Invoice.objects.create(
            title="Консультация",
            client_name="Клиент",
            client_email="client@example.test",
            amount="5000.00",
            status=Invoice.Status.ISSUED,
        )
        self.payment = Payment.objects.create(invoice=self.invoice, amount=self.invoice.amount)
        self.client = RobokassaClient(LIVE_CONFIG)

    def _state_response(self, **kwargs):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = operation_xml(**kwargs)
        return response

    def test_lost_callback_is_recovered_from_successful_provider_state(self):
        with patch("apps.integrations.robokassa.urlopen", return_value=self._state_response()):
            outcome = reconcile_payment(self.payment, client=self.client)

        self.payment.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(outcome, "succeeded")
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(self.payment.provider_state_code, 100)
        self.assertEqual(self.payment.provider_operation_id, "operation-key")
        self.assertIsNotNone(self.payment.last_reconciled_at)
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

    def test_amount_mismatch_requires_review_without_marking_invoice_paid(self):
        response = self._state_response(out_sum="4999.00")
        with patch("apps.integrations.robokassa.urlopen", return_value=response):
            outcome = reconcile_payment(self.payment, client=self.client)

        self.payment.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(outcome, "review")
        self.assertEqual(self.payment.status, Payment.Status.REVIEW)
        self.assertEqual(self.invoice.status, Invoice.Status.ISSUED)
        self.assertIn("не совпадает", self.payment.reconciliation_note)

    def test_confirmed_payment_of_cancelled_invoice_requires_review(self):
        self.invoice.status = Invoice.Status.CANCELLED
        self.invoice.save(update_fields=("status", "updated_at"))

        mark_payment_succeeded(
            self.payment,
            {"source": "result_url", "OutSum": "5000.00"},
            signature_valid=True,
        )

        self.payment.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.REVIEW)
        self.assertTrue(self.payment.signature_valid)
        self.assertEqual(self.invoice.status, Invoice.Status.CANCELLED)

    def test_provider_cancellation_closes_pending_payment_only(self):
        response = self._state_response(state_code=10)
        with patch("apps.integrations.robokassa.urlopen", return_value=response):
            outcome = reconcile_payment(self.payment, client=self.client)

        self.payment.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(outcome, "cancelled")
        self.assertEqual(self.payment.status, Payment.Status.CANCELLED)
        self.assertEqual(self.invoice.status, Invoice.Status.ISSUED)

    def test_provider_security_hold_stays_pending_and_is_visible(self):
        response = self._state_response(state_code=80)
        with patch("apps.integrations.robokassa.urlopen", return_value=response):
            outcome = reconcile_payment(self.payment, client=self.client)

        self.payment.refresh_from_db()
        self.assertEqual(outcome, "review")
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertTrue(
            IntegrationEvent.objects.filter(
                provider=IntegrationEvent.Provider.ROBOKASSA,
                status=IntegrationEvent.Status.WARNING,
                title="Платеж приостановлен Robokassa",
            ).exists()
        )

    def test_test_payment_gets_manual_review_notice_without_network_request(self):
        test_client = RobokassaClient(RobokassaConfig(**{**LIVE_CONFIG.__dict__, "test_mode": True}))
        with patch("apps.integrations.robokassa.urlopen") as urlopen:
            outcome = reconcile_payment(self.payment, client=test_client)

        self.payment.refresh_from_db()
        self.assertEqual(outcome, "review")
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertIsNotNone(self.payment.last_reconciled_at)
        urlopen.assert_not_called()

    def test_command_only_checks_old_pending_payments(self):
        Payment.objects.filter(pk=self.payment.pk).update(created_at=timezone.now() - timedelta(hours=1))
        recent = Payment.objects.create(invoice=self.invoice, amount=self.invoice.amount)
        response = self._state_response(state_code=5)
        with patch("apps.integrations.robokassa.urlopen", return_value=response) as urlopen:
            summary = reconcile_pending_payments(older_than_minutes=30, client=self.client)

        self.assertEqual(summary.checked, 1)
        self.assertEqual(summary.pending, 1)
        self.assertEqual(urlopen.call_count, 1)
        recent.refresh_from_db()
        self.assertIsNone(recent.last_reconciled_at)

    def test_management_command_is_registered(self):
        Payment.objects.filter(pk=self.payment.pk).update(created_at=timezone.now() - timedelta(hours=1))
        with patch("apps.integrations.robokassa.urlopen", return_value=self._state_response(state_code=5)):
            call_command("reconcile_robokassa_payments", older_than_minutes=30, limit=10)
