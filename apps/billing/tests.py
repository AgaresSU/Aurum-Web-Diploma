import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.integrations.models import IntegrationEvent
from apps.integrations.robokassa import RobokassaClient, RobokassaConfig

from .models import Invoice, InvoiceItem, Payment
from .services import create_robokassa_payment, payment_url_for_invoice

ROBOKASSA_SETTINGS = {
    "MERCHANT_LOGIN": "aurum-test",
    "PASSWORD1": "pass-one",
    "PASSWORD2": "pass-two",
    "TEST_MODE": True,
    "HASH_ALGORITHM": "md5",
    "PAYMENT_URL": "https://auth.robokassa.ru/Merchant/Index.aspx",
    "RECEIPT_ENABLED": True,
    "RECEIPT_SNO": "",
    "RECEIPT_TAX": "none",
    "RECEIPT_PAYMENT_METHOD": "full_payment",
    "RECEIPT_PAYMENT_OBJECT": "service",
}

ROBOKASSA_SHA256_SETTINGS = {
    **ROBOKASSA_SETTINGS,
    "MERCHANT_LOGIN": "aurumweb",
    "PASSWORD1": "password-one",
    "PASSWORD2": "password-two",
    "TEST_MODE": False,
    "HASH_ALGORITHM": "sha256",
}


