import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Profile
from apps.billing.models import Invoice, Payment
from apps.billing.services import issue_invoice, mark_payment_succeeded
from apps.integrations.models import QuickConsultationBotSettings, RobokassaSettings, TelegramBotSettings
from apps.integrations.robokassa import RobokassaClient
from apps.integrations.telegram import TelegramBotClient
from apps.leads.models import Lead, QuickConsultation
from apps.leads.services import create_quick_consultation_invoice, quick_payment_item_name_for_amount
from apps.messaging.models import Conversation, Message

ROBOKASSA_EMPTY_SETTINGS = {
    "MERCHANT_LOGIN": "",
    "PASSWORD1": "",
    "PASSWORD2": "",
    "TEST_MODE": True,
    "HASH_ALGORITHM": "md5",
    "PAYMENT_URL": "https://auth.robokassa.ru/Merchant/Index.aspx",
    "RECEIPT_ENABLED": True,
    "RECEIPT_SNO": "",
    "RECEIPT_TAX": "none",
    "RECEIPT_PAYMENT_METHOD": "full_payment",
    "RECEIPT_PAYMENT_OBJECT": "service",
}


class RobokassaSettingsTests(TestCase):
    @override_settings(ROBOKASSA=ROBOKASSA_EMPTY_SETTINGS)
    def test_client_loads_database_settings(self):
        config = RobokassaSettings.load()
        config.is_enabled = True
        config.merchant_login = "aurumweb"
        config.set_password1("password-one")
        config.set_password2("password-two")
        config.test_mode = False
        config.receipt_enabled = True
        config.receipt_tax = "none"
        config.save()

        client = RobokassaClient()

        self.assertTrue(client.configured)
        self.assertEqual(client.config.merchant_login, "aurumweb")
        self.assertEqual(client.config.password1, "password-one")
        self.assertEqual(client.config.password2, "password-two")
        self.assertFalse(client.config.test_mode)
        self.assertTrue(client.config.receipt_enabled)

    @override_settings(
        ROBOKASSA={
            **ROBOKASSA_EMPTY_SETTINGS,
            "MERCHANT_LOGIN": "env-login",
            "PASSWORD1": "env-password-one",
            "PASSWORD2": "env-password-two",
        }
    )
    def test_client_uses_environment_settings_when_database_settings_disabled(self):
        config = RobokassaSettings.load()
        config.is_enabled = False
        config.merchant_login = "database-login"
        config.set_password1("database-password-one")
        config.set_password2("database-password-two")
        config.save()

        client = RobokassaClient()

        self.assertTrue(client.configured)
        self.assertEqual(client.config.merchant_login, "env-login")
        self.assertEqual(client.config.password1, "env-password-one")
        self.assertEqual(client.config.password2, "env-password-two")


class TelegramWebhookTests(TestCase):
    def _telegram_response(self, payload):
        class Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, traceback):
                return False

            def read(self_inner):
                return json.dumps(payload).encode("utf-8")

        return Response()

    def _telegram_payloads(self, urlopen):
        payloads = []
        for call in urlopen.call_args_list:
            request_obj = call.args[0]
            content_type = request_obj.get_header("Content-type") or request_obj.get_header("Content-Type") or ""
            if request_obj.data and "multipart/form-data" not in content_type:
                payloads.append((request_obj.full_url, json.loads(request_obj.data.decode("utf-8"))))
        return payloads

    def _telegram_multipart_bodies(self, urlopen):
        bodies = []
        for call in urlopen.call_args_list:
            request_obj = call.args[0]
            content_type = request_obj.get_header("Content-type") or request_obj.get_header("Content-Type") or ""
            if request_obj.data and "multipart/form-data" in content_type:
                bodies.append((request_obj.full_url, request_obj.data.decode("utf-8", errors="ignore")))
        return bodies

    @override_settings(TELEGRAM_WEBHOOK_SECRET="expected-secret")
    def test_webhook_requires_secret_when_configured(self):
        response = self.client.post(
            "/integrations/telegram/webhook/",
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(TELEGRAM_WEBHOOK_SECRET="expected-secret")
    def test_webhook_accepts_valid_secret(self):
        response = self.client.post(
            "/integrations/telegram/webhook/",
            data="{}",
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="expected-secret",
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(DEBUG=False, TELEGRAM_WEBHOOK_SECRET="", ALLOWED_HOSTS=["testserver"])
    def test_webhook_requires_secret_in_production(self):
        response = self.client.post(
            "/integrations/telegram/webhook/",
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_webhook_status_command_replies_to_admin_chat(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.save()
        Lead.objects.create(name="Client", email="client@example.test", task="Нужен сайт.")
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 10}})

        response = self.client.post(
            "/integrations/telegram/webhook/",
            data=json.dumps({"update_id": 1, "message": {"chat": {"id": 777}, "text": "/status"}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], "777")
        self.assertIn("Сводка AurumWeb Office", payload["text"])
        self.assertIn("Новые заявки: 1", payload["text"])

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_webhook_does_not_expose_status_to_unknown_chat(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.save()
        Lead.objects.create(name="Client", email="client@example.test", task="Нужен сайт.")
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 11}})

        response = self.client.post(
            "/integrations/telegram/webhook/",
            data=json.dumps({"update_id": 2, "message": {"chat": {"id": 999}, "text": "/status"}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], "999")
        self.assertIn("Зарегистрируйтесь", payload["text"])
        self.assertNotIn("Новые заявки", payload["text"])

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_webhook_links_client_chat_from_start_payload(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_bot"
        config.save()
        user = User.objects.create_user(
            username="client",
            email="client@example.test",
            password="StrongPass123!",
        )
        profile = Profile.objects.create(user=user, role=Profile.Role.CLIENT)
        profile.refresh_telegram_link_code()
        profile.save(update_fields=("telegram_link_code", "telegram_link_code_created_at"))
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 12}})

        response = self.client.post(
            "/integrations/telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 3,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"username": "client_tg"},
                        "text": f"/start {profile.telegram_link_code}",
                    },
                }
            ),
            content_type="application/json",
        )

        profile.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(profile.telegram_chat_id, "999")
        self.assertTrue(profile.telegram_notifications_enabled)
        self.assertEqual(profile.telegram_username, "@client_tg")
        self.assertEqual(profile.telegram_link_code, "")
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("Telegram подключен", payload["text"])
        self.assertIn("reply_markup", payload)
        self.assertIn("Открыть кабинет", json.dumps(payload["reply_markup"], ensure_ascii=False))

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_webhook_does_not_link_recent_client_without_explicit_code(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_bot"
        config.save()
        user = User.objects.create_user(
            username="client",
            email="client@example.test",
            password="StrongPass123!",
        )
        profile = Profile.objects.create(user=user, role=Profile.Role.CLIENT)
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 15}})

        self.client.force_login(user)
        response = self.client.get("/client/telegram/connect/")
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            "/integrations/telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 31,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"username": "client_tg"},
                        "text": "/start",
                    },
                }
            ),
            content_type="application/json",
        )

        profile.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(profile.telegram_chat_id, "")
        self.assertFalse(profile.telegram_notifications_enabled)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("подключите Telegram", payload["text"])

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_webhook_start_without_payload_replies_with_connection_instruction(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_bot"
        config.save()
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 17}})

        response = self.client.post(
            "/integrations/telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 33,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"username": "client_tg"},
                        "text": "/start",
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], "999")
        self.assertIn("зарегистрируйтесь по кнопке", payload["text"])
        markup = json.dumps(payload["reply_markup"], ensure_ascii=False)
        self.assertIn("Зарегистрироваться", markup)
        self.assertIn("/accounts/register/?telegram=", markup)
        self.assertIn("Войти в кабинет", markup)
        self.assertIn("/accounts/login/?fresh=1", markup)

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_quick_consultation_start_payload_opens_hidden_flow_without_site_buttons(self, urlopen):
        config = QuickConsultationBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_bot"
        config.save()
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 18}})

        response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 34,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"id": 999, "username": "quick_client"},
                        "text": "/start consult",
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["method"], "sendPhoto")
        self.assertEqual(payload["chat_id"], "999")
        self.assertIn("Публичная оферта", payload["caption"])
        markup = json.dumps(payload["reply_markup"], ensure_ascii=False)
        self.assertIn("Открыть оферту", markup)
        self.assertIn("Принимаю оферту", markup)
        consultation = QuickConsultation.objects.get()
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_OFFER)
        self.assertEqual(consultation.pending_action, QuickConsultation.PendingAction.MENU)
        self.assertNotIn("Зарегистрироваться", markup)
        self.assertNotIn("/accounts/", markup)

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_quick_consultation_start_legal_flow_finishes_with_menu(self, urlopen):
        config = QuickConsultationBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_bot"
        config.save()
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 26}})

        start_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 341,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "text": "/start",
                    },
                }
            ),
            content_type="application/json",
        )
        start_payload = start_response.json()
        self.assertEqual(start_payload["method"], "sendPhoto")
        self.assertIn("Публичная оферта", start_payload["caption"])

        self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 342,
                    "callback_query": {
                        "id": "callback-start-offer",
                        "data": "quick:accept_offer",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )
        privacy_payload = next(
            payload
            for url, payload in self._telegram_payloads(urlopen)
            if "sendPhoto" in url and "Политика обработки" in payload["caption"]
        )
        self.assertEqual(privacy_payload["chat_id"], "999")

        self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 343,
                    "callback_query": {
                        "id": "callback-start-privacy",
                        "data": "quick:accept_privacy",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )
        email_payload = next(
            payload
            for url, payload in self._telegram_payloads(urlopen)
            if "sendPhoto" in url and "Email для уведомлений" in payload["caption"]
        )
        self.assertEqual(email_payload["chat_id"], "999")

        email_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 344,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "text": "client@example.test",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertIn("Проверьте email", email_response.json()["caption"])

        confirm_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 345,
                    "callback_query": {
                        "id": "callback-start-email",
                        "data": "quick:confirm_email",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )

        consultation = QuickConsultation.objects.get()
        consultation.refresh_from_db()
        self.assertEqual(confirm_response.json()["method"], "answerCallbackQuery")
        self.assertEqual(consultation.status, QuickConsultation.Status.CLOSED)
        self.assertEqual(consultation.pending_action, QuickConsultation.PendingAction.MENU)
        menu_payload = next(
            payload
            for url, payload in self._telegram_payloads(urlopen)
            if "sendPhoto" in url and payload["caption"].startswith("AurumWeb Fast")
        )
        menu_markup = json.dumps(menu_payload["reply_markup"], ensure_ascii=False)
        self.assertIn("Оплатить услугу", menu_markup)
        self.assertIn("Проконсультироваться", menu_markup)

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_main_telegram_bot_does_not_handle_quick_consultation_commands(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_bot"
        config.save()
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 21}})

        response = self.client.post(
            "/integrations/telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 37,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"id": 999, "username": "quick_client"},
                        "text": "/consult",
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(QuickConsultation.objects.count(), 0)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], "999")
        self.assertIn("Зарегистрируйтесь", payload["text"])

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_quick_consultation_flow_creates_office_request_from_bot(self, urlopen):
        config = QuickConsultationBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_bot"
        config.save()
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 19}})
        accepted_at = timezone.now()
        previous_consultation = QuickConsultation.objects.create(
            telegram_chat_id="999",
            telegram_user_id="999",
            telegram_username="@quick_client",
            client_email="client@example.test",
            offer_accepted_at=accepted_at,
            privacy_accepted_at=accepted_at,
            status=QuickConsultation.Status.PAID,
        )

        start_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 35,
                    "callback_query": {
                        "id": "callback-quick",
                        "data": "quick:start",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(start_response.status_code, 200)
        start_payload = start_response.json()
        self.assertEqual(start_payload["method"], "answerCallbackQuery")
        self.assertEqual(start_payload["callback_query_id"], "callback-quick")
        self.assertNotIn("text", start_payload)
        payloads = self._telegram_payloads(urlopen)
        start_message = next(
            payload for url, payload in payloads if "sendMessage" in url and payload["chat_id"] == "999"
        )
        self.assertIn("Опишите вопрос", start_message["text"])
        consultation = QuickConsultation.objects.exclude(pk=previous_consultation.pk).get()
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_QUESTION)
        self.assertEqual(consultation.pending_action, QuickConsultation.PendingAction.CONSULTATION)
        self.assertEqual(consultation.client_email, "client@example.test")
        self.assertEqual(consultation.telegram_username, "@quick_client")
        self.assertEqual(consultation.client_name, "Иван")

        question_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 36,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "text": "Нужно быстро разобрать структуру сайта.",
                    },
                }
            ),
            content_type="application/json",
        )

        consultation.refresh_from_db()
        self.assertEqual(question_response.status_code, 200)
        question_payload = question_response.json()
        self.assertEqual(question_payload["method"], "sendMessage")
        self.assertEqual(question_payload["chat_id"], "999")
        self.assertIn("принята", question_payload["text"])
        self.assertEqual(consultation.status, QuickConsultation.Status.NEW)
        self.assertEqual(consultation.question, "Нужно быстро разобрать структуру сайта.")
        payloads = self._telegram_payloads(urlopen)
        sent_messages = [payload for url, payload in payloads if "sendMessage" in url]
        admin_payload = next(
            payload
            for payload in sent_messages
            if payload["chat_id"] == "777" and "Новая быстрая консультация" in payload["text"]
        )
        admin_markup = json.dumps(admin_payload["reply_markup"], ensure_ascii=False)
        self.assertIn("Открыть консультацию", admin_markup)
        self.assertNotIn("admin:", admin_markup)
        self.assertFalse(any(payload["chat_id"] == "999" and "принята" in payload["text"] for payload in sent_messages))

    @override_settings(
        DEBUG=True,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
        AURUMWEB_SITE_URL="https://aurumweb.test",
        ROBOKASSA={
            **ROBOKASSA_EMPTY_SETTINGS,
            "MERCHANT_LOGIN": "AurumWeb",
            "PASSWORD1": "password-one",
            "PASSWORD2": "password-two",
        },
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_quick_consultation_client_enters_amount_and_receives_payment_link(self, urlopen):
        config = QuickConsultationBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_fast_bot"
        config.save()
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 22}})

        pay_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 38,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "text": "Оплатить консультацию",
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(pay_response.status_code, 200)
        pay_payload = pay_response.json()
        self.assertEqual(pay_payload["method"], "sendPhoto")
        self.assertIn("Публичная оферта", pay_payload["caption"])
        self.assertEqual(pay_payload["chat_id"], "999")
        consultation = QuickConsultation.objects.get()
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_OFFER)

        offer_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 39,
                    "callback_query": {
                        "id": "callback-offer",
                        "data": "quick:accept_offer",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )
        consultation.refresh_from_db()
        offer_payload = offer_response.json()
        self.assertEqual(offer_payload["method"], "answerCallbackQuery")
        self.assertNotIn("text", offer_payload)
        privacy_deferred_payload = next(
            payload
            for url, payload in self._telegram_payloads(urlopen)
            if "sendPhoto" in url and "Политика обработки" in payload["caption"]
        )
        self.assertEqual(privacy_deferred_payload["chat_id"], "999")
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_PRIVACY)
        self.assertIsNotNone(consultation.offer_accepted_at)

        privacy_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 40,
                    "callback_query": {
                        "id": "callback-privacy",
                        "data": "quick:accept_privacy",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )
        consultation.refresh_from_db()
        privacy_payload = privacy_response.json()
        self.assertEqual(privacy_payload["method"], "answerCallbackQuery")
        self.assertNotIn("text", privacy_payload)
        email_deferred_payload = next(
            payload
            for url, payload in self._telegram_payloads(urlopen)
            if "sendPhoto" in url and "Email для уведомлений" in payload["caption"]
        )
        self.assertEqual(email_deferred_payload["chat_id"], "999")
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_EMAIL)
        self.assertIsNotNone(consultation.privacy_accepted_at)

        back_offer_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 401,
                    "callback_query": {
                        "id": "callback-back-offer",
                        "data": "quick:back_offer",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )
        consultation.refresh_from_db()
        back_offer_payload = back_offer_response.json()
        self.assertEqual(back_offer_payload["method"], "answerCallbackQuery")
        self.assertNotIn("text", back_offer_payload)
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_OFFER)
        back_offer_deferred_payload = next(
            payload
            for url, payload in self._telegram_payloads(urlopen)
            if "sendPhoto" in url and "Публичная оферта" in payload["caption"]
        )
        self.assertIn("Принимаю оферту", json.dumps(back_offer_deferred_payload["reply_markup"], ensure_ascii=False))

        self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 402,
                    "callback_query": {
                        "id": "callback-offer-again",
                        "data": "quick:accept_offer",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )
        self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 403,
                    "callback_query": {
                        "id": "callback-privacy-again",
                        "data": "quick:accept_privacy",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )
        consultation.refresh_from_db()
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_EMAIL)

        email_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 41,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "text": "client@example.test",
                    },
                }
            ),
            content_type="application/json",
        )
        consultation.refresh_from_db()
        email_payload = email_response.json()
        self.assertEqual(email_payload["method"], "sendPhoto")
        self.assertIn("Проверьте email", email_payload["caption"])
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_EMAIL_CONFIRM)
        self.assertEqual(consultation.client_email, "client@example.test")

        confirm_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 42,
                    "callback_query": {
                        "id": "callback-email",
                        "data": "quick:confirm_email",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )
        consultation.refresh_from_db()
        confirm_payload = confirm_response.json()
        self.assertEqual(confirm_payload["method"], "answerCallbackQuery")
        choice_deferred_payload = next(
            payload
            for url, payload in self._telegram_payloads(urlopen)
            if "sendPhoto" in url and "AurumWeb Fast" in payload["caption"]
        )
        self.assertIn("Оплатить 5 000", json.dumps(choice_deferred_payload["reply_markup"], ensure_ascii=False))
        self.assertIn("Указать свою сумму", json.dumps(choice_deferred_payload["reply_markup"], ensure_ascii=False))
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_PAYMENT_AMOUNT)

        amount_response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 43,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "text": "7550",
                    },
                }
            ),
            content_type="application/json",
        )

        consultation.refresh_from_db()
        self.assertEqual(amount_response.status_code, 200)
        amount_payload = amount_response.json()
        self.assertEqual(amount_payload["method"], "sendMessage")
        self.assertIn("Счет за услугу AurumWeb сформирован", amount_payload["text"])
        self.assertIn("Написание и разработка Telegram-бота", amount_payload["text"])
        self.assertIn("7550.00", amount_payload["text"])
        payment_markup = json.dumps(amount_payload["reply_markup"], ensure_ascii=False)
        self.assertIn("Оплатить услугу", payment_markup)
        self.assertIn(
            f"https://aurumweb.test/billing/invoices/{consultation.invoice.public_token}/pay/", payment_markup
        )
        self.assertEqual(consultation.status, QuickConsultation.Status.INVOICED)
        self.assertIsNotNone(consultation.invoice_id)
        invoice = consultation.invoice
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)
        self.assertEqual(str(invoice.amount), "7550.00")
        self.assertEqual(invoice.client_email, "client@example.test")
        self.assertEqual(invoice.client_requisites["client_type"], "telegram")
        self.assertEqual(invoice.client_requisites["client_email"], "client@example.test")
        self.assertTrue(invoice.client_requisites["offer_accepted_at"])
        self.assertTrue(invoice.client_requisites["privacy_accepted_at"])
        self.assertEqual(invoice.title, "Написание и разработка Telegram-бота")
        self.assertEqual(invoice.client_requisites["item_name"], "Написание и разработка Telegram-бота")
        item = invoice.items.get()
        self.assertEqual(item.name, "Написание и разработка Telegram-бота")
        self.assertEqual(str(item.unit_price), "7550.00")
        receipt_items = RobokassaClient().receipt_payload(invoice)["items"]
        self.assertEqual(len(receipt_items), 1)
        self.assertEqual(receipt_items[0]["name"], "Написание и разработка Telegram-бота")
        self.assertEqual(receipt_items[0]["sum"], 7550)
        self.assertEqual(invoice.payments.get().amount, invoice.amount)

    @override_settings(
        DEBUG=True,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
        AURUMWEB_SITE_URL="https://aurumweb.test",
        ROBOKASSA={
            **ROBOKASSA_EMPTY_SETTINGS,
            "MERCHANT_LOGIN": "AurumWeb",
            "PASSWORD1": "password-one",
            "PASSWORD2": "password-two",
        },
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_quick_consultation_fixed_amount_creates_5000_invoice(self, urlopen):
        config = QuickConsultationBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_fast_bot"
        config.save()
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 23}})
        consultation = QuickConsultation.objects.create(
            telegram_chat_id="999",
            telegram_user_id="999",
            telegram_username="@quick_client",
            client_email="client@example.test",
            offer_accepted_at=timezone.now(),
            privacy_accepted_at=timezone.now(),
            status=QuickConsultation.Status.WAITING_PAYMENT_AMOUNT,
        )

        response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 44,
                    "callback_query": {
                        "id": "callback-fixed",
                        "data": "quick:pay_fixed",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )

        consultation.refresh_from_db()
        payload = response.json()
        self.assertEqual(payload["method"], "answerCallbackQuery")
        deferred_payload = next(
            payload
            for url, payload in self._telegram_payloads(urlopen)
            if "sendMessage" in url and "Счет за услугу AurumWeb сформирован" in payload["text"]
        )
        self.assertIn("5000.00", deferred_payload["text"])
        self.assertIn("Консультация по поддержке Telegram-бота", deferred_payload["text"])
        self.assertIn("Оплатить услугу", json.dumps(deferred_payload["reply_markup"], ensure_ascii=False))
        self.assertEqual(consultation.status, QuickConsultation.Status.INVOICED)
        self.assertEqual(consultation.invoice.status, Invoice.Status.ISSUED)
        self.assertEqual(str(consultation.invoice.amount), "5000.00")
        item = consultation.invoice.items.get()
        self.assertEqual(item.name, "Консультация по поддержке Telegram-бота")
        self.assertEqual(str(item.unit_price), "5000.00")

    @override_settings(
        DEBUG=True,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
        AURUMWEB_SITE_URL="https://aurumweb.test",
        ROBOKASSA={
            **ROBOKASSA_EMPTY_SETTINGS,
            "MERCHANT_LOGIN": "AurumWeb",
            "PASSWORD1": "password-one",
            "PASSWORD2": "password-two",
        },
    )
    def test_quick_consultation_invoice_uses_amount_based_nomenclature(self):
        cases = (
            ("5000.00", "Консультация по поддержке Telegram-бота"),
            ("5001.00", "Написание и разработка Telegram-бота"),
            ("14999.00", "Написание и разработка Telegram-бота"),
            ("15000.00", "Разработка программного приложения"),
            ("30000.00", "Создание сайта"),
        )
        for index, (amount, expected_name) in enumerate(cases, start=1):
            with self.subTest(amount=amount):
                consultation = QuickConsultation.objects.create(
                    telegram_chat_id=f"999{index}",
                    telegram_user_id=f"999{index}",
                    telegram_username="@quick_client",
                    client_email="client@example.test",
                    offer_accepted_at=timezone.now(),
                    privacy_accepted_at=timezone.now(),
                    status=QuickConsultation.Status.WAITING_PAYMENT_AMOUNT,
                )

                invoice, _payment_url = create_quick_consultation_invoice(consultation, amount)

                self.assertEqual(quick_payment_item_name_for_amount(amount), expected_name)
                self.assertEqual(invoice.title, expected_name)
                self.assertEqual(invoice.client_requisites["item_name"], expected_name)
                item = invoice.items.get()
                self.assertEqual(item.name, expected_name)
                self.assertEqual(str(item.unit_price), amount)
                receipt_items = RobokassaClient().receipt_payload(invoice)["items"]
                self.assertEqual(receipt_items[0]["name"], expected_name)
                self.assertEqual(receipt_items[0]["sum"], int(float(amount)))

    @override_settings(
        ROBOKASSA={
            **ROBOKASSA_EMPTY_SETTINGS,
            "MERCHANT_LOGIN": "AurumWeb",
            "PASSWORD1": "password-one",
            "PASSWORD2": "password-two",
        }
    )
    def test_quick_consultation_reuses_pending_payment_when_amount_changes(self):
        consultation = QuickConsultation.objects.create(
            telegram_chat_id="999",
            telegram_user_id="999",
            client_email="client@example.test",
            offer_accepted_at=timezone.now(),
            privacy_accepted_at=timezone.now(),
            status=QuickConsultation.Status.WAITING_PAYMENT_AMOUNT,
        )
        invoice, _payment_url = create_quick_consultation_invoice(consultation, "5000.00")
        payment = invoice.payments.get(status=Payment.Status.PENDING)

        invoice, _payment_url = create_quick_consultation_invoice(consultation, "7000.00")

        payment.refresh_from_db()
        self.assertEqual(invoice.payments.count(), 1)
        self.assertEqual(payment.amount, invoice.amount)
        self.assertEqual(str(payment.amount), "7000.00")

    @override_settings(
        DEBUG=True,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
        AURUMWEB_SITE_URL="https://aurumweb.test",
        ROBOKASSA={
            **ROBOKASSA_EMPTY_SETTINGS,
            "MERCHANT_LOGIN": "AurumWeb",
            "PASSWORD1": "password-one",
            "PASSWORD2": "password-two",
        },
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_quick_consultation_reuses_accepted_legal_and_email_for_next_payment(self, urlopen):
        config = QuickConsultationBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_fast_bot"
        config.save()
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 24}})
        accepted_at = timezone.now()
        previous_consultation = QuickConsultation.objects.create(
            telegram_chat_id="999",
            telegram_user_id="999",
            telegram_username="@quick_client",
            client_email="client@example.test",
            offer_accepted_at=accepted_at,
            privacy_accepted_at=accepted_at,
            status=QuickConsultation.Status.PAID,
        )

        response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 45,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "text": "Оплатить консультацию",
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["method"], "sendPhoto")
        self.assertIn("AurumWeb Fast", payload["caption"])
        self.assertNotIn("Публичная оферта", payload["caption"])
        self.assertIn("Оплатить 5 000", json.dumps(payload["reply_markup"], ensure_ascii=False))
        consultation = QuickConsultation.objects.exclude(pk=previous_consultation.pk).get()
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_PAYMENT_AMOUNT)
        self.assertEqual(consultation.client_email, "client@example.test")
        self.assertEqual(consultation.offer_accepted_at, accepted_at)
        self.assertEqual(consultation.privacy_accepted_at, accepted_at)

    @override_settings(
        DEBUG=True,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
        AURUMWEB_SITE_URL="https://aurumweb.test",
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_quick_consultation_custom_amount_button_confirms_amount_input_mode(self, urlopen):
        config = QuickConsultationBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_fast_bot"
        config.save()
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 25}})
        consultation = QuickConsultation.objects.create(
            telegram_chat_id="999",
            telegram_user_id="999",
            telegram_username="@quick_client",
            client_email="client@example.test",
            offer_accepted_at=timezone.now(),
            privacy_accepted_at=timezone.now(),
            status=QuickConsultation.Status.WAITING_PAYMENT_AMOUNT,
        )

        response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 46,
                    "callback_query": {
                        "id": "callback-custom",
                        "data": "quick:custom_amount",
                        "from": {"id": 999, "username": "quick_client", "first_name": "Иван"},
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )

        consultation.refresh_from_db()
        payload = response.json()
        self.assertEqual(payload["method"], "answerCallbackQuery")
        self.assertEqual(payload["text"], "Введите сумму сообщением от 10 до 200 000 ₽.")
        amount_payload = next(
            payload
            for url, payload in self._telegram_payloads(urlopen)
            if "sendPhoto" in url and "Своя сумма" in payload["caption"]
        )
        self.assertEqual(amount_payload["chat_id"], "999")
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_PAYMENT_AMOUNT)

    @override_settings(
        DEBUG=True,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
        AURUMWEB_SITE_URL="https://aurumweb.test",
    )
    def test_quick_consultation_text_start_requires_legal_first(self):
        config = QuickConsultationBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.save()

        response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 47,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"id": 999, "username": "quick_client"},
                        "text": "Проконсультироваться",
                    },
                }
            ),
            content_type="application/json",
        )

        consultation = QuickConsultation.objects.get()
        payload = response.json()
        markup = json.dumps(payload["reply_markup"], ensure_ascii=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["method"], "sendPhoto")
        self.assertIn("Публичная оферта", payload["caption"])
        self.assertIn("Принимаю оферту", markup)
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_OFFER)
        self.assertEqual(consultation.pending_action, QuickConsultation.PendingAction.CONSULTATION)

    @override_settings(
        DEBUG=True,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
        AURUMWEB_SITE_URL="https://aurumweb.test",
    )
    def test_quick_consultation_payment_rejects_invalid_amount(self):
        config = QuickConsultationBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.save()
        QuickConsultation.objects.create(
            telegram_chat_id="999",
            telegram_user_id="999",
            telegram_username="@quick_client",
            status=QuickConsultation.Status.WAITING_PAYMENT_AMOUNT,
        )

        response = self.client.post(
            "/integrations/quick-telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 40,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"id": 999, "username": "quick_client"},
                        "text": "семь тысяч",
                    },
                }
            ),
            content_type="application/json",
        )

        consultation = QuickConsultation.objects.get()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("Не смог распознать сумму", payload["text"])
        self.assertEqual(consultation.status, QuickConsultation.Status.WAITING_PAYMENT_AMOUNT)
        self.assertIsNone(consultation.invoice_id)

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_admin_chat_start_payload_can_link_client_profile(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_bot"
        config.save()
        user = User.objects.create_user(
            username="client",
            email="client@example.test",
            password="StrongPass123!",
        )
        profile = Profile.objects.create(user=user, role=Profile.Role.CLIENT)
        profile.refresh_telegram_link_code()
        profile.save(update_fields=("telegram_link_code", "telegram_link_code_created_at"))
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 16}})

        response = self.client.post(
            "/integrations/telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 32,
                    "message": {
                        "chat": {"id": 777},
                        "from": {"username": "admin_tg"},
                        "text": f"/start {profile.telegram_link_code}",
                    },
                }
            ),
            content_type="application/json",
        )

        profile.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(profile.telegram_chat_id, "777")
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("Telegram подключен", payload["text"])
        self.assertNotIn("AurumWeb Office", payload["text"])

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_admin_callback_button_returns_requested_section(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.save()
        Lead.objects.create(name="Client", email="client@example.test", task="Нужен сайт.")
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 13}})

        response = self.client.post(
            "/integrations/telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 4,
                    "callback_query": {
                        "id": "callback-1",
                        "data": "admin:leads",
                        "message": {"chat": {"id": 777}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payloads = self._telegram_payloads(urlopen)
        self.assertTrue(any("answerCallbackQuery" in url for url, _payload in payloads))
        send_payload = next(payload for url, payload in payloads if "sendMessage" in url)
        self.assertEqual(send_payload["chat_id"], "777")
        self.assertIn("Последние заявки", send_payload["text"])
        self.assertIn("reply_markup", send_payload)
        self.assertIn("Открыть Office", json.dumps(send_payload["reply_markup"], ensure_ascii=False))

    @override_settings(
        DEBUG=True, TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_client_callback_button_returns_client_status(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.save()
        user = User.objects.create_user(
            username="client",
            email="client@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(
            user=user,
            role=Profile.Role.CLIENT,
            telegram_chat_id="999",
            telegram_notifications_enabled=True,
        )
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 14}})

        response = self.client.post(
            "/integrations/telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 5,
                    "callback_query": {
                        "id": "callback-2",
                        "data": "client:status",
                        "message": {"chat": {"id": 999}, "message_id": 1},
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payloads = self._telegram_payloads(urlopen)
        self.assertTrue(any("answerCallbackQuery" in url for url, _payload in payloads))
        send_payload = next(payload for url, payload in payloads if "sendMessage" in url)
        self.assertEqual(send_payload["chat_id"], "999")
        self.assertIn("AurumWeb: Telegram подключен", send_payload["text"])
        self.assertIn("Открыть кабинет", json.dumps(send_payload["reply_markup"], ensure_ascii=False))


class EmailNotificationTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=True,
        AURUMWEB_ADMIN_EMAILS=("admin@example.test",),
        AURUMWEB_SITE_URL="https://aurumweb.test",
    )
    def test_new_lead_sends_admin_email(self):
        response = self.client.post(
            "/api/leads/",
            data=json.dumps(
                {
                    "name": "Client",
                    "email": "client@example.test",
                    "task": "Нужен сайт под ключ.",
                    "personal_data_consent": "on",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Новая заявка", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["admin@example.test"])
        self.assertIn("https://aurumweb.test/office/leads/", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=True,
        AURUMWEB_ADMIN_EMAILS=("admin@example.test",),
        AURUMWEB_SITE_URL="https://aurumweb.test",
    )
    def test_client_message_sends_admin_email(self):
        response = self.client.post(
            "/messenger/api/messages/",
            data=json.dumps(
                {
                    "message": "Здравствуйте, хочу обсудить поддержку сайта.",
                    "title": "Диалог с сайта",
                    "email": "client@example.test",
                    "personal_data_consent": "on",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Новое сообщение клиента", mail.outbox[0].subject)
        self.assertIn("https://aurumweb.test/office/conversations/", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=True,
        AURUMWEB_ADMIN_EMAILS=(),
        AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED=True,
        AURUMWEB_SITE_URL="https://aurumweb.test",
    )
    def test_invoice_issue_can_send_client_email(self):
        lead = Lead.objects.create(
            name="Client",
            email="client@example.test",
            subject="Сайт под ключ",
            service_type="Сайт",
            task="Нужен сайт.",
        )
        invoice = Invoice.objects.create(
            lead=lead,
            client_name=lead.name,
            client_email=lead.email,
            title="Счет по сайту",
            amount="48000.00",
        )

        issue_invoice(invoice)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Счет AurumWeb", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["client@example.test"])
        self.assertIn("https://aurumweb.test/billing/invoices/", mail.outbox[0].body)
        self.assertIn("Оплатить счет можно через Robokassa", mail.outbox[0].body)
        self.assertNotIn("после подключения платежного контура", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=True,
        AURUMWEB_ADMIN_EMAILS=("admin@example.test",),
        AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED=True,
        AURUMWEB_SITE_URL="https://aurumweb.test",
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
    )
    def test_payment_success_client_email_mentions_receipt_without_next_stage(self):
        invoice = Invoice.objects.create(
            title="Консультация",
            client_name="Client",
            client_email="client@example.test",
            amount="5000.00",
            status=Invoice.Status.ISSUED,
        )
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)

        mark_payment_succeeded(payment, {"EMail": "receipt@example.test"}, signature_valid=True)

        client_message = next(message for message in mail.outbox if message.to == ["client@example.test"])
        self.assertIn("Оплата по счету AurumWeb получена.", client_message.body)
        self.assertIn("Статус: оплачен", client_message.body)
        self.assertIn("фискальный чек", client_message.body)
        self.assertIn("email, указанный при оплате", client_message.body)
        self.assertIn("https://aurumweb.test/client/invoices/", client_message.body)
        self.assertNotIn("следующему", client_message.body.lower())


class TelegramBotClientTests(TestCase):
    def _telegram_response(self, payload):
        class Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, traceback):
                return False

            def read(self_inner):
                return json.dumps(payload).encode("utf-8")

        return Response()

    @override_settings(TELEGRAM_BOT_TOKEN="123:test", TELEGRAM_ADMIN_CHAT_ID="42")
    @patch("apps.integrations.telegram.request.urlopen")
    def test_get_me_calls_telegram_api(self, urlopen):
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"username": "aurumweb_bot"}})

        result = TelegramBotClient().get_me()

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["username"], "aurumweb_bot")
        self.assertEqual(TelegramBotSettings.load().bot_username, "aurumweb_bot")

    @override_settings(TELEGRAM_BOT_TOKEN="123:test", TELEGRAM_ADMIN_CHAT_ID="42")
    def test_client_start_url_uses_bot_username_and_payload(self):
        config = TelegramBotSettings.load()
        config.bot_username = "aurumweb_bot"
        config.save()

        url = TelegramBotClient().client_start_url("ABC123")

        self.assertEqual(url, "https://t.me/aurumweb_bot?start=ABC123")

    @override_settings(TELEGRAM_BOT_TOKEN="123:test", TELEGRAM_ADMIN_CHAT_ID="42")
    @patch("apps.integrations.telegram.request.urlopen")
    def test_send_message_uses_configured_chat(self, urlopen):
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 1}})

        self.assertTrue(TelegramBotClient().send_message("test"))
        request_obj = urlopen.call_args.args[0]
        payload = json.loads(request_obj.data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], "42")
        self.assertEqual(payload["text"], "test")

    @override_settings(
        TELEGRAM_BOT_TOKEN="123:test", TELEGRAM_ADMIN_CHAT_ID="42", AURUMWEB_SITE_URL="https://aurumweb.test"
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_send_message_can_attach_inline_keyboard(self, urlopen):
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 1}})
        client = TelegramBotClient()

        self.assertTrue(client.send_message("test", reply_markup=client.admin_keyboard()))
        request_obj = urlopen.call_args.args[0]
        payload = json.loads(request_obj.data.decode("utf-8"))
        self.assertIn("reply_markup", payload)
        self.assertIn("Открыть Office", json.dumps(payload["reply_markup"], ensure_ascii=False))

    @override_settings(TELEGRAM_BOT_TOKEN="123:test", TELEGRAM_ADMIN_CHAT_ID="")
    def test_send_message_without_chat_id_does_not_call_api(self):
        with patch("apps.integrations.telegram.request.urlopen") as urlopen:
            self.assertFalse(TelegramBotClient().send_message("test"))
        urlopen.assert_not_called()

    @override_settings(
        TELEGRAM_BOT_TOKEN="123:test",
        TELEGRAM_ADMIN_CHAT_ID="42",
        AURUMWEB_SITE_URL="https://aurumweb.test",
        TELEGRAM_WEBHOOK_SECRET="secret",
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_set_webhook_uses_site_url_and_secret(self, urlopen):
        urlopen.return_value = self._telegram_response({"ok": True, "result": True})

        result = TelegramBotClient().set_webhook()

        self.assertTrue(result["ok"])
        request_obj = urlopen.call_args.args[0]
        payload = json.loads(request_obj.data.decode("utf-8"))
        self.assertEqual(payload["url"], "https://aurumweb.test/integrations/telegram/webhook/")
        self.assertEqual(payload["secret_token"], "secret")

    @override_settings(TELEGRAM_BOT_TOKEN="123:test", TELEGRAM_ADMIN_CHAT_ID="42")
    @patch("apps.integrations.telegram.request.urlopen")
    def test_management_command_check(self, urlopen):
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"username": "aurumweb_bot"}})

        call_command("telegram_bot", "check")

    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="")
    @patch("apps.integrations.telegram.request.urlopen")
    def test_client_loads_database_settings(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.save()
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 1}})

        self.assertTrue(TelegramBotClient().send_message("hello"))
        request_obj = urlopen.call_args.args[0]
        payload = json.loads(request_obj.data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], "777")

    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test")
    @patch("apps.integrations.telegram.request.urlopen")
    def test_notify_client_message_sends_office_link(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.save()
        conversation = Conversation.objects.create(title="Диалог", client_email="client@example.test")
        message = Message.objects.create(
            conversation=conversation,
            author_role=Message.AuthorRole.CLIENT,
            body="Нужно обсудить сайт.",
        )
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 2}})

        self.assertTrue(TelegramBotClient().notify_client_message(conversation, message))
        request_obj = urlopen.call_args.args[0]
        payload = json.loads(request_obj.data.decode("utf-8"))
        self.assertIn("Новое сообщение клиента", payload["text"])
        self.assertIn(f"https://aurumweb.test/office/conversations/{conversation.pk}/", payload["text"])

    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test")
    @patch("apps.integrations.telegram.request.urlopen")
    def test_mark_payment_succeeded_notifies_telegram_once(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.save()
        invoice = Invoice.objects.create(
            title="Счет по сайту",
            client_name="Client",
            client_email="client@example.test",
            amount="48000.00",
            status=Invoice.Status.ISSUED,
        )
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 3}})

        mark_payment_succeeded(payment, {"OutSum": "48000.00"}, signature_valid=True)
        mark_payment_succeeded(payment, {"OutSum": "48000.00"}, signature_valid=True)

        self.assertEqual(urlopen.call_count, 1)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("Оплата получена", payload["text"])

    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test")
    @patch("apps.integrations.telegram.request.urlopen")
    def test_client_invoice_issued_telegram_has_direct_payment_link(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.save()
        user = User.objects.create_user(
            username="client",
            email="client@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(
            user=user,
            role=Profile.Role.CLIENT,
            telegram_chat_id="999",
            telegram_notifications_enabled=True,
        )
        invoice = Invoice.objects.create(
            user=user,
            title="Визитка",
            client_name="Client",
            client_email="client@example.test",
            amount="10000.00",
        )
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 5}})

        self.assertTrue(TelegramBotClient().notify_client_invoice_issued(invoice))

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], "999")
        self.assertIn("Оплатить:", payload["text"])
        self.assertIn(f"https://aurumweb.test/billing/invoices/{invoice.public_token}/pay/", payload["text"])
        markup = json.dumps(payload["reply_markup"], ensure_ascii=False)
        self.assertIn("Оплатить счет", markup)
        self.assertIn(f"/billing/invoices/{invoice.public_token}/pay/", markup)

    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="", AURUMWEB_SITE_URL="https://aurumweb.test")
    @patch("apps.integrations.telegram.request.urlopen")
    def test_mark_payment_succeeded_notifies_client_with_receipt_note(self, urlopen):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.save()
        user = User.objects.create_user(
            username="client",
            email="client@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(
            user=user,
            role=Profile.Role.CLIENT,
            telegram_chat_id="999",
            telegram_notifications_enabled=True,
        )
        invoice = Invoice.objects.create(
            user=user,
            title="Консультация",
            client_name="Client",
            client_email="client@example.test",
            amount="5000.00",
            status=Invoice.Status.ISSUED,
        )
        payment = Payment.objects.create(invoice=invoice, amount=invoice.amount)
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 4}})

        mark_payment_succeeded(payment, {"EMail": "receipt@example.test"}, signature_valid=True)

        payloads = [json.loads(call.args[0].data.decode("utf-8")) for call in urlopen.call_args_list]
        client_payload = next(payload for payload in payloads if payload["chat_id"] == "999")
        self.assertIn("Оплата счета AurumWeb подтверждена", client_payload["text"])
        self.assertIn("Статус: оплачен", client_payload["text"])
        self.assertIn("фискальный чек", client_payload["text"])
        self.assertIn("email, указанный при оплате", client_payload["text"])
        self.assertNotIn("следующ", client_payload["text"].lower())

    @override_settings(
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
        AURUMWEB_SITE_URL="https://aurumweb.test",
        ROBOKASSA={
            **ROBOKASSA_EMPTY_SETTINGS,
            "MERCHANT_LOGIN": "",
            "PASSWORD1": "",
            "PASSWORD2": "",
        },
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_quick_consultation_invoice_and_payment_use_direct_telegram_chat(self, urlopen):
        telegram_config = QuickConsultationBotSettings.load()
        telegram_config.is_enabled = True
        telegram_config.set_bot_token("123:test")
        telegram_config.admin_chat_id = "777"
        telegram_config.save()
        robokassa_config = RobokassaSettings.load()
        robokassa_config.is_enabled = True
        robokassa_config.merchant_login = "AurumWeb"
        robokassa_config.set_password1("password-one")
        robokassa_config.set_password2("password-two")
        robokassa_config.test_mode = False
        robokassa_config.receipt_enabled = True
        robokassa_config.save()
        consultation = QuickConsultation.objects.create(
            telegram_chat_id="999",
            telegram_user_id="999",
            telegram_username="@quick_client",
            client_name="Quick Client",
            question="Нужна консультация по сайту.",
            status=QuickConsultation.Status.NEW,
        )
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 20}})

        from apps.office.services import invoice_quick_consultation

        invoice, _payment_url = invoice_quick_consultation(
            consultation,
            {
                "quoted_amount": "5000.00",
                "manager_response": "Разберем структуру и следующий шаг.",
                "internal_note": "Тест быстрой консультации.",
            },
        )
        payment = invoice.payments.get()
        mark_payment_succeeded(payment, {"OutSum": "5000.00"}, signature_valid=True)

        consultation.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(consultation.invoice_id, invoice.pk)
        self.assertEqual(consultation.status, QuickConsultation.Status.PAID)
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        item = invoice.items.get()
        self.assertEqual(item.name, "Консультация по поддержке Telegram-бота")
        self.assertEqual(str(item.unit_price), "5000.00")
        receipt_items = RobokassaClient().receipt_payload(invoice)["items"]
        self.assertEqual(len(receipt_items), 1)
        self.assertEqual(receipt_items[0]["name"], "Консультация по поддержке Telegram-бота")
        self.assertEqual(receipt_items[0]["sum"], 5000)
        payloads = []
        photo_bodies = []
        for call in urlopen.call_args_list:
            request_obj = call.args[0]
            content_type = request_obj.get_header("Content-type") or request_obj.get_header("Content-Type") or ""
            if "multipart/form-data" in content_type:
                photo_bodies.append(request_obj.data.decode("utf-8", errors="ignore"))
            elif request_obj.data:
                payloads.append(json.loads(request_obj.data.decode("utf-8")))
        quick_payment_payload = next(
            payload
            for payload in payloads
            if payload["chat_id"] == "999" and "Счет за услугу AurumWeb" in payload["text"]
        )
        self.assertIn("Оплатить услугу", json.dumps(quick_payment_payload["reply_markup"], ensure_ascii=False))
        self.assertTrue(
            any("Оплата прошла успешно" in body and "aurumweb-payment-receipt.png" in body for body in photo_bodies)
        )
        self.assertFalse(any("Счет оплачен" in body or "Фискальный чек" in body for body in photo_bodies))