class RobokassaBillingTests(TestCase):
    def _invoice(self, amount="48000.00"):
        return Invoice.objects.create(
            title="Сайт под ключ",
            client_name="Client",
            client_email="client@example.test",
            amount=amount,
            status=Invoice.Status.ISSUED,
        )

    @override_settings(ROBOKASSA=ROBOKASSA_SETTINGS)
    def test_client_verifies_success_signature_and_amount(self):
        client = RobokassaClient(RobokassaConfig.from_settings())
        signature = client.success_signature("10.00", 5)

        self.assertTrue(client.verify_success("10.00", 5, signature))
        self.assertTrue(client.amount_matches("10", "10.00"))

    @override_settings(ROBOKASSA=ROBOKASSA_SHA256_SETTINGS)
    def test_sha256_signatures_match_robokassa_formulas(self):
        client = RobokassaClient(RobokassaConfig.from_settings())

        self.assertEqual(
            client.payment_signature("10.00", 5),
            "98e9a3eecd32dbf8d5192f23acec93469008f3bdfb3d7e917ff8ea6fd062de07",
        )
        self.assertEqual(
            client.result_signature("10.00", 5),
            "aafe369d1f824765c05119a9b2d6700f10e5a7dbc78c347f3d62fd1ec8ba3e9e",
        )
        self.assertEqual(
            client.success_signature("10.00", 5),
            "19509f3f92321f92da906687d2a260972811611c8cbdf8ed73c08199f1f6ec65",
        )
        self.assertEqual(
            client.operation_state_signature(5),
            "a8105f088baab553f31453e6ec92fb30e4553bb232282c6a35ae5e6c92e866f9",
        )

    @override_settings(
        ROBOKASSA=ROBOKASSA_SHA256_SETTINGS,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_result_url_accepts_sha256_signature(self):
        invoice = self._invoice(amount="10.00")
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        signature = RobokassaClient().result_signature("10.00", payment.pk)

        response = self.client.post(
            reverse("billing:robokassa-result"),
            {"OutSum": "10.00", "InvId": payment.pk, "SignatureValue": signature},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), f"OK{payment.pk}")
        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertTrue(payment.signature_valid)
        self.assertEqual(invoice.status, Invoice.Status.PAID)

    @override_settings(
        ROBOKASSA=ROBOKASSA_SETTINGS,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_result_url_marks_payment_succeeded_when_signature_and_amount_valid(self):
        invoice = self._invoice()
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        client = RobokassaClient()
        signature = client.result_signature(payment.amount, payment.pk)

        response = self.client.post(
            reverse("billing:robokassa-result"),
            {"OutSum": "48000.00", "InvId": payment.pk, "SignatureValue": signature},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), f"OK{payment.pk}")
        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertTrue(payment.signature_valid)
        self.assertEqual(invoice.status, Invoice.Status.PAID)

    @override_settings(
        ROBOKASSA=ROBOKASSA_SETTINGS,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_result_url_cancels_other_pending_robokassa_payments_for_invoice(self):
        invoice = self._invoice()
        stale_payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        client = RobokassaClient()
        signature = client.result_signature(payment.amount, payment.pk)

        response = self.client.post(
            reverse("billing:robokassa-result"),
            {"OutSum": "48000.00", "InvId": payment.pk, "SignatureValue": signature},
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        stale_payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(stale_payment.status, Payment.Status.CANCELLED)

    @override_settings(
        ROBOKASSA=ROBOKASSA_SETTINGS,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_result_url_puts_cancelled_invoice_payment_under_review(self):
        invoice = self._invoice()
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        invoice.status = Invoice.Status.CANCELLED
        invoice.save(update_fields=("status", "updated_at"))
        client = RobokassaClient()
        signature = client.result_signature(payment.amount, payment.pk)

        response = self.client.post(
            reverse("billing:robokassa-result"),
            {"OutSum": "48000.00", "InvId": payment.pk, "SignatureValue": signature},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), f"OK{payment.pk}")
        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.REVIEW)
        self.assertEqual(invoice.status, Invoice.Status.CANCELLED)

    @override_settings(
        ROBOKASSA=ROBOKASSA_SETTINGS,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_result_url_verifies_exact_robokassa_out_sum_string(self):
        invoice = self._invoice(amount="10.00")
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        client = RobokassaClient()
        robokassa_out_sum = "10.000000"
        signature = client.result_signature(robokassa_out_sum, payment.pk)

        response = self.client.post(
            reverse("billing:robokassa-result"),
            {"OutSum": robokassa_out_sum, "InvId": payment.pk, "SignatureValue": signature},
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(invoice.status, Invoice.Status.PAID)

    @override_settings(
        ROBOKASSA=ROBOKASSA_SETTINGS,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_short_billing_result_urls_accept_robokassa_payload(self):
        for path in ("/billing", "/billing/"):
            with self.subTest(path=path):
                invoice = self._invoice()
                payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
                client = RobokassaClient()
                signature = client.result_signature(payment.amount, payment.pk)

                response = self.client.post(
                    path,
                    {"OutSum": "48000.00", "InvId": payment.pk, "SignatureValue": signature},
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content.decode(), f"OK{payment.pk}")
                payment.refresh_from_db()
                invoice.refresh_from_db()
                self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
                self.assertEqual(invoice.status, Invoice.Status.PAID)

    @override_settings(
        ROBOKASSA=ROBOKASSA_SETTINGS,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_result_url_rejects_invalid_signature(self):
        invoice = self._invoice()
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)

        response = self.client.post(
            reverse("billing:robokassa-result"),
            {"OutSum": "48000.00", "InvId": payment.pk, "SignatureValue": "bad-signature"},
        )

        self.assertEqual(response.status_code, 400)
        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertFalse(payment.signature_valid)
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)

    @override_settings(
        ROBOKASSA={
            **ROBOKASSA_SETTINGS,
            "MERCHANT_LOGIN": "",
            "PASSWORD1": "",
            "PASSWORD2": "",
        }
    )
    def test_result_url_fails_closed_when_robokassa_is_not_configured(self):
        invoice = self._invoice()
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        forged_signature = RobokassaClient().result_signature(payment.amount, payment.pk)

        response = self.client.post(
            reverse("billing:robokassa-result"),
            {"OutSum": "48000.00", "InvId": payment.pk, "SignatureValue": forged_signature},
        )

        self.assertEqual(response.status_code, 400)
        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)

    @override_settings(
        ROBOKASSA=ROBOKASSA_SETTINGS,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_result_url_rejects_amount_mismatch_even_with_valid_signature(self):
        invoice = self._invoice()
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        client = RobokassaClient()
        signature = client.result_signature("1.00", payment.pk)

        response = self.client.post(
            reverse("billing:robokassa-result"),
            {"OutSum": "1.00", "InvId": payment.pk, "SignatureValue": signature},
        )

        self.assertEqual(response.status_code, 400)
        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertFalse(payment.signature_valid)
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)

    @override_settings(
        ROBOKASSA=ROBOKASSA_SETTINGS,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_success_url_validates_signature_without_marking_pending_payment_paid(self):
        invoice = self._invoice()
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        client = RobokassaClient()
        signature = client.success_signature(payment.amount, payment.pk)

        response = self.client.get(
            reverse("billing:robokassa-success"),
            {"OutSum": "48000.00", "InvId": payment.pk, "SignatureValue": signature},
        )

        self.assertContains(response, "Подпись SuccessURL корректна")
        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)

    @override_settings(ROBOKASSA=ROBOKASSA_SETTINGS)
    def test_fail_url_does_not_disclose_payment_from_integer_identifier(self):
        invoice = self._invoice()
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)

        response = self.client.get(
            reverse("billing:robokassa-fail"),
            {"OutSum": "48000.00", "InvId": payment.pk, "SignatureValue": "client-return"},
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)
        self.assertNotContains(response, invoice.title)
        self.assertNotContains(response, str(invoice.public_token))
        self.assertFalse(IntegrationEvent.objects.filter(provider=IntegrationEvent.Provider.ROBOKASSA).exists())

    @override_settings(
        ROBOKASSA={
            **ROBOKASSA_SETTINGS,
            "MERCHANT_LOGIN": "",
            "PASSWORD1": "",
            "PASSWORD2": "",
        },
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_pay_invoice_without_robokassa_does_not_create_provider_payment(self):
        invoice = self._invoice()

        first_response = self.client.get(reverse("billing:pay-invoice", kwargs={"token": invoice.public_token}))
        second_response = self.client.get(reverse("billing:pay-invoice", kwargs={"token": invoice.public_token}))

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(first_response["X-Robots-Tag"], "noindex, nofollow, noarchive")
        self.assertContains(first_response, "защищенной")
        self.assertContains(first_response, "СБП")
        self.assertFalse(invoice.payments.exists())

    @override_settings(ROBOKASSA=ROBOKASSA_SETTINGS)
    def test_paid_invoice_payment_link_does_not_create_new_payment(self):
        invoice = self._invoice()
        invoice.status = Invoice.Status.PAID
        invoice.save(update_fields=("status", "updated_at"))

        response = self.client.get(reverse("billing:pay-invoice", kwargs={"token": invoice.public_token}))

        self.assertRedirects(
            response,
            reverse("billing:invoice-detail", kwargs={"token": invoice.public_token}),
            fetch_redirect_response=False,
        )
        self.assertFalse(invoice.payments.exists())

    @override_settings(ROBOKASSA=ROBOKASSA_SETTINGS)
    def test_non_issued_invoice_payment_link_does_not_create_payment(self):
        for status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED):
            with self.subTest(status=status):
                invoice = self._invoice()
                invoice.status = status
                invoice.save(update_fields=("status", "updated_at"))

                response = self.client.get(reverse("billing:pay-invoice", kwargs={"token": invoice.public_token}))

                self.assertEqual(response.status_code, 410)
                invoice.refresh_from_db()
                self.assertEqual(invoice.status, status)
                self.assertFalse(invoice.payments.exists())

    @override_settings(ROBOKASSA=ROBOKASSA_SETTINGS)
    def test_robokassa_payment_requires_issued_invoice(self):
        for status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED, Invoice.Status.PAID):
            with self.subTest(status=status):
                invoice = self._invoice()
                invoice.status = status
                invoice.save(update_fields=("status", "updated_at"))

                with self.assertRaisesMessage(ValueError, "only for issued invoices"):
                    create_robokassa_payment(invoice)

                invoice.refresh_from_db()
                self.assertEqual(invoice.status, status)
                self.assertFalse(invoice.payments.exists())

    @override_settings(ROBOKASSA=ROBOKASSA_SETTINGS)
    def test_payment_url_contains_signed_robokassa_payload(self):
        invoice = self._invoice()

        url = payment_url_for_invoice(invoice)
        payment = invoice.payments.get()
        query = parse_qs(urlparse(url).query)

        self.assertEqual(query["MerchantLogin"], ["aurum-test"])
        self.assertEqual(query["OutSum"], ["48000.00"])
        self.assertEqual(query["InvId"], [str(payment.pk)])
        self.assertEqual(query["IsTest"], ["1"])
        self.assertEqual(query["Email"], ["client@example.test"])
        receipt = query["Receipt"][0]
        self.assertEqual(
            query["SignatureValue"], [RobokassaClient().payment_signature(payment.amount, payment.pk, receipt)]
        )

    @override_settings(ROBOKASSA=ROBOKASSA_SETTINGS)
    def test_payment_url_reuses_pending_payment_for_repeated_attempt(self):
        invoice = self._invoice()

        first_url = payment_url_for_invoice(invoice)
        second_url = payment_url_for_invoice(invoice)

        first_inv_id = parse_qs(urlparse(first_url).query)["InvId"][0]
        second_inv_id = parse_qs(urlparse(second_url).query)["InvId"][0]
        self.assertEqual(first_inv_id, second_inv_id)
        self.assertEqual(invoice.payments.filter(status=Payment.Status.PENDING).count(), 1)

    @override_settings(ROBOKASSA=ROBOKASSA_SETTINGS)
    def test_success_url_requires_valid_signature_before_rendering_invoice(self):
        invoice = self._invoice()
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)

        response = self.client.get(
            reverse("billing:robokassa-success"),
            {"OutSum": "48000.00", "InvId": payment.pk, "SignatureValue": "invalid"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, invoice.title)
        self.assertNotContains(response, str(invoice.public_token))

    def test_public_invoice_hides_personal_data_and_expires(self):
        invoice = self._invoice()
        invoice.client_requisites = {"inn": "123456789012", "client_type_display": "Физическое лицо"}
        invoice.public_access_expires_at = timezone.now() + timedelta(minutes=5)
        invoice.save(update_fields=("client_requisites", "public_access_expires_at", "updated_at"))

        response = self.client.get(reverse("billing:invoice-detail", kwargs={"token": invoice.public_token}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, invoice.client_email)
        self.assertNotContains(response, "123456789012")
        self.assertNotContains(response, "mc.yandex.ru")

        invoice.public_access_expires_at = timezone.now() - timedelta(seconds=1)
        invoice.save(update_fields=("public_access_expires_at", "updated_at"))
        response = self.client.get(reverse("billing:invoice-detail", kwargs={"token": invoice.public_token}))
        self.assertEqual(response.status_code, 410)

    @override_settings(
        ROBOKASSA=ROBOKASSA_SETTINGS,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_second_successful_payment_is_recorded_as_duplicate(self):
        invoice = self._invoice(amount="10.00")
        first_payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        second_payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        client = RobokassaClient()

        for payment in (first_payment, second_payment):
            signature = client.result_signature(payment.amount, payment.pk)
            response = self.client.post(
                reverse("billing:robokassa-result"),
                {"OutSum": "10.00", "InvId": payment.pk, "SignatureValue": signature},
            )
            self.assertEqual(response.status_code, 200)

        first_payment.refresh_from_db()
        second_payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(first_payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(second_payment.status, Payment.Status.DUPLICATE)
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertTrue(
            IntegrationEvent.objects.filter(
                provider=IntegrationEvent.Provider.ROBOKASSA,
                status=IntegrationEvent.Status.ERROR,
                title="Обнаружена повторная оплата счета",
            ).exists()
        )

    @override_settings(ROBOKASSA=ROBOKASSA_SETTINGS)
    def test_payment_url_includes_receipt_item_note_and_actual_amount(self):
        invoice = self._invoice(amount="30000.00")
        InvoiceItem.objects.create(
            invoice=invoice,
            name="Консультация",
            note="Продвижение сайта",
            quantity=1,
            unit_price="30000.00",
        )

        url = payment_url_for_invoice(invoice)
        payment = invoice.payments.get()
        query = parse_qs(urlparse(url).query)
        receipt = json.loads(query["Receipt"][0])

        self.assertEqual(receipt["items"][0]["name"], "Консультация - Продвижение сайта")
        self.assertEqual(receipt["items"][0]["sum"], 30000)
        self.assertEqual(
            query["SignatureValue"],
            [RobokassaClient().payment_signature(payment.amount, payment.pk, query["Receipt"][0])],
        )

    @override_settings(ROBOKASSA=ROBOKASSA_SETTINGS)
    def test_receipt_falls_back_to_invoice_amount_when_items_do_not_match(self):
        invoice = self._invoice(amount="30000.00")
        InvoiceItem.objects.create(
            invoice=invoice,
            name="Консультация",
            note="Стартовая цена",
            quantity=1,
            unit_price="5000.00",
        )

        url = payment_url_for_invoice(invoice)
        query = parse_qs(urlparse(url).query)
        receipt = json.loads(query["Receipt"][0])

        self.assertEqual(
            receipt["items"],
            [
                {
                    "name": "Сайт под ключ",
                    "quantity": 1,
                    "sum": 30000,
                    "payment_method": "full_payment",
                    "payment_object": "service",
                    "tax": "none",
                }
            ],
        )

    @override_settings(
        ROBOKASSA=ROBOKASSA_SETTINGS,
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    @patch("apps.billing.services.TelegramBotClient.notify_client_payment_succeeded")
    @patch("apps.billing.services.TelegramBotClient.notify_payment_succeeded")
    @patch("apps.billing.services.EmailNotificationClient.notify_payment_succeeded")
    def test_result_url_is_idempotent_for_repeated_success_callback(
        self,
        notify_email,
        notify_admin_telegram,
        notify_client_telegram,
    ):
        invoice = self._invoice()
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        client = RobokassaClient()
        signature = client.result_signature(payment.amount, payment.pk)
        payload = {"OutSum": "48000.00", "InvId": payment.pk, "SignatureValue": signature}

        first_response = self.client.post(reverse("billing:robokassa-result"), payload)
        second_response = self.client.post(reverse("billing:robokassa-result"), payload)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(notify_email.call_count, 1)
        self.assertEqual(notify_admin_telegram.call_count, 1)
        self.assertEqual(notify_client_telegram.call_count, 1)
        self.assertEqual(
            IntegrationEvent.objects.filter(
                provider=IntegrationEvent.Provider.ROBOKASSA,
                status=IntegrationEvent.Status.SUCCESS,
                title="Оплата подтверждена",
            ).count(),
            1,
        )
